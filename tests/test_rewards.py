"""Incentive allocation and N-agent episodes; these are control-plane tests, not agent evidence."""

import asyncio

import pytest

from mesocosm.adapters.harbor.task import load_task
from mesocosm.core.manifest import RewardSpec, WorldManifest
from mesocosm.core.records import RunBundle
from mesocosm.core.rewards import RewardEpisodes, allocate, outcome_records
from mesocosm.core.run import _run_together


def manifest_data(n=3):
    return {"name": "conditions-test", "model": {"id": "policy", "hf_repo": "model/weights"},
            "gateway": {"url": "http://gateway"}, "agents": [{"id": f"agent-{i}"} for i in range(1, n + 1)],
            "tasks": [{"path": "task"}], "rewards": {"enabled": True}}


@pytest.mark.parametrize(("allocation", "weight", "expected"), [
    ("individual", 0.5, {"a": 0.0, "b": 0.5, "c": 1.0}),
    ("team_mean", 0.5, {"a": 0.5, "b": 0.5, "c": 0.5}),
    ("mixed", 0.25, {"a": 0.125, "b": 0.5, "c": 0.875}),
    ("mixed", 0.0, {"a": 0.0, "b": 0.5, "c": 1.0}),
    ("mixed", 1.0, {"a": 0.5, "b": 0.5, "c": 0.5}),
])
def test_three_agent_allocations_preserve_independent_measurements(allocation, weight, expected):
    original = {"a": 0.0, "b": 0.5, "c": 1.0}
    allocated = allocate(original, RewardSpec(enabled=True, allocation=allocation, team_weight=weight))
    assert allocated == expected
    assert original == {"a": 0.0, "b": 0.5, "c": 1.0}


@pytest.mark.parametrize(("allocation", "weight", "expected"), [
    ("individual", 0.5, {"a": 0.0, "b": None, "c": 1.0}),
    ("team_mean", 0.5, {"a": None, "b": None, "c": None}),
    ("mixed", 0.5, {"a": None, "b": None, "c": None}),
    ("mixed", 0.0, {"a": 0.0, "b": None, "c": 1.0}),
    ("mixed", 1.0, {"a": None, "b": None, "c": None}),
])
def test_missing_measurement_never_becomes_zero(allocation, weight, expected):
    assert allocate({"a": 0.0, "b": None, "c": 1.0},
                    RewardSpec(enabled=True, allocation=allocation, team_weight=weight)) == expected


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_raw_scores_are_rejected(bad):
    with pytest.raises(ValueError, match="finite"):
        allocate({"a": bad, "b": 0.5, "c": 1.0}, RewardSpec(enabled=True))


class RatingsService:
    def __init__(self, ratings):
        self.ratings = ratings
        self.epochs = []

    async def set_epoch(self, task):
        self.epochs.append(task)

    async def verdicts(self, task):
        return self.ratings


def _task(tmp_path, name="task"):
    directory = tmp_path / name
    directory.mkdir()
    (directory / "instruction.md").write_text("Complete the actual task.")
    return load_task(directory)


async def _finish(episodes, task, measurements):
    await episodes.open(task)
    await asyncio.wait_for(asyncio.gather(*(episodes.finish(a, task, value)
                                           for a, value in measurements.items())), timeout=1)


@pytest.mark.parametrize("topology", ["all", "ring"])
async def test_peer_reward_is_distinct_from_verifier_and_scales_to_four_agents(tmp_path, topology):
    data = manifest_data(4)
    data["rewards"] = {"enabled": True, "source": "peer_verdict", "allocation": "individual"}
    data["services"] = {"peer_review": {"enabled": True, "protocol": "ratings", "topology": topology}}
    manifest = WorldManifest.model_validate(data)
    ids = [a.id for a in manifest.agents]
    # All reviewers accept an independently failed task; disagreement remains visible.
    ratings = [{"from": a, "about": b, "verdict": "accept"} for a in ids for b in ids if a != b]
    services = RatingsService(ratings)
    bundle = RunBundle.create(tmp_path / "runs")
    episodes = RewardEpisodes(manifest, services, bundle)
    task = _task(tmp_path)
    verifier = {a: 0.0 for a in ids}
    await _finish(episodes, task, verifier)
    rewards = [e for e in bundle.events() if e.kind == "learning.reward"]
    assert len(rewards) == 4 and services.epochs == [task.name]
    assert all(e.data["raw_reward"] == 1.0 and e.data["allocated_reward"] == 1.0 for e in rewards)
    assert all(e.data["evidence"]["verifier_rewards"] == verifier for e in rewards)
    assert verifier == {a: 0.0 for a in ids}


