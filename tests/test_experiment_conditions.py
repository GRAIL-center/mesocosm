"""Manifest guardrails and three-agent integration for the experiment conditions.

Hardware is replaced by the existing test harness. These checks establish control-plane
semantics, not successful real-agent collaboration or reinforcement learning.
"""

import asyncio
import json

import pytest
import yaml
from test_rewards import RatingsService, manifest_data
from test_run_e2e import two_tasks
from test_run_e2e import world as world

from mesocosm.adapters.harbor.task import Reward
from mesocosm.core.control import STOP_MARKER
from mesocosm.core.manifest import LearningSpec, RewardSpec, TaskSpec, validate_manifest
from mesocosm.core.records import RunBundle
from mesocosm.core.run import run_world


def _write(tmp_path, data):
    task = tmp_path / "task"
    task.mkdir(exist_ok=True)
    (task / "instruction.md").write_text("Solve the task.")
    path = tmp_path / "world.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


@pytest.mark.parametrize("changes", [
    {"mode": "persistent"},
    {"learning": {"mode": "rl"}},
    {"rewards": {"enabled": False}, "learning": {"mode": "in_context"}},
    {"rewards": {"enabled": True, "source": "peer_verdict"}},
    {"services": {"chat": {"enabled": True}, "peer_review": {"enabled": True, "protocol": "legacy"}}},
    {"rewards": {"enabled": False}, "services": {"chat": {"enabled": True},
                                                "peer_review": {"enabled": True, "protocol": "ratings"}}},
    {"judge": {"monitor": {"timing": "before_action"}}},
    {"conditions": {"budget": {"requests": 1}}},
    {"rewards": {"enabled": True, "allcoation": "team_mean"}},
])
def test_unsupported_or_incompatible_condition_combinations_fail_explicitly(tmp_path, changes):
    data = manifest_data()
    data.update(changes)
    report = validate_manifest(_write(tmp_path, data))
    assert not report.ok and report.errors


def test_valid_three_agent_peer_reward_configuration(tmp_path):
    data = manifest_data()
    data["rewards"] = {"enabled": True, "source": "peer_verdict", "allocation": "mixed", "team_weight": 0.25}
    data["services"] = {"chat": {"enabled": True}, "peer_review": {"enabled": True, "protocol": "ratings"}}
    data["learning"] = {"mode": "in_context"}
    report = validate_manifest(_write(tmp_path, data))
    assert report.ok, report.errors


@pytest.mark.parametrize("changes", [
    {"rewards": {"enabled": False, "allocation": "team_mean"}},
    {"rewards": {"enabled": True, "allocation": "individual", "team_weight": 0.75}},
    {"services": {"chat": {"enabled": True}, "peer_review": {
        "enabled": True, "protocol": "ratings", "reward_per_verdict": 3}}},
    {"judge": {"monitor": {"enabled": True, "model": "policy", "on_flag": "terminate"}}},
    {"judge": {"monitor": {"enabled": True, "model": "policy", "decision_timeout_seconds": 2}}},
    {"judge": {"monitor": {"enabled": True, "model": "policy", "timing": "pre_action", "scope": "joint"}}},
])
def test_configured_conditions_cannot_be_silently_ignored(tmp_path, changes):
    data = manifest_data()
    data.update(changes)
    report = validate_manifest(_write(tmp_path, data))
    assert not report.ok, "an experimental variable with no enforcement must be rejected"


def _three_agents(manifest):
    for i in (2, 3):
        manifest.agents.append(manifest.agents[0].model_copy(update={
            "id": f"agent-{i}", "url": f"http://127.0.0.1:{4095 + i}"}))


class MeasuredVerifier:
    def __init__(self, values):
        self.values = values
        self.calls = []

    async def verify(self, task, workspace, logs_dir, script="test.sh"):
        self.calls.append((workspace.name, task.name, script))
        return Reward(self.values[workspace.name], "reward.txt")


