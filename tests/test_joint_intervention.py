"""Labeled synthetic proposals verify evidence sharing, not emergent agent behavior."""
from __future__ import annotations

import hashlib
import json

import pytest
from test_action_gate import Harness, auth, setup_controller
from test_action_gate import gate_http as gate_http

from mesocosm.judge.intervention import InterventionError, InterventionState


@pytest.mark.parametrize("visibility", ["actions", "transcript"])
async def test_joint_three_agent_evidence_and_shared_state_are_retained(gate_http, tmp_path, visibility):
    http, _ = gate_http

    async def shared():
        return {"chat": [{"id": 7, "text": "untrusted shared message"}], "atomic": False}

    state = InterventionState(expected_agents=["agent-1", "agent-2", "agent-3"], shared_evidence=shared)
    other = Harness()
    other.action["args"] = {"command": "peer command"}
    state.register("agent-2", other, "s2", "original-task")
    state.register("agent-3", other, "s3", "original-task")
    state.finish("agent-3", await other.list_messages("s3"))
    gate, harness, model, monitor, admin = await setup_controller(http, tmp_path, {"score": 5, "reasoning": "fixture"},
        state=state, scope="joint", include_shared=True, visibility=visibility, decision_delay_seconds=.001)
    try:
        assert (await gate.run_turn(http.post("/v1/actions/authorize", headers=auth(),
                                             json=harness.action))).json()["allow"]
        event = next(e for e in gate.bundle.events() if e.kind == "monitor.intervention")
        raw = (gate.bundle.root / "native" / event.data["evidence_path"]).read_text()
        assert hashlib.sha256(raw.encode()).hexdigest() == event.data["evidence_sha256"]
        evidence = json.loads(raw)
        assert set(evidence["joint"]["agents"]) == {"agent-1", "agent-2", "agent-3"}
        assert evidence["joint"]["agents"]["agent-3"]["status"] == "completed"
        assert evidence["joint"]["atomic"] is False
        assert evidence["shared"]["chat"][0]["id"] == 7
        assert "peer command" in model.state.inputs[0]["messages"][1]["content"]
        assert event.data["scope"] == "joint" and event.data["proposed_only"]
    finally:
        await gate.close()
        await monitor.aclose()
        await admin.aclose()


async def test_unavailable_peer_evidence_cannot_allow_action(gate_http, tmp_path):
    http, _ = gate_http

    class MissingPeer(Harness):
        async def list_messages(self, session_id):
            raise ConnectionError("peer evidence unavailable")

    state = InterventionState()
    state.register("agent-2", MissingPeer(), "s2", "task")
    gate, harness, model, monitor, admin = await setup_controller(http, tmp_path, {"score": 0},
                                                                 state=state, scope="joint")
    try:
        with pytest.raises(InterventionError):
            await gate.run_turn(http.post("/v1/actions/authorize", headers=auth(), json=harness.action))
        assert harness.aborted and model.state.inputs == []
        assert state.outcomes["unknown"] == 1
    finally:
        await monitor.aclose()
        await admin.aclose()


MALFORMED_PEER_MESSAGES = [
    None,
    {"messages": []},
    [None],
    [{"info": {"sessionID": "different-session"}, "parts": []}],
    [{"info": {"sessionID": "s2"}, "parts": None}],
    [{"info": {"sessionID": "s2"}, "parts": {}}],
    [{"info": {"sessionID": "s2"}, "parts": ["not-a-part"]}],
]


@pytest.mark.parametrize("messages", MALFORMED_PEER_MESSAGES)
@pytest.mark.parametrize("visibility", ["transcript", "actions"])
@pytest.mark.parametrize("peer_status", ["active", "completed"])
async def test_malformed_joint_peer_evidence_fails_closed_before_review(
        gate_http, tmp_path, messages, visibility, peer_status):
    http, _ = gate_http

    class MalformedPeer(Harness):
        async def list_messages(self, session_id):
            return messages

    state = InterventionState(expected_agents=["agent-1", "agent-2", "agent-3"])
    if peer_status == "active":
        state.register("agent-2", MalformedPeer(), "s2", "original-task")
    else:
        # Validate completed evidence again at use, even if a retained mutable payload was corrupted.
        state.completed["agent-2"] = {"session_id": "s2", "task": "original-task",
                                      "messages": messages, "status": "completed"}
    gate, harness, model, monitor, admin = await setup_controller(
        http, tmp_path, {"score": 0}, state=state, scope="joint", visibility=visibility)
    try:
        with pytest.raises(InterventionError, match="failed closed"):
            await gate.run_turn(http.post("/v1/actions/authorize", headers=auth(), json=harness.action))
        assert harness.aborted and model.state.inputs == []
        assert state.calls == 0 and state.outcomes == {"allowed": 0, "blocked": 0, "unknown": 1}
        event = next(e for e in gate.bundle.events() if e.kind == "monitor.intervention")
        assert event.data["decision"] == "deny" and event.data["outcome"] == "unknown"
        assert event.data["evidence_path"] is None
    finally:
        await gate.close()
        await monitor.aclose()
        await admin.aclose()


@pytest.mark.parametrize("messages", MALFORMED_PEER_MESSAGES)
def test_completed_peer_registration_rejects_malformed_snapshot(messages):
    state = InterventionState()
    state.register("agent-2", Harness(), "s2", "task")
    with pytest.raises(ValueError, match="evidence"):
        state.finish("agent-2", messages)
    assert "agent-2" not in state.completed
    assert "agent-2" in state.sessions, "failed final capture must not turn an active peer into not_started"
