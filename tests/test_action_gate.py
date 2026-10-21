"""Before-tool plumbing tests use explicit synthetic proposals/reviewer answers, not behavioral evidence."""
import asyncio
import hashlib
import json

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from mesocosm.adapters.opencode.action_plugin import action_plugin_digest, render_action_plugin
from mesocosm.adapters.opencode.client import OpenCodeClient
from mesocosm.core.manifest import MonitorSpec
from mesocosm.core.records import RunBundle
from mesocosm.gateway.action_client import ActionAdmin, ActionGateFailure
from mesocosm.gateway.actions import register_action_routes
from mesocosm.judge.intervention import (
    InterventionError,
    InterventionGate,
    InterventionState,
    InterventionTerminated,
    proposed_tool_matches,
)
from mesocosm.judge.monitor import MonitorModel

DIGEST = "a" * 64


@pytest.fixture
async def gate_http():
    app = FastAPI()
    logs = []

    def identity(request):
        key = request.headers.get("authorization", "").removeprefix("Bearer ")
        return (key, "run", "key") if key in {"agent-1", "agent-2", "agent-3"} else ("unknown", "unknown", "none")

    register_action_routes(app, identity_callback=identity, log_callback=logs.append, control_key="controller")
    async with httpx.AsyncClient(base_url="http://gate", transport=httpx.ASGITransport(app=app)) as http:
        yield http, logs


def auth(agent="agent-1"):
    return {"Authorization": "Bearer " + agent}


async def binding(http, agent="agent-1", session="s1", timeout=1.0, instance="i1"):
    ready = await http.post("/v1/actions/ready", headers=auth(agent), json={
        "instance_id": instance, "plugin_sha256": DIGEST, "protocol": "tool-gate-v1"})
    assert ready.status_code == 200
    response = await http.post("/admin/actions/bind", headers=auth("controller"), json={
        "run": "run", "agent_id": agent, "session_id": session, "task": "original-task",
        "plugin_sha256": DIGEST, "timeout_seconds": timeout})
    assert response.status_code == 200
    return response.json()


def proposal(session="s1", call="call1", instance="i1"):
    return {"instance_id": instance, "nonce": "nonce", "session_id": session, "call_id": call,
            "tool": "bash", "args": {"command": "printf fixture", "description": "plumbing fixture"}}


async def get_pending(http, bound):
    for _ in range(100):
        result = await http.get("/admin/actions", headers=auth("controller"),
                                params={"binding_id": bound["binding_id"]})
        if result.json()["pending"]:
            return result.json()["pending"][0]
        await asyncio.sleep(.001)
    raise AssertionError("proposal did not become pending")


async def test_three_identities_hold_until_single_use_decision(gate_http):
    http, logs = gate_http
    for i in range(1, 4):
        agent, session = f"agent-{i}", f"s{i}"
        bound = await binding(http, agent, session)
        action = proposal(session)
        pending = asyncio.create_task(http.post("/v1/actions/authorize", headers=auth(agent), json=action))
        item = await get_pending(http, bound)
        assert not pending.done(), "HTTP request remains held before the controller decides"
        assert (item["agent"], item["run"], item["task"], item["session_id"]) == (agent, "run", "original-task", session)
        decision = {"binding_id": bound["binding_id"], "decision": "allow" if i == 1 else "deny", "reason": "test"}
        # An agent cannot read or decide actions, including another agent's.
        assert (await http.get("/admin/actions", headers=auth(agent),
                               params={"binding_id": bound["binding_id"]})).status_code == 404
        assert (await http.post(f"/admin/actions/{item['action_id']}/decision", headers=auth(agent),
                                json=decision)).status_code == 404
        assert (await http.post(f"/admin/actions/{item['action_id']}/decision", headers=auth("controller"),
                                json={**decision, "binding_id": "wrong"})).status_code == 409
        assert (await http.post(f"/admin/actions/{item['action_id']}/decision", headers=auth("controller"),
                                json=decision)).status_code == 200
        response = (await pending).json()
        assert response["allow"] is (i == 1)
        assert response["nonce"] == "nonce" and response["call_id"] == "call1"
        assert (await http.post(f"/admin/actions/{item['action_id']}/decision", headers=auth("controller"),
                                json=decision)).status_code == 409
        assert (await http.post("/v1/actions/authorize", headers=auth(agent), json=action)).status_code == 409
    assert len([row for row in logs if row["event"] == "action.held"]) == 3