@pytest.mark.parametrize("learning", ["none", "in_context"])
async def test_three_agent_incentives_and_delivered_feedback_do_not_update_policy(world, monkeypatch, learning):
    manifest, components, tmp_path = world
    monkeypatch.setattr("mesocosm.core.run.EVENT_GRACE_SECONDS", 0)
    _three_agents(manifest)
    path = two_tasks(tmp_path)
    manifest.tasks = [TaskSpec(path="tasks/first"), TaskSpec(path="tasks/second")]
    manifest.rewards = RewardSpec(enabled=True, allocation="team_mean")
    manifest.learning = LearningSpec(mode=learning)
    initial_config = manifest.model_dump(mode="json")
    seen = []
    components.hooks["on_message"] = lambda body: seen.append(body["parts"][0]["text"])
    comps = components()
    verifier = MeasuredVerifier({"agent-1": 0.0, "agent-2": 0.5, "agent-3": 1.0})
    comps.verifier = verifier
    result = await asyncio.wait_for(run_world(manifest, path, comps, tmp_path / "runs"), 3)
    assert result.status == "ok", result.error
    assert len(verifier.calls) == 6 and len(seen) == 6, "feedback must not add extra agent turns"
    assert result.rewards == {f"{a}/{t}": r for a, r in verifier.values.items() for t in ("first", "second")}
    summary = json.loads((result.bundle_path / "summary.json").read_text())
    assert len(summary["task_outcomes"]) == 6 and all(o["status"] == "verified" for o in summary["task_outcomes"])
    assert len(summary["learning_rewards"]) == 6
    assert all(o["allocated_reward"] == 0.5 for o in summary["learning_rewards"])
    assert summary["learning"] == {"mode": learning, "weight_updates": 0}
    feedback = [text for text in seen if "Previous episode feedback:" in text]
    delivered = [e for e in RunBundle.open(result.bundle_path).events() if e.kind == "learning.feedback_delivered"]
    assert len(feedback) == len(delivered) == (3 if learning == "in_context" else 0)
    assert all("(second)" in text for text in feedback)
    assert all(e.data["weight_update"] is False and e.data["mode"] == "in_context" for e in delivered)
    assert manifest.model_dump(mode="json") == initial_config


class SharedRatings(RatingsService):
    async def set_clock(self, start):
        return {"started": start}

    async def world_state(self, agent_id):
        return {"chat": {"verdicts": self.ratings}}


async def test_peer_incentives_do_not_overwrite_independent_verifier_results(world, monkeypatch):
    manifest, components, tmp_path = world
    monkeypatch.setattr("mesocosm.core.run.EVENT_GRACE_SECONDS", 0)
    _three_agents(manifest)
    path = two_tasks(tmp_path)
    manifest.tasks = [TaskSpec(path="tasks/first")]
    manifest.rewards = RewardSpec(enabled=True, source="peer_verdict", allocation="individual")
    manifest.services.peer_review.enabled = True
    manifest.services.peer_review.protocol = "ratings"
    manifest.services.chat.enabled = True
    ids = [a.id for a in manifest.agents]
    ratings = [{"from": a, "about": b, "verdict": "accept"} for a in ids for b in ids if a != b]
    comps = components()
    comps.services = SharedRatings(ratings)
    comps.verifier = MeasuredVerifier({a: 0.0 for a in ids})
    result = await asyncio.wait_for(run_world(manifest, path, comps, tmp_path / "runs"), 3)
    assert result.status == "ok", result.error
    assert result.rewards == {f"{a}/first": 0.0 for a in ids}
    summary = json.loads((result.bundle_path / "summary.json").read_text())
    assert all(o["reward"] == 0.0 for o in summary["task_outcomes"])
    assert all(o["allocated_reward"] == 1.0 for o in summary["learning_rewards"])
    assert not any(e.kind == "peer_review.episode" for e in RunBundle.open(result.bundle_path).events())


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf")])
async def test_bad_independent_measurement_fails_run_without_leaving_reward_barrier_hanging(world, monkeypatch, bad):
    manifest, components, tmp_path = world
    monkeypatch.setattr("mesocosm.core.run.EVENT_GRACE_SECONDS", 0)
    _three_agents(manifest)
    path = two_tasks(tmp_path)
    manifest.tasks = [TaskSpec(path="tasks/first")]
    manifest.rewards = RewardSpec(enabled=True, allocation="team_mean")
    comps = components()
    comps.verifier = MeasuredVerifier({"agent-1": 0.0, "agent-2": 0.5, "agent-3": bad})
    result = await asyncio.wait_for(run_world(manifest, path, comps, tmp_path / "runs"), 3)
    assert result.status == "failed_verifier"
    events = RunBundle.open(result.bundle_path).events()
    assert not any(e.kind == "learning.reward" for e in events)
    assert not any(e.kind == "verifier.reward" and e.agent_id == "agent-3" for e in events)
    summary = json.loads((result.bundle_path / "summary.json").read_text())
    assert summary["learning_rewards"] == []
    failed = next(o for o in summary["task_outcomes"] if o["agent_id"] == "agent-3")
    assert failed["status"] == "failed" and failed["reward"] is None and failed["error"]


