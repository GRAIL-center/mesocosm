"""Exact sampled token/reward boundaries. Observations never become training targets."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field, StrictInt, model_validator

from mesocosm.learning.config import PolicyIdentity, TrainingModel

Token = Annotated[StrictInt, Field(ge=0)]


class ActionRequest(TrainingModel):
    request_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    task: str = Field(min_length=1)
    episode_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    prompt_token_ids: list[Token] = Field(min_length=1)
    token_ids: list[Token] = Field(min_length=1)
    token_logprobs: list[float] = Field(min_length=1)

    @model_validator(mode="after")
    def aligned(self) -> ActionRequest:
        if len(self.token_ids) != len(self.token_logprobs):
            raise ValueError("every action token needs its actual sampling logprob")
        if any(value > 0 for value in self.token_logprobs):
            raise ValueError("log probabilities cannot be positive")
        return self


class AgentTrajectory(TrainingModel):
    agent_id: str = Field(min_length=1)
    task: str = Field(min_length=1)
    episode_id: str = Field(min_length=1)
    reward: float
    reward_source: Literal["verifier", "peer_verdict"]
    reward_evidence: dict[str, Any] = Field(default_factory=dict)
    requests: list[ActionRequest] = Field(min_length=1)

    @model_validator(mode="after")
    def boundaries(self) -> AgentTrajectory:
        for request in self.requests:
            if (request.agent_id, request.task, request.episode_id) != (self.agent_id, self.task, self.episode_id):
                raise ValueError("request does not belong to its rewarded agent/task/episode")
        return self


class RolloutBatch(TrainingModel):
    policy: PolicyIdentity
    source_run_ids: list[str] = Field(min_length=1)
    trajectories: list[AgentTrajectory] = Field(min_length=3)

    @model_validator(mode="after")
    def unique(self) -> RolloutBatch:
        if len({trajectory.agent_id for trajectory in self.trajectories}) < 3:
            raise ValueError("training requires at least three distinct real agents")
        keys = [(t.agent_id, t.task, t.episode_id) for t in self.trajectories]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate rewarded trajectory")
        request_ids = [request.request_id for t in self.trajectories for request in t.requests]
        if len(set(request_ids)) != len(request_ids):
            raise ValueError("an action request cannot be used twice in one update")
        return self

    def statistics(self, baseline: float) -> dict[str, Any]:
        rewards = [trajectory.reward for trajectory in self.trajectories]
        advantages = [reward - baseline for reward in rewards]
        return {"trajectories": len(rewards), "requests": sum(len(t.requests) for t in self.trajectories),
                "action_tokens": sum(len(r.token_ids) for t in self.trajectories for r in t.requests),
                "rewards": rewards, "baseline": baseline, "advantages": advantages,
                "constant_observed_reward": len(set(rewards)) == 1,
                "performance_improvement_demonstrated": False}
