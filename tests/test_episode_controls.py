"""Measured N-agent episode plumbing; fixed ratings here are not agent behavior evidence."""

from __future__ import annotations

import asyncio
import json
from collections import Counter

import pytest
from test_rewards import _task, manifest_data

from mesocosm.core.episode_controls import (
    AssignmentSpec,
    FeedbackSpec,
    ReputationSpec,
    RewardTerm,
    component_rewards,
    episode_metrics,
    review_assignment,
    select_participants,
)
from mesocosm.core.episode_validation import episode_condition_errors
from mesocosm.core.manifest import RewardSpec, WorldManifest
from mesocosm.core.records import RunBundle
from mesocosm.core.rewards import RewardEpisodes

AGENTS = ["agent-1", "agent-2", "agent-3"]


class RatingsService:
    """In-memory verdict transport used only to test the real episode controller."""

    def __init__(self, ratings=None):
        self.ratings = ratings or []
        self.epochs = []

    async def set_epoch(self, task, **kwargs):
        self.epochs.append({"task": task, **kwargs})

    async def verdicts(self, task):
        return list(self.ratings)


def configured(**changes):
    data = manifest_data(3)
    data.update(changes)
    return WorldManifest.model_validate(data)


def peer_settings(topology="ring", **options):
    return {"chat": {"enabled": True},
            "peer_review": {"enabled": True, "protocol": "ratings", "topology": topology, **options}}


async def complete(episodes, task, scores, usages=None):
    await episodes.open(task)
    await asyncio.wait_for(asyncio.gather(*(
        episodes.finish(agent, task, score, (usages or {}).get(agent))
        for agent, score in scores.items()
    )), timeout=1)


def test_three_agent_components_use_declared_weights_scales_and_personal_overrides():
    terms = [RewardTerm(metric="verifier", weight=2),
             RewardTerm(metric="model_requests", weight=-0.5, scale=2,
                        agent_weights={"agent-2": 0, "agent-3": -1})]
    metrics = {"agent-1": {"verifier": 1.0, "model_requests": 2},
               "agent-2": {"verifier": 0.5, "model_requests": None},
               "agent-3": {"verifier": 0.25, "model_requests": 4}}
    assert component_rewards(terms, metrics) == {"agent-1": 1.5, "agent-2": 1.0, "agent-3": -1.5}
    assert metrics["agent-2"]["model_requests"] is None, "zero weight must not invent a measurement"


def test_missing_component_is_unknown_only_for_agents_that_depend_on_it():
    terms = [RewardTerm(metric="verifier"),
             RewardTerm(metric="completion_tokens", weight=-1, scale=100,
                        agent_weights={"agent-2": 0})]
    metrics = {"agent-1": {"verifier": 1.0, "completion_tokens": None},
               "agent-2": {"verifier": 0.5, "completion_tokens": None},
               "agent-3": {"verifier": 1.0, "completion_tokens": 0}}
    assert component_rewards(terms, metrics) == {"agent-1": None, "agent-2": 0.5, "agent-3": 1.0}


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_measurement_cannot_become_a_component_reward(bad):
    metrics = {agent: {"verifier": bad if agent == "agent-2" else 1.0} for agent in AGENTS}
    with pytest.raises(ValueError, match="nonfinite"):
        component_rewards([RewardTerm(metric="verifier")], metrics)


def test_review_accuracy_uses_original_thresholded_outcome_not_peer_consensus():
    assignment = review_assignment(AGENTS, "ring", 0, 0)
    verdicts = [{"from": "agent-1", "about": "agent-2", "verdict": "reject"},
                {"from": "agent-2", "about": "agent-3", "verdict": "accept"},
                {"from": "agent-3", "about": "agent-1", "verdict": "accept"}]
    original = {"agent-1": 1.0, "agent-2": 0.5, "agent-3": 0.0}
    metrics = episode_metrics(original, verdicts, assignment,
                              {"agent-1": {"model_requests": 0, "completion_tokens": 0}}, 0.6)
    assert [metrics[a]["review_accuracy"] for a in AGENTS] == [1.0, 0.0, 1.0]
    assert [metrics[a]["peer_acceptance"] for a in AGENTS] == [1.0, 0.0, 1.0]
    assert [metrics[a]["verifier"] for a in AGENTS] == [1.0, 0.5, 0.0]
    assert metrics["agent-1"]["completion_tokens"] == 0
    assert metrics["agent-2"]["completion_tokens"] is None
    missing = episode_metrics({**original, "agent-2": None}, verdicts[:-1], assignment, {}, 0.6)
    assert missing["agent-1"]["review_accuracy"] is None
    assert missing["agent-1"]["peer_acceptance"] is None
    assert missing["agent-3"]["review_accuracy"] is None
    with pytest.raises(ValueError, match="duplicate peer verdict"):
        episode_metrics(original, verdicts + verdicts[:1], assignment, {}, 0.6)