async def test_stop_between_agent_starts_cannot_leave_remaining_agents_in_barrier(world, monkeypatch):
    import mesocosm.core.run as run_module

    manifest, components, tmp_path = world
    monkeypatch.setattr(run_module, "EVENT_GRACE_SECONDS", 0)
    _three_agents(manifest)
    path = two_tasks(tmp_path)
    manifest.tasks = [TaskSpec(path="tasks/first")]
    manifest.rewards = RewardSpec(enabled=True, allocation="team_mean")
    original_stop = run_module.stop_requested
    checks = 0

    def stop_after_first_check(bundle_root):
        nonlocal checks
        checks += 1
        if checks == 2:
            marker = bundle_root / STOP_MARKER
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.touch()
        return original_stop(bundle_root)

    monkeypatch.setattr(run_module, "stop_requested", stop_after_first_check)
    result = await asyncio.wait_for(run_world(manifest, path, components(), tmp_path / "runs"), 3)
    assert result.status == "stopped"
    events = RunBundle.open(result.bundle_path).events()
    assert not any(e.kind == "learning.reward" for e in events), "a partial cohort has no measured team reward"


async def test_duplicate_resolved_task_names_fail_before_agent_execution(world, monkeypatch):
    manifest, components, tmp_path = world
    monkeypatch.setattr("mesocosm.core.run.EVENT_GRACE_SECONDS", 0)
    _three_agents(manifest)
    path = two_tasks(tmp_path)
    for name in ("first", "second"):
        (tmp_path / "tasks" / name / "task.toml").write_text('[metadata]\nname = "same-task"\n')
    manifest.tasks = [TaskSpec(path="tasks/first"), TaskSpec(path="tasks/second")]
    manifest.rewards = RewardSpec(enabled=True)
    sent = []
    components.hooks["on_message"] = lambda body: sent.append(body)
    result = await asyncio.wait_for(run_world(manifest, path, components(), tmp_path / "runs"), 3)
    assert result.status == "failed" and "unique" in result.error
    assert sent == []
    assert not any(e.kind == "learning.reward" for e in RunBundle.open(result.bundle_path).events())


async def test_feedback_is_not_claimed_delivered_when_next_turn_fails(world, monkeypatch):
    manifest, components, tmp_path = world
    monkeypatch.setattr("mesocosm.core.run.EVENT_GRACE_SECONDS", 0)
    _three_agents(manifest)
    path = two_tasks(tmp_path)
    manifest.tasks = [TaskSpec(path="tasks/first"), TaskSpec(path="tasks/second")]
    manifest.rewards = RewardSpec(enabled=True)
    manifest.learning = LearningSpec(mode="in_context")

    def reject_feedback(body):
        if "Previous episode feedback:" in body["parts"][0]["text"]:
            raise RuntimeError("harness did not accept the next turn")

    components.hooks["on_message"] = reject_feedback
    result = await asyncio.wait_for(run_world(manifest, path, components(), tmp_path / "runs"), 3)
    assert result.status == "failed"
    events = RunBundle.open(result.bundle_path).events()
    assert any(e.kind == "learning.feedback_attached" for e in events)
    assert not any(e.kind == "learning.feedback_delivered" for e in events)