async def test_unknown_stale_restarted_and_expired_requests_fail_closed(gate_http):
    http, logs = gate_http
    assert (await http.post("/v1/actions/authorize", headers=auth("unknown"), json=proposal())).status_code == 401
    assert (await http.post("/v1/actions/authorize", headers=auth(), json=proposal())).status_code == 403
    bound = await binding(http, timeout=.02)
    response = await http.post("/v1/actions/authorize", headers=auth(), json=proposal())
    assert response.json()["allow"] is False and response.json()["reason"] == "approval_timeout"
    for _ in range(2):
        status = (await http.get("/admin/actions", headers=auth("controller"),
                                params={"binding_id": bound["binding_id"]})).json()
        assert status["pending"] == [] and status["active"] is True
        assert status["failures"][0]["reason"] == "approval_timeout"
        assert status["failures"][0]["source"] == "gateway"
    assert (await http.post("/v1/actions/authorize", headers=auth("agent-2"), json=proposal(call="c2"))).status_code == 403
    pending = asyncio.create_task(http.post("/v1/actions/authorize", headers=auth(), json=proposal(call="c2")))
    await get_pending(http, bound)
    await http.post("/v1/actions/ready", headers=auth(), json={
        "instance_id": "new-instance", "plugin_sha256": DIGEST, "protocol": "tool-gate-v1"})
    assert (await pending).json()["reason"] == "harness_instance_replaced"
    status = (await http.get("/admin/actions", headers=auth("controller"),
                            params={"binding_id": bound["binding_id"]})).json()
    assert status["active"] is False and len(status["failures"]) == 2
    assert (await http.post("/v1/actions/authorize", headers=auth(), json=proposal(call="c3"))).status_code == 403
    bad = await http.post("/admin/actions/bind", headers=auth("controller"), json={
        "run": "run", "agent_id": "agent-1", "session_id": "s2", "task": "t",
        "plugin_sha256": "b" * 64})
    assert bad.status_code == 409
    assert any(r.get("reason") == "approval_timeout" for r in logs)


class Harness:
    def __init__(self):
        self.action = proposal()
        self.aborted = False

    async def prepare_action_hook(self):
        return {"version": "1.19.3", "tool_ids": ["bash"], "hook": "tool.execute.before"}

    async def list_messages(self, session_id):
        return [{"info": {"role": "assistant", "sessionID": session_id}, "parts": [{
            "type": "tool", "tool": self.action["tool"], "callID": self.action["call_id"],
            "state": {"status": "running", "input": self.action["args"]}}]}]

    async def abort_session(self, session_id):
        self.aborted = True
        return True


async def setup_controller(http, tmp_path, result, *, state=None, **options):
    await http.post("/v1/actions/ready", headers=auth(), json={
        "instance_id": "i1", "plugin_sha256": DIGEST, "protocol": "tool-gate-v1"})
    model = FastAPI()
    model.state.inputs = []

    @model.post("/v1/chat/completions")
    async def chat(request: Request):
        model.state.inputs.append(await request.json())
        if result == "timeout":
            await asyncio.sleep(.2)
        if result == "transport":
            return JSONResponse({"error": "unavailable"}, status_code=503)
        return {"choices": [{"message": {"content": json.dumps(result)}}]}

    monitor = MonitorModel("http://monitor/v1", "reviewer", transport=httpx.ASGITransport(app=model))
    admin_client = httpx.AsyncClient(base_url="http://gate", transport=http._transport, headers=auth("controller"))
    bundle = RunBundle.create(tmp_path)
    harness = Harness()
    spec = MonitorSpec(enabled=True, timing="pre_action", poll_seconds=.001,
                       decision_timeout_seconds=.05, **options)
    gate = InterventionGate(admin=ActionAdmin(admin_client), monitor=monitor, spec=spec, bundle=bundle,
        run_label="run", agent_id="agent-1", session_id="s1", task="original-task", harness=harness,
        plugin_sha256=DIGEST, state=state or InterventionState())
    await gate.prepare()
    return gate, harness, model, monitor, admin_client


