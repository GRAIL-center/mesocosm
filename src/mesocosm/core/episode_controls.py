"""Measured episode conditions, with no authored agent behavior or invented histories."""
from __future__ import annotations

import math
import random
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ControlModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


Metric = Literal["verifier", "peer_acceptance", "review_accuracy", "model_requests", "completion_tokens"]


class RewardTerm(ControlModel):
    metric: Metric
    weight: float = 1.0
    scale: float = Field(default=1.0, gt=0)
    agent_weights: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def effective(self) -> RewardTerm:
        if self.weight == 0 and not any(self.agent_weights.values()):
            raise ValueError("reward term has no nonzero weight")
        return self


class FeedbackSpec(ControlModel):
    fields: list[Literal["raw_reward", "allocated_reward", "verifier", "peer_verdicts",
                         "review_accuracy", "components"]] = Field(
                             default=["raw_reward", "allocated_reward"])
    delay_episodes: int = Field(default=1, ge=1)
    max_chars: int = Field(default=60000, ge=100)

    @model_validator(mode="after")
    def unique(self) -> FeedbackSpec:
        if not self.fields or len(set(self.fields)) != len(self.fields):
            raise ValueError("feedback fields must be nonempty and unique")
        return self


class ReputationSpec(ControlModel):
    enabled: bool = False
    window: int = Field(default=3, ge=1)
    metrics: list[Literal["verifier", "review_accuracy"]] = Field(default=["verifier"])

    @model_validator(mode="after")
    def effective(self) -> ReputationSpec:
        if not self.metrics or len(set(self.metrics)) != len(self.metrics):
            raise ValueError("reputation metrics must be nonempty and unique")
        if not self.enabled and (self.window != 3 or self.metrics != ["verifier"]):
            raise ValueError("reputation options require reputation.enabled")
        return self


class AssignmentSpec(ControlModel):
    policy: Literal["all", "round_robin", "performance"] = "all"
    participants: int | None = Field(default=None, ge=1)
    score: Literal["verifier", "allocated_reward"] = "verifier"
    seed: int = 0

    @model_validator(mode="after")
    def effective(self) -> AssignmentSpec:
        if self.policy == "all" and (self.participants is not None or self.seed != 0 or self.score != "verifier"):
            raise ValueError("assignment options require a selective policy")
        if self.policy != "all" and self.participants is None:
            raise ValueError("selective assignment requires participants")
        if self.policy != "performance" and self.score != "verifier":
            raise ValueError("assignment.score only applies to performance")
        return self


def review_assignment(agents: list[str], topology: str, seed: int, episode: int) -> dict[str, list[str]]:
    order = list(agents)
    if topology == "random_ring":
        random.Random(f"review:{seed}:{episode}").shuffle(order)
    if topology in {"ring", "random_ring"}:
        return {a: [order[(i + 1) % len(order)]] for i, a in enumerate(order)} if len(order) > 1 else {}
    if topology != "all":
        raise ValueError(f"unknown review topology: {topology}")
    return {a: [b for b in agents if b != a] for a in agents}


def select_participants(spec: AssignmentSpec, agents: list[str], episode: int,
                        history: dict[str, list[float | None]]) -> list[str]:
    """First task measures every participant; later selections use only completed observations."""
    if spec.policy == "all" or episode == 0:
        return list(agents)
    count = spec.participants
    if count is None or count >= len(agents):
        raise ValueError("selective assignment requires participants smaller than the agent population")
    order = list(agents)
    random.Random(f"assignment:{spec.seed}").shuffle(order)
    if spec.policy == "round_robin":
        start = ((episode - 1) * count) % len(order)
        return [order[(start + i) % len(order)] for i in range(count)]
    scores = {}
    for agent in agents:
        values = history.get(agent, [])
        if not values or any(v is None or not math.isfinite(v) for v in values):
            raise ValueError("performance assignment requires complete measured score history")
        scores[agent] = sum(v for v in values if v is not None) / len(values)
    order.sort(key=lambda agent: scores[agent], reverse=True)
    return order[:count]


def episode_metrics(verifier: dict[str, float | None], verdicts: list[dict[str, Any]],
                    assignment: dict[str, list[str]], usage: dict[str, dict[str, Any]],
                    success_threshold: float) -> dict[str, dict[str, float | None]]:
    """Review accuracy is thresholded original outcome agreement, not a misconduct verdict."""
    given = {}
    for verdict in verdicts:
        key = (verdict.get("from"), verdict.get("about"))
        if key in given:
            raise ValueError("duplicate peer verdict")
        given[key] = verdict.get("verdict")
    result = {}
    for agent, score in verifier.items():
        received = [given.get((peer, agent)) for peer in assignment if agent in assignment[peer]]
        judged = assignment.get(agent, [])
        correctness = []
        for peer in judged:
            value, truth = given.get((agent, peer)), verifier.get(peer)
            correctness.append(float((value == "accept") == (truth >= success_threshold))
                               if value in {"accept", "reject"} and truth is not None else None)
        result[agent] = {
            "verifier": score,
            "peer_acceptance": (sum(v == "accept" for v in received) / len(received)
                                if received and all(v in {"accept", "reject"} for v in received) else None),
            "review_accuracy": (sum(v for v in correctness if v is not None) / len(correctness)
                                if correctness and None not in correctness else None),
            "model_requests": usage.get(agent, {}).get("model_requests"),
            "completion_tokens": usage.get(agent, {}).get("completion_tokens"),
        }
    return result


def component_rewards(terms: list[RewardTerm], metrics: dict[str, dict[str, float | None]]) -> dict[str, float | None]:
    result: dict[str, float | None] = {}
    for agent, values in metrics.items():
        total: float | None = 0.0
        for term in terms:
            weight = term.agent_weights.get(agent, term.weight)
            if weight == 0:
                continue
            value = values.get(term.metric)
            if value is None:
                total = None
                break
            if not math.isfinite(value):
                raise ValueError("nonfinite component measurement")
            assert total is not None
            total += weight * value / term.scale
        if total is not None and not math.isfinite(total):
            raise ValueError("nonfinite component reward")
        result[agent] = total
    return result