@pytest.mark.parametrize("verdict", [None, "maybe"])
async def test_missing_or_invalid_peer_verdict_is_unmeasured_not_rejected_or_accepted(tmp_path, verdict):
    data = manifest_data()
    data["rewards"] = {"enabled": True, "source": "peer_verdict", "allocation": "team_mean"}
    data["services"] = {"peer_review": {"enabled": True, "protocol": "ratings", "topology": "ring"}}
    ratings = [{"from": "agent-1", "about": "agent-2", "verdict": "accept"},
               {"from": "agent-2", "about": "agent-3", "verdict": "reject"}]
    if verdict is not None:
        ratings.append({"from": "agent-3", "about": "agent-1", "verdict": verdict})
    bundle = RunBundle.create(tmp_path / "runs")
    episodes = RewardEpisodes(WorldManifest.model_validate(data), RatingsService(ratings), bundle)
    await _finish(episodes, _task(tmp_path), {"agent-1": 1.0, "agent-2": 1.0, "agent-3": 1.0})
    scores = {e.agent_id: e.data for e in bundle.events() if e.kind == "learning.reward"}
    assert scores["agent-1"]["raw_reward"] is None
    assert scores["agent-2"]["raw_reward"] == 1.0 and scores["agent-3"]["raw_reward"] == 0.0
    assert all(score["allocated_reward"] is None for score in scores.values())


async def test_first_failure_cancels_agents_waiting_for_episode_barrier(tmp_path):
    bundle = RunBundle.create(tmp_path / "runs")
    episodes = RewardEpisodes(WorldManifest.model_validate(manifest_data()), None, bundle)
    task = _task(tmp_path)
    await episodes.open(task)
    waiting = asyncio.Event()
    cancelled = []

    async def participant(agent):
        waiting.set()
        try:
            await episodes.finish(agent, task, 1.0)
        finally:
            cancelled.append(agent)
        return {}

    async def failed():
        await waiting.wait()
        raise RuntimeError("independent verifier failed")

    with pytest.raises(RuntimeError, match="verifier failed"):
        await asyncio.wait_for(_run_together([participant("agent-1"), participant("agent-2"), failed()]), 1)
    assert sorted(cancelled) == ["agent-1", "agent-2"]
    assert not any(e.kind == "learning.reward" for e in bundle.events())


def test_outcomes_keep_objective_scores_separate_from_incentive_scores(tmp_path):
    bundle = RunBundle.create(tmp_path / "runs")
    for agent in ("agent-1", "agent-2", "agent-3"):
        bundle.record("harness.session", agent_id=agent, task="work")
    bundle.record("verifier.reward", agent_id="agent-1", task="work", reward=0.0)
    bundle.record("learning.reward", agent_id="agent-1", task="work", raw_reward=1.0, allocated_reward=1.0)
    bundle.record("harness.task_timeout", agent_id="agent-2", task="work")
    summary = outcome_records(bundle.events())
    outcomes = {o["agent_id"]: o for o in summary["task_outcomes"]}
    assert outcomes["agent-1"]["reward"] == 0.0 and outcomes["agent-1"]["status"] == "verified"
    assert outcomes["agent-2"]["reward"] is None and outcomes["agent-2"]["status"] == "failed"
    assert outcomes["agent-3"]["reward"] is None and outcomes["agent-3"]["status"] == "incomplete"
    assert summary["learning_rewards"][0]["allocated_reward"] == 1.0
