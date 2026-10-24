"""Controller sequencing with substituted GPU/Docker boundaries, not live-agent evidence."""

from __future__ import annotations

import asyncio
import json
import threading

import pytest
import yaml

from mesocosm.core.manifest import WorldManifest
from mesocosm.core.run import RunResult
from mesocosm.learning.config import PolicyIdentity, TrainingSpec, policy_id
from mesocosm.learning.controller import run_training, training_world
from mesocosm.learning.rollout import ActionRequest, AgentTrajectory, RolloutBatch


def identity(model="base", adapter=None):
    values = {"model": model, "base_model": "test/model", "base_revision": "a" * 40,
              "base_sha256": "b" * 64, "adapter_sha256": "c" * 64 if adapter else None,
              "adapter_path": adapter}
    return PolicyIdentity(id=policy_id(values["base_model"], values["base_revision"],
                                      values["base_sha256"], values["adapter_sha256"]), **values)


def setup(tmp_path, monkeypatch, *, monitor=False, failures=None, updater=None):
    failures = failures or {}
    world = {"name": "training-test", "model": {"id": "base", "hf_repo": "test/model"},
             "gateway": {"url": "http://gateway", "api_key": "unit-control-key"},
             "agents": [{"id": f"agent-{index}"} for index in range(3)],
             "tasks": [{"dataset": "terminal-bench@2.0/fix-git"}], "services": {"enabled": False},
             "memory": "reset", "rewards": {"enabled": True}}
    if monitor:
        world["judge"] = {"monitor": {"enabled": True, "model": "base"}}
    (tmp_path / "world.yaml").write_text(yaml.safe_dump(world))
    path = tmp_path / "training.yaml"
    configured = TrainingSpec(name="training-test", world="world.yaml", base_model="test/model",
                              base_revision="a" * 40, initial_model="base", server_url="http://vllm")
    path.write_text(yaml.safe_dump(configured.model_dump()))
    current, updated = identity(), identity("updated", str(tmp_path / "adapter"))
    calls, worlds = [], []

    class Server:
        async def _act(self, action):
            calls.append(action)
            if action in failures:
                raise failures[action]
            return {"action": action}

        async def verify(self, policy):
            assert policy == current
            return await self._act("verify_initial")

        async def sleep(self):
            return await self._act("sleep")

        async def wake(self):
            return await self._act("wake")

        async def load(self, policy):
            assert policy == updated
            return await self._act("activate")

        async def aclose(self):
            return await self._act("close")

    def collect(bundle, expected, episode_id):
        calls.append("collect")
        if "collect" in failures:
            raise failures["collect"]
        trajectories = []
        for index in range(3):
            agent = f"agent-{index}"
            request = ActionRequest(request_id=f"r-{episode_id}-{index}", agent_id=agent, task="fix-git",
                                    episode_id=episode_id, session_id=f"s-{index}", prompt_token_ids=[1],
                                    token_ids=[2], token_logprobs=[-1])
            trajectories.append(AgentTrajectory(agent_id=agent, task="fix-git", episode_id=episode_id,
                                                 reward=0, reward_source="verifier", requests=[request]))
        return RolloutBatch(policy=expected, source_run_ids=[bundle.name], trajectories=trajectories)

    def update(batch, policy, spec, out):
        calls.append("update")
        if "update" in failures:
            raise failures["update"]
        assert batch.policy == current and policy == current
        if updater:
            return updater(batch, policy, spec, out)
        return {"status": "updated", "next_policy": updated.model_dump(), "optimizer_steps": 1}

    def verify(batch, policy, spec):
        calls.append("verify_final")
        assert batch.policy == updated and policy == updated
        if "verify_final" in failures:
            raise failures["verify_final"]
        return {"status": "verified", "optimizer_steps": 0}

    async def run_one(manifest, context):
        calls.append("world")
        loaded = WorldManifest.model_validate(yaml.safe_load(manifest.read_text()))
        worlds.append((manifest, loaded, context))
        if "world" in failures:
            manifest.with_suffix(".result.json").write_text(json.dumps({"status": "ok", "run_id": "completed"}))
            raise failures["world"]
        return RunResult(run_id=f"run-{len(worlds)}", bundle_path=tmp_path / f"run-{len(worlds)}",
                         status="ok", rewards={})

    monkeypatch.setattr("mesocosm.learning.controller.initial_policy", lambda spec: current)
    monkeypatch.setattr("mesocosm.learning.controller.VLLMPolicyServer", lambda *args: Server())
    monkeypatch.setattr("mesocosm.learning.collect.collect_rollouts", collect)
    monkeypatch.setattr("mesocosm.learning.training.reinforce_update", update)
    monkeypatch.setattr("mesocosm.learning.training.verify_rollout_policy", verify)
    return configured, path, tmp_path / "output", run_one, calls, worlds


async def test_complete_world_update_activation_next_world_and_final_verification(tmp_path, monkeypatch):
    configured, path, out, run_one, calls, worlds = setup(tmp_path, monkeypatch, monitor=True)
    result = await run_training(configured, path, out, run_one)
    assert result["status"] == "ok" and len(result["updates"]) == 1
    assert calls == ["verify_initial", "world", "collect", "sleep", "update", "wake", "activate",
                     "world", "collect", "sleep", "verify_final", "wake", "close"]
    assert len({entry[1].gateway.run_label for entry in worlds}) == 2
    for index, (_, world, context) in enumerate(worlds):
        expected = "base" if index == 0 else "updated"
        assert all(world.model_for(agent).id == expected for agent in world.agents)
        assert all(world.model_for(agent).revision == context.policy.base_revision for agent in world.agents)
        assert all(world.model_for(agent).hf_repo == context.policy.base_model for agent in world.agents)
        assert context.policy.model == expected and context.episode_id == world.gateway.run_label
        assert world.judge.monitor.model == "base" and "base" in world.catalog()
    assert worlds[1][1].catalog()["base"].hf_repo == "test/model"
    assert result["episodes"][1]["evaluation_only"] is True
    assert json.loads((out / "training.json").read_text()) == result