@pytest.mark.parametrize(("score", "outcome", "allowed"), [(5, "allowed", True), (90, "blocked", False)])
async def test_controller_model_decides_and_preserves_rubric_receipts(gate_http, tmp_path, score, outcome, allowed):
    http, _ = gate_http
    gate, harness, model, monitor, admin = await setup_controller(http, tmp_path,
        {"score": score, "reasoning": "explicit test judgement"}, rubric="Fixture policy.", rubric_version="fixture-v1")
    result = await gate.run_turn(http.post("/v1/actions/authorize", headers=auth(), json=harness.action))
    assert result.json()["allow"] is allowed
    assert harness.aborted is False, "deny blocks this request, not the entire session"
    assert gate.state.summary() == {"timing": "pre_action", "calls": 1,
                                  "allowed": int(allowed), "blocked": int(not allowed), "unknown": 0}
    event = next(e for e in gate.bundle.events() if e.kind == "monitor.intervention")
    assert event.data["outcome"] == outcome and event.data["rubric_version"] == "fixture-v1"
    assert event.data["rubric_sha256"] == hashlib.sha256(b"Fixture policy.").hexdigest()
    assert event.data["proposed_only"] is True
    sent = model.state.inputs[0]["messages"]
    assert sent[0]["content"].startswith("Fixture policy.")
    assert "exact proposed tool action would violate" in sent[0]["content"]
    assert sent[1]["content"].startswith("PROPOSED ACTION:\n\n")
    assert event.data["assessment_phase"] == "pre_action"
    assert event.data["prompt_format_version"] == "monitor-pre_action-v1"
    prompt_hash = hashlib.sha256(sent[0]["content"].encode()).hexdigest()
    assert event.data["system_prompt_sha256"] == prompt_hash != event.data["rubric_sha256"]
    assert event.data["assessment"]["system_prompt_sha256"] == prompt_hash
    receipt = json.loads((gate.bundle.root / "receipts.jsonl").read_text().splitlines()[-1])
    assert receipt["verification"]["detail"]["system_prompt_sha256"] == prompt_hash
    await gate.close()
    await monitor.aclose()
    await admin.aclose()


@pytest.mark.parametrize("failure", ["malformed", "timeout", "transport", "attribution", "budget", "oversize", "nested", "terminate"])
async def test_unknowns_and_terminate_deny_and_abort_actual_session(gate_http, tmp_path, failure):
    http, logs = gate_http
    options = {"on_flag": "terminate"} if failure == "terminate" else {}
    if failure == "oversize":
        options["max_input_chars"] = 100
    if failure == "budget":
        options["max_calls"] = 1
    state = InterventionState(calls=1 if failure == "budget" else 0)
    result = {"score": 95, "reasoning": "test"} if failure == "terminate" else {"score": 5, "reasoning": "test"}
    if failure in {"timeout", "transport"}:
        result = failure
    if failure == "malformed":
        result = {"score": True, "reasoning": "invalid"}
    gate, harness, model, monitor, admin = await setup_controller(http, tmp_path, result, state=state, **options)
    if failure == "nested":
        harness.action["tool"] = "task"
    request = dict(harness.action)
    if failure == "attribution":
        request["args"] = {"command": "different"}
    with pytest.raises(InterventionTerminated if failure == "terminate" else InterventionError):
        await gate.run_turn(http.post("/v1/actions/authorize", headers=auth(), json=request))
    assert harness.aborted is True and gate.binding is None
    assert any(r.get("decision") == "deny" for r in logs)
    assert (await http.post("/v1/actions/authorize", headers=auth(), json=proposal(call="later"))).status_code == 403
    if failure in {"budget", "attribution", "oversize", "nested"}:
        assert model.state.inputs == []
    await monitor.aclose()
    await admin.aclose()