@pytest.mark.parametrize("count", [3, 4, 7])
def test_random_ring_is_seeded_balanced_and_never_self_review(count):
    agents = [f"agent-{i}" for i in range(count)]
    mappings = []
    for episode in range(12):
        first = review_assignment(agents, "random_ring", 73, episode)
        assert first == review_assignment(agents, "random_ring", 73, episode)
        assert set(first) == set(agents)
        assert all(len(targets) == 1 and agent not in targets for agent, targets in first.items())
        assert Counter(target for targets in first.values() for target in targets) == Counter(agents)
        mappings.append(json.dumps(first, sort_keys=True))
    assert len(set(mappings)) > 1, "repeated episodes must not be one fixed ring"
    assert agents == [f"agent-{i}" for i in range(count)], "selection must not mutate manifest order"


async def test_random_ring_assignment_is_sent_to_service_and_saved_with_rewards(tmp_path):
    services = RatingsService()
    manifest = configured(services=peer_settings("random_ring", matching_seed=17),
                          rewards={"enabled": True, "source": "peer_verdict"})
    bundle = RunBundle.create(tmp_path / "runs")
    episodes = RewardEpisodes(manifest, services, bundle)
    task = _task(tmp_path)
    await episodes.open(task)
    assignment = services.epochs[0]["assignments"]
    assert assignment == review_assignment(AGENTS, "random_ring", 17, 0)
    services.ratings = [{"from": agent, "about": targets[0], "verdict": "accept"}
                        for agent, targets in assignment.items()]
    for agent, targets in assignment.items():
        assert f"Review these participants' work: {targets[0]}." in episodes.instructions(agent)
    await asyncio.wait_for(asyncio.gather(*(episodes.finish(agent, task, 0.0) for agent in AGENTS)), 1)
    rewards = [event for event in bundle.events() if event.kind == "learning.reward"]
    assert len(rewards) == 3 and len(services.epochs) == 1
    assert all(event.data["raw_reward"] == 1.0 for event in rewards)
    assert all(event.data["evidence"]["assignment"] == assignment for event in rewards)
    assert all(event.data["evidence"]["verifier_rewards"] == dict.fromkeys(AGENTS, 0.0) for event in rewards)


async def test_component_episode_preserves_original_scores_and_records_measured_costs(tmp_path):
    manifest = configured(rewards={"enabled": True, "source": "components", "terms": [
        {"metric": "verifier", "weight": 2}, {"metric": "completion_tokens", "weight": -1, "scale": 10}]})
    bundle = RunBundle.create(tmp_path / "runs")
    episodes = RewardEpisodes(manifest, None, bundle)
    task = _task(tmp_path)
    original = dict(zip(AGENTS, [1.0, 0.5, 0.0], strict=True))
    usage = {agent: {"model_requests": 2, "completion_tokens": 5} for agent in AGENTS}
    await complete(episodes, task, original, usage)
    recorded = {event.agent_id: event.data for event in bundle.events() if event.kind == "learning.reward"}
    assert [recorded[a]["raw_reward"] for a in AGENTS] == [1.5, 0.5, -0.5]
    assert all(row["evidence"]["verifier_rewards"] == original for row in recorded.values())
    assert all(row["evidence"]["usage"] == usage for row in recorded.values())


async def test_default_peer_feedback_does_not_disclose_original_verifier_or_peer_scores(tmp_path):
    manifest = configured(services=peer_settings(), rewards={"enabled": True, "source": "peer_verdict"},
                          learning={"mode": "in_context"})
    service = RatingsService([{"from": agent, "about": AGENTS[(i + 1) % 3], "verdict": "accept"}
                              for i, agent in enumerate(AGENTS)])
    bundle = RunBundle.create(tmp_path / "runs")
    episodes = RewardEpisodes(manifest, service, bundle)
    await complete(episodes, _task(tmp_path, "first"), dict.fromkeys(AGENTS, 0.0))
    await episodes.open(_task(tmp_path, "second"))
    for agent in AGENTS:
        text = episodes.instructions(agent)
        payload = json.loads(text.split("Previous episode feedback: ", 1)[1])
        assert payload == {"task": "first", "source": "peer_verdict", "raw_reward": 1.0, "allocated_reward": 1.0}
    assert not any(event.kind == "learning.feedback_delivered" for event in bundle.events())


