"""Synthetic harness/upstream integration through the real run manager and gateway.

This validates bundle contracts for three concurrent test harnesses, not GPU learning or native
OpenCode acceptance. Real native policy execution is recorded separately as hardware evidence.
"""

import asyncio

import httpx
import pytest
import yaml
from test_run_e2e import FakeEgress, FakeVerifier
from test_training_gateway import policy, training_gateway

from mesocosm.core.manifest import WorldManifest
from mesocosm.core.records import RunBundle
from mesocosm.core.run import Components, run_world
from mesocosm.learning.collect import collect_rollouts
from mesocosm.learning.controller import RolloutContext


class FixtureHarness:
    def __init__(self, agent, workspace, transport):
        self.agent, self.workspace = agent, workspace
        self.client = httpx.AsyncClient(transport=transport, base_url="http://gateway",
                                       headers={"Authorization": f"Bearer key-{agent.id[-1]}"})
        self.sessions = {}

    async def create_session(self, title):
        session = f"{self.agent.id}-session-{len(self.sessions) + 1}"
        self.sessions[session] = []
        return session

    async def send_message(self, session_id, text, *, provider_id, model_id):
        response = await self.client.post("/v1/chat/completions", json={"model": model_id,
                                          "messages": [{"role": "user", "content": text}]})
        response.raise_for_status()
        message = response.json()["choices"][0]["message"]
        reply = {"info": {"role": "assistant", "sessionID": session_id},
                 "parts": [{"type": "text", "text": message["content"]}]}
        self.sessions[session_id].append(reply)
        self.workspace.joinpath("hello.txt").write_text("Hello, team!\n")
        return reply

    async def list_messages(self, session_id):
        return self.sessions[session_id]

    async def abort_session(self, session_id):
        return True

    async def events(self):
        yield {"type": "server.connected", "properties": {}}
        await asyncio.Event().wait()

    async def aclose(self):
        await self.client.aclose()


@pytest.mark.parametrize("rounds", [1, 2])
async def test_run_world_emits_collectable_three_agent_multi_task_native_bundle(tmp_path, monkeypatch, rounds):
    monkeypatch.setattr("mesocosm.core.run.EVENT_GRACE_SECONDS", 0)
    app, upstream, transport, admin = training_gateway(tmp_path / "gateway")
    for name in ("first", "second"):
        task = tmp_path / name
        task.mkdir()
        task.joinpath("instruction.md").write_text("Write hello.txt with Hello, team!\n")
        if rounds > 1:
            task.joinpath("task.toml").write_text('[[rounds]]\nprompt="Begin the task."\n'
                                                '[[rounds]]\nprompt="Finish the task."\n')
    manifest = WorldManifest.model_validate({"name": "training-runtime", "profile": "fixture",
                "model": {"id": "policy-0"},
                "agents": [{"id": f"agent-{i}"} for i in range(1, 4)],
                "tasks": [{"path": "first"}, {"path": "second"}],
                "gateway": {"url": "http://gateway", "run_label": "world"}, "rewards": {"enabled": True}})
    path = tmp_path / "world.yaml"
    path.write_text(yaml.safe_dump(manifest.model_dump(mode="json")))
    components = Components(harness_for=lambda agent: FixtureHarness(agent, tmp_path / "workspace" / agent.id, transport),
                            workspace_for=lambda agent: tmp_path / "workspace" / agent.id,
                            gateway=admin, egress=FakeEgress(True), verifier=FakeVerifier(),
                            rollout=RolloutContext(policy=policy(), episode_id="episode-1"))
    result = await asyncio.wait_for(run_world(manifest, path, components, tmp_path / "runs"), 5)
    assert result.status == "ok", result.error
    batch = collect_rollouts(result.bundle_path, policy(), "episode-1")
    assert len(batch.trajectories) == 6
    assert {trajectory.agent_id for trajectory in batch.trajectories} == {"agent-1", "agent-2", "agent-3"}
    assert all(len(trajectory.requests) == rounds and trajectory.reward == 1.0 for trajectory in batch.trajectories)
    events = RunBundle.open(result.bundle_path).events()
    assert sum(event.kind == "rollout.bound" for event in events) == 6 * rounds
    assert sum(event.kind == "rollout.unbound" for event in events) == 6 * rounds
    assert len(upstream.state.requests) == 6 * rounds
    await admin.aclose()
    await app.state.gateway.client.aclose()