@pytest.mark.parametrize("cancelled", [False, True])
@pytest.mark.parametrize("record_failed", [False, True])
async def test_gate_cleanup_failure_preserves_original_failure_or_cancellation(gate_http, tmp_path,
                                                                            monkeypatch, cancelled, record_failed):
    http, _ = gate_http
    gate, harness, _, monitor, admin = await setup_controller(http, tmp_path, {"score": 5})
    original = asyncio.CancelledError("original cancellation") if cancelled else InterventionError("original failure")

    async def fail_unbind(binding_id):
        raise RuntimeError("unbind transport failed")

    async def fail_turn():
        raise original

    monkeypatch.setattr(gate.admin, "unbind", fail_unbind)
    if record_failed:
        record = gate.bundle.record

        def fail_cleanup_record(kind, **data):
            if kind == "monitor.cleanup_failed":
                raise OSError("event volume is full")
            return record(kind, **data)

        monkeypatch.setattr(gate.bundle, "record", fail_cleanup_record)
    try:
        with pytest.raises(type(original)) as error:
            await gate.run_turn(fail_turn())
        assert error.value is original
        assert harness.aborted is True and gate.binding is None
        cleanup = [event for event in gate.bundle.events() if event.kind == "monitor.cleanup_failed"]
        if record_failed:
            assert not cleanup and any("event volume is full" in note for note in original.__notes__)
        else:
            assert len(cleanup) == 1 and cleanup[0].data["phase"] == "gate_close"
            assert "unbind transport failed" in cleanup[0].data["error"]
        assert "unbind transport failed" in original.__notes__[0]
    finally:
        await monitor.aclose()
        await admin.aclose()


async def test_expiry_between_polls_fails_after_harness_turn_already_completed(gate_http, tmp_path):
    """Use accepted production settings: the gate's five-second grace is shorter than this poll."""
    http, logs = gate_http
    gate, harness, model, monitor, admin = await setup_controller(http, tmp_path, {"score": 5})
    gate.spec.poll_seconds = 10
    response = None

    async def finish_after_denial():
        nonlocal response
        response = await http.post("/v1/actions/authorize", headers=auth(), json=harness.action)
        return "harness completed"

    try:
        with pytest.raises(InterventionError, match="automatic failures"):
            await gate.run_turn(finish_after_denial())
        assert response is not None and response.json()["reason"] == "approval_timeout"
        assert harness.aborted and gate.binding is None and not model.state.inputs
        assert gate.state.summary() == {"timing": "pre_action", "calls": 0,
                                       "allowed": 0, "blocked": 0, "unknown": 1}
        failure = next(event for event in gate.bundle.events() if event.kind == "monitor.gate_failed")
        assert failure.data["turn_completed"] is True
        original = next(row for row in logs if row.get("reason") == "approval_timeout")
        assert failure.data["failures"][0]["action_id"] == original["action_id"]
        receipts = [json.loads(line) for line in (gate.bundle.root / "receipts.jsonl").read_text().splitlines()]
        assert receipts[-1]["op"] == "monitor.gate.status" and receipts[-1]["result"] == "failed"
        assert receipts[-1]["verification"]["detail"]["failures"] == failure.data["failures"]
    finally:
        await monitor.aclose()
        await admin.aclose()


async def test_expired_action_fails_while_harness_turn_continues(gate_http, tmp_path):
    http, _ = gate_http
    gate, harness, _, monitor, admin = await setup_controller(http, tmp_path, {"score": 5})
    # The same gateway timeout path with a short test binding, to exercise the live poll.
    await gate.close()
    gate.binding = await binding(http, timeout=.005)
    gate.spec.poll_seconds = .03

    async def continue_after_denial():
        response = await http.post("/v1/actions/authorize", headers=auth(), json=harness.action)
        assert response.json()["reason"] == "approval_timeout"
        await asyncio.sleep(100)

    try:
        with pytest.raises(InterventionError, match="automatic failures"):
            await gate.run_turn(continue_after_denial())
        failure = next(event for event in gate.bundle.events() if event.kind == "monitor.gate_failed")
        assert failure.data["turn_completed"] is False
        assert harness.aborted and gate.state.outcomes["unknown"] == 1
    finally:
        await monitor.aclose()
        await admin.aclose()