async def test_feedback_fields_are_delivered_only_after_declared_delay_and_acknowledgment(tmp_path):
    manifest = configured(learning={"mode": "in_context", "feedback": {
        "fields": ["verifier"], "delay_episodes": 2}})
    bundle = RunBundle.create(tmp_path / "runs")
    episodes = RewardEpisodes(manifest, None, bundle)
    scores = dict(zip(AGENTS, [1.0, 0.5, 0.0], strict=True))
    await complete(episodes, _task(tmp_path, "first"), scores)
    second = _task(tmp_path, "second")
    await episodes.open(second)
    assert all("Previous episode feedback:" not in episodes.instructions(agent) for agent in AGENTS)
    await complete(episodes, second, dict.fromkeys(AGENTS, 0.75))
    await episodes.open(_task(tmp_path, "third"))
    for agent in AGENTS:
        payload = json.loads(episodes.instructions(agent).split("Previous episode feedback: ", 1)[1])
        assert payload == {"task": "first", "source": "verifier", "verifier": scores[agent]}
    assert not any(event.kind == "learning.feedback_delivered" for event in bundle.events())
    episodes.confirm_feedback("agent-1")
    delivered = [event for event in bundle.events() if event.kind == "learning.feedback_delivered"]
    assert len(delivered) == 1 and delivered[0].agent_id == "agent-1"
    assert delivered[0].data["weight_update"] is False
    assert len(episodes.feedback["agent-1"]) == 1 and len(episodes.feedback["agent-2"]) == 2
    assert not any(event.kind.startswith("harness.") for event in bundle.events()), "controller must add no turn"


async def test_feedback_capacity_failure_preserves_pending_evidence_without_truncating(tmp_path):
    manifest = configured(learning={"mode": "in_context", "feedback": {
        "fields": ["raw_reward", "allocated_reward", "verifier", "components"], "max_chars": 100}})
    bundle = RunBundle.create(tmp_path / "runs")
    episodes = RewardEpisodes(manifest, None, bundle)
    await complete(episodes, _task(tmp_path, "first"), dict.fromkeys(AGENTS, 1.0))
    await episodes.open(_task(tmp_path, "second"))
    for agent in AGENTS:
        with pytest.raises(ValueError, match="feedback exceeds max_chars"):
            episodes.instructions(agent)
        assert len(episodes.feedback[agent]) == 1
    assert not any(event.kind.startswith("learning.feedback_") for event in bundle.events())


async def test_reputation_uses_only_completed_original_observations_and_window(tmp_path):
    manifest = configured(rewards={"enabled": True, "source": "components",
                                   "terms": [{"metric": "verifier", "weight": -100}]},
                          reputation={"enabled": True, "window": 1, "metrics": ["verifier"]})
    bundle = RunBundle.create(tmp_path / "runs")
    episodes = RewardEpisodes(manifest, None, bundle)
    first = _task(tmp_path, "first")
    await episodes.open(first)
    for agent in AGENTS:
        assert episodes.instructions(agent).endswith("unknown): []")
    await complete(episodes, first, dict.fromkeys(AGENTS, 0.0))
    second = _task(tmp_path, "second")
    await complete(episodes, second, {"agent-1": 1.0, "agent-2": None, "agent-3": 0.5})
    await episodes.open(_task(tmp_path, "third"))
    for agent in AGENTS:
        text = episodes.instructions(agent)
        rows = json.loads(text.split("Observed participant history (missing measurements are unknown): ", 1)[1])
        assert rows == [{"participant": "agent-1", "task": "second", "verifier": 1.0},
                        {"participant": "agent-2", "task": "second", "verifier": None},
                        {"participant": "agent-3", "task": "second", "verifier": 0.5}]
        assert "allocated_reward" not in text and "raw_reward" not in text


def test_selection_starts_with_all_agents_then_round_robin_balances_actual_opportunities():
    spec = AssignmentSpec(policy="round_robin", participants=2, seed=9)
    assert select_participants(spec, AGENTS, 0, {}) == AGENTS
    selections = [select_participants(spec, AGENTS, episode, {}) for episode in range(1, 4)]
    assert all(len(set(selection)) == 2 for selection in selections)
    assert Counter(agent for selection in selections for agent in selection) == dict.fromkeys(AGENTS, 2)
    assert selections == [select_participants(spec, AGENTS, episode, {}) for episode in range(1, 4)]