@pytest.mark.parametrize("phase", ["verify_initial", "world", "collect", "activate", "verify_final"])
async def test_failures_stop_next_world_and_persist_the_phase(tmp_path, monkeypatch, phase):
    error = RuntimeError(f"failure at {phase}")
    configured, path, out, run_one, calls, worlds = setup(tmp_path, monkeypatch, failures={phase: error})
    with pytest.raises(RuntimeError, match=f"failure at {phase}"):
        await run_training(configured, path, out, run_one)
    result = json.loads((out / "training.json").read_text())
    assert result["status"] == "failed" and calls[-1] == "close"
    assert any(f"failure at {phase}" in item["error"] for item in result["errors"])
    assert len(worlds) == (0 if phase == "verify_initial" else 2 if phase == "verify_final" else 1)
    if phase == "world":
        assert result["episodes"][0]["status"] == "failed"
        receipt = worlds[0][0].with_suffix(".result.json")
        assert result["episodes"][0]["world_result"] == str(receipt)
        assert json.loads(receipt.read_text())["run_id"] == "completed"
    if phase == "verify_final":
        assert calls[-2] == "wake"


async def test_training_error_and_wake_error_are_both_retained(tmp_path, monkeypatch):
    configured, path, out, run_one, calls, _ = setup(tmp_path, monkeypatch, failures={
        "update": ValueError("training failed"), "wake": RuntimeError("wake failed")})
    with pytest.raises(ValueError, match="training failed"):
        await run_training(configured, path, out, run_one)
    result = json.loads((out / "training.json").read_text())
    assert result["status"] == "failed"
    assert [(item["phase"], item["error"]) for item in result["errors"]] == [
        ("policy_compute", "training failed"), ("wake", "wake failed")]
    assert calls[-2:] == ["wake", "close"] and "activate" not in calls


async def test_uncertain_sleep_failure_still_attempts_wake(tmp_path, monkeypatch):
    configured, path, out, run_one, calls, _ = setup(tmp_path, monkeypatch, failures={"sleep": RuntimeError("sleep timeout")})
    with pytest.raises(RuntimeError, match="sleep timeout"):
        await run_training(configured, path, out, run_one)
    assert calls[-3:] == ["sleep", "wake", "close"] and "update" not in calls


async def test_client_cleanup_failure_cannot_leave_successful_summary(tmp_path, monkeypatch):
    configured, path, out, run_one, _, _ = setup(tmp_path, monkeypatch, failures={"close": RuntimeError("close failed")})
    with pytest.raises(RuntimeError, match="close failed"):
        await run_training(configured, path, out, run_one)
    result = json.loads((out / "training.json").read_text())
    assert result["status"] == "failed" and result["errors"][-1]["phase"] == "client_cleanup"
    assert len(result["episodes"]) == 2 and len(result["updates"]) == 1


async def test_zero_advantage_stops_without_activating_or_claiming_update(tmp_path, monkeypatch):
    def no_update(batch, policy, spec, output):
        return {"status": "no_update", "reason": "zero_advantage", "optimizer_steps": 0,
                "next_policy": policy.model_dump()}
    configured, path, out, run_one, calls, worlds = setup(tmp_path, monkeypatch, updater=no_update)
    result = await run_training(configured, path, out, run_one)
    assert result["status"] == "no_update" and len(worlds) == 1
    assert "activate" not in calls and calls[-2:] == ["wake", "close"]


async def test_cancelled_thread_finishes_and_releases_gpu_before_wake(tmp_path, monkeypatch):
    started, release, finished = threading.Event(), threading.Event(), threading.Event()

    def blocking_update(batch, policy, spec, output):
        started.set()
        assert release.wait(timeout=5)
        output.mkdir()
        (output / "update.json").write_text(json.dumps({"status": "updated", "optimizer_steps": 1}))
        finished.set()
        return {"status": "no_update", "optimizer_steps": 0, "next_policy": policy.model_dump()}

    configured, path, out, run_one, calls, _ = setup(tmp_path, monkeypatch, updater=blocking_update)
    pending = asyncio.create_task(run_training(configured, path, out, run_one))
    try:
        assert await asyncio.to_thread(started.wait, 3)
        pending.cancel()
        await asyncio.sleep(0.02)
        assert "wake" not in calls and not pending.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await pending
    finally:
        release.set()
        if not pending.done():
            await asyncio.gather(pending, return_exceptions=True)
    assert finished.is_set() and calls[-2:] == ["wake", "close"]
    result = json.loads((out / "training.json").read_text())
    assert result["status"] == "cancelled" and "activate" not in calls
    assert result["episodes"][0]["update_receipt"] == str(out / "update-0001" / "update.json")


@pytest.mark.parametrize("change", [{"memory": "carry"}, {"learning": {"mode": "in_context"}},
                                  {"rewards": {"enabled": False}},
                                  {"model": {"id": "base", "hf_repo": "test/model", "revision": "c" * 40}}])
def test_unsupported_training_world_fails_before_server_or_output(tmp_path, monkeypatch, change):
    configured, path, out, _, calls, _ = setup(tmp_path, monkeypatch)
    world = yaml.safe_load((tmp_path / "world.yaml").read_text())
    world.update(change)
    (tmp_path / "world.yaml").write_text(yaml.safe_dump(world))
    with pytest.raises(ValueError):
        training_world(configured, path)
    assert not calls and not out.exists()