async def test_completed_turn_with_unresolved_action_is_not_assessed_or_graded(gate_http, tmp_path):
    http, _ = gate_http
    gate, harness, model, monitor, admin = await setup_controller(http, tmp_path, {"score": 5})
    gate.spec.poll_seconds = .03
    request = None

    async def finish_with_outstanding_request():
        nonlocal request
        request = asyncio.create_task(http.post("/v1/actions/authorize", headers=auth(), json=harness.action))
        await get_pending(http, gate.binding)
        return "harness completed"

    try:
        with pytest.raises(InterventionError, match="unresolved tool actions"):
            await gate.run_turn(finish_with_outstanding_request())
        assert request is not None and (await request).json()["reason"] == "session_closed"
        assert not model.state.inputs and harness.aborted and gate.state.outcomes["unknown"] == 1
    finally:
        await monitor.aclose()
        await admin.aclose()


@pytest.mark.parametrize(("failure", "status", "reason"), [
    ("oversize", 413, "action_input_exceeds_limit"),
    ("replay", 409, "action_replayed"),
    ("stale_instance", 403, "unbound_or_stale_session"),
])
async def test_bound_early_rejections_fail_turn_even_without_pending_actions(gate_http, tmp_path,
                                                                         failure, status, reason):
    http, logs = gate_http
    gate, harness, _, monitor, admin = await setup_controller(http, tmp_path,
                                                            {"score": 5, "reasoning": "fixture judgement"})
    request = dict(harness.action)
    if failure == "oversize":
        request["args"] = {"command": "x" * 1_000_001}
    elif failure == "stale_instance":
        request["instance_id"] = "old-instance"
    else:
        # A controller-approved first use is valid; replaying it must still fail the next turn.
        first = await gate.run_turn(http.post("/v1/actions/authorize", headers=auth(), json=request))
        assert first.json()["allow"] is True

    async def finish_after_rejection():
        response = await http.post("/v1/actions/authorize", headers=auth(), json=request)
        assert response.status_code == status
        return "harness completed"

    try:
        with pytest.raises(InterventionError, match="automatic failures"):
            await gate.run_turn(finish_after_rejection())
        assert harness.aborted and gate.state.outcomes["unknown"] == 1
        event = next(event for event in gate.bundle.events() if event.kind == "monitor.gate_failed")
        rejected = next(row for row in logs if row.get("reason") == reason)
        recorded = event.data["failures"][0]
        assert recorded["reason"] == reason and recorded["action_id"] == rejected["action_id"]
        encoded = json.dumps(request["args"], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        assert recorded["args_sha256"] == hashlib.sha256(encoded).hexdigest()
        assert recorded["args_bytes"] == len(encoded) and "args" not in recorded
    finally:
        await monitor.aclose()
        await admin.aclose()


async def test_shared_budget_is_atomic_across_agents_and_tasks():
    state = InterventionState()
    results = await asyncio.gather(*(state.reserve(2) for _ in range(10)))
    assert sorted(x for x in results if x is not None) == [1, 2]
    assert results.count(None) == 8 and state.summary()["calls"] == 2


async def test_unsupported_harness_version_is_rejected_before_tool_discovery():
    seen = []

    async def handle(request):
        seen.append(request.url.path)
        return httpx.Response(200, json={"healthy": True, "version": "2.0.0"})

    client = OpenCodeClient("http://harness", transport=httpx.MockTransport(handle))
    with pytest.raises(ValueError, match="rejected_unsupported"):
        await client.prepare_action_hook()
    assert seen == ["/global/health"]
    await client.aclose()


async def test_harness_match_requires_exact_session_call_tool_and_arguments():
    harness = Harness()
    messages = await harness.list_messages("s1")
    assert proposed_tool_matches(messages, harness.action)
    for key, val in [("session_id", "s2"), ("call_id", "c2"), ("tool", "write"), ("args", {})]:
        assert not proposed_tool_matches(messages, {**harness.action, key: val})
    messages[0]["parts"][0]["state"]["status"] = "completed"
    assert not proposed_tool_matches(messages, harness.action)


def test_rendered_plugin_digest_covers_exact_bytes_and_timeout():
    plugin = render_action_plugin("http://gateway", "agent-key", 20)
    assert action_plugin_digest("http://gateway", "agent-key", 20) == hashlib.sha256(plugin.encode()).hexdigest()
    assert '"tool.execute.before"' in plugin and "AbortSignal.timeout" in plugin
    with pytest.raises(ValueError):
        render_action_plugin("http://gateway", "key", 0)


async def test_unbind_denies_outstanding_actions_and_cancellation_drains_requests(gate_http):
    http, logs = gate_http
    bound = await binding(http)
    pending = asyncio.create_task(http.post("/v1/actions/authorize", headers=auth(), json=proposal()))
    await get_pending(http, bound)
    response = await http.delete("/admin/actions/bind/" + bound["binding_id"], headers=auth("controller"))
    assert response.json()["closed"] is True
    assert (await pending).json()["reason"] == "session_closed"
    bound = await binding(http, session="s2")
    pending = asyncio.create_task(http.post("/v1/actions/authorize", headers=auth(), json=proposal(session="s2")))
    await get_pending(http, bound)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    result = await http.get("/admin/actions", headers=auth("controller"), params={"binding_id": bound["binding_id"]})
    assert result.json()["pending"] == []
    assert result.json()["failures"][0]["reason"] == "request_cancelled"
    assert any(r.get("reason") == "request_cancelled" and r["decision"] == "deny" for r in logs)


async def test_admin_rejects_malformed_decision_acknowledgement():
    async def handle(request):
        return httpx.Response(200, json={"action_id": "wrong", "binding_id": "b1", "decision": "allow"})

    async with httpx.AsyncClient(base_url="http://gate", transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ValueError, match="acknowledgement"):
            await ActionAdmin(client).decide("a1", "b1", "allow", "test")


@pytest.mark.parametrize("status", [
    {"pending": []},
    {"binding_id": "b1", "active": True, "pending": [], "failures": []},
    {"binding_id": "b1", "active": False, "pending": [], "failures": []},
])
async def test_admin_status_requires_current_binding_and_preserves_inactive_failure(status):
    async def handle(request):
        return httpx.Response(200, json=status)

    async with httpx.AsyncClient(base_url="http://gate", transport=httpx.MockTransport(handle)) as client:
        if status.get("active") is False:
            with pytest.raises(ActionGateFailure):
                await ActionAdmin(client).pending("b1")
        else:
            with pytest.raises(ValueError, match="status"):
                await ActionAdmin(client).pending("other-binding")


async def test_pending_transport_failure_aborts_session(gate_http, tmp_path):
    http, _ = gate_http
    gate, harness, _, monitor, client = await setup_controller(http, tmp_path, {"score": 5, "reasoning": "test"})

    async def broken_pending(binding_id):
        raise httpx.ConnectError("controller transport unavailable")

    gate.admin.pending = broken_pending
    with pytest.raises(InterventionError, match="failed closed"):
        await gate.run_turn(asyncio.sleep(100))
    assert harness.aborted and gate.binding is None
    assert gate.state.outcomes["unknown"] == 1
    assert any(e.kind == "monitor.gate_failed" for e in gate.bundle.events())
    await monitor.aclose()
    await client.aclose()


async def test_unsupported_prepare_records_rejection_and_sends_no_turn(gate_http, tmp_path):
    http, _ = gate_http
    gate, harness, _, monitor, admin = await setup_controller(http, tmp_path, {"score": 5, "reasoning": "test"})
    await gate.close()

    async def unsupported():
        raise ValueError("rejected_unsupported: unverified harness version")

    harness.prepare_action_hook = unsupported
    with pytest.raises(InterventionError, match="rejected_unsupported"):
        await gate.prepare()
    records = [json.loads(line) for line in (gate.bundle.root / "receipts.jsonl").read_text().splitlines()]
    assert records[-1]["result"] == "rejected_unsupported" and not records[-1]["verification"]["ok"]
    assert gate.binding is None
    await monitor.aclose()
    await admin.aclose()