def test_performance_selection_uses_only_supplied_completed_measurements_and_is_seeded_on_ties():
    spec = AssignmentSpec(policy="performance", participants=1, seed=8)
    assert select_participants(spec, AGENTS, 0, {}) == AGENTS
    past = {"agent-1": [0.9], "agent-2": [0.8], "agent-3": [0.0]}
    assert select_participants(spec, AGENTS, 1, past) == ["agent-1"]
    past["agent-1"].append(0.0)
    assert select_participants(spec, AGENTS, 2, past) == ["agent-2"]
    tied = dict.fromkeys(AGENTS, [1.0])
    assert select_participants(spec, AGENTS, 1, tied) == select_participants(spec, AGENTS, 5, tied)


@pytest.mark.parametrize("bad", [[], [None], [float("nan")], [float("inf")]])
def test_performance_selection_does_not_rank_missing_or_nonfinite_observations_as_zero(bad):
    spec = AssignmentSpec(policy="performance", participants=1)
    with pytest.raises(ValueError, match="complete measured score history"):
        select_participants(spec, AGENTS, 1, {"agent-1": [0.0], "agent-2": bad, "agent-3": [1.0]})


@pytest.mark.parametrize("changes,expected", [
    ({"rewards": {"enabled": True, "source": "components", "terms": [
        {"metric": "verifier", "agent_weights": {"unknown": 2}}]}}, "unknown agent"),
    ({"rewards": {"enabled": True, "source": "components", "terms": [
        {"metric": "verifier", "agent_weights": dict.fromkeys(AGENTS, 0)}]}}, "zero effective weight"),
    ({"rewards": {"enabled": True, "source": "peer_verdict", "success_threshold": 0.5},
      "services": peer_settings()}, "only applies to review_accuracy"),
    ({"rewards": {"enabled": True, "source": "components", "terms": [
        {"metric": "review_accuracy"}]}}, "enabled ratings peer review"),
    ({"learning": {"mode": "in_context", "feedback": {"fields": ["peer_verdicts"]}}},
     "enabled ratings peer review"),
    ({"learning": {"mode": "none", "feedback": {"delay_episodes": 2}}}, "feedback options"),
    ({"services": peer_settings("ring", matching_seed=4)}, "matching_seed"),
    ({"services": {"peer_review": {"topology": "random_ring"}}}, "random_ring"),
    ({"rewards": {"enabled": False}, "reputation": {"enabled": True}}, "synchronized reward episodes"),
    ({"reputation": {"enabled": True, "metrics": ["review_accuracy"]}}, "enabled ratings peer review"),
    ({"task_assignment": {"policy": "round_robin", "participants": 3}}, "smaller than"),
    ({"task_assignment": {"policy": "round_robin", "participants": 2},
      "rewards": {"enabled": True, "allocation": "team_mean"}}, "individual rewards"),
    ({"task_assignment": {"policy": "performance", "participants": 2},
      "services": peer_settings()}, "no peer review"),
    ({"task_assignment": {"policy": "round_robin", "participants": 2}, "mode": "persistent"},
     "isolated comparison"),
    ({"history": {"episodes": 1}, "memory": "carry"}, "memory: reset"),
])
def test_unsupported_cross_condition_combinations_are_rejected(changes, expected):
    errors = episode_condition_errors(configured(**changes))
    assert errors and any(expected in error for error in errors)


@pytest.mark.parametrize("factory,options", [
    (RewardTerm, {"metric": "verifier", "weight": 0}),
    (RewardTerm, {"metric": "verifier", "scale": 0}),
    (RewardTerm, {"metric": "verifier", "weight": float("nan")}),
    (RewardSpec, {"enabled": True, "source": "components"}),
    (RewardSpec, {"enabled": True, "source": "components", "terms": [
        {"metric": "verifier"}, {"metric": "verifier", "weight": 2}]}),
    (FeedbackSpec, {"fields": []}),
    (FeedbackSpec, {"fields": ["verifier", "verifier"]}),
    (FeedbackSpec, {"delay_episodes": 0}),
    (ReputationSpec, {"window": 2}),
    (ReputationSpec, {"enabled": True, "metrics": []}),
    (AssignmentSpec, {"policy": "performance"}),
    (AssignmentSpec, {"policy": "all", "participants": 2}),
    (AssignmentSpec, {"policy": "round_robin", "participants": 2, "score": "allocated_reward"}),
])
def test_degenerate_or_dormant_conditions_fail_schema_validation(factory, options):
    with pytest.raises(ValueError):
        factory(**options)
