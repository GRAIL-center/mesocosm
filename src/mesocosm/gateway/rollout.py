"""Control-owned rollout identities and the exact sampling contract used by training."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, HTTPException
from pydantic import Field

from mesocosm.core.receipts import EnforcedAt, Receipt, Verification
from mesocosm.learning.config import PolicyIdentity, TrainingModel

if TYPE_CHECKING:
    from mesocosm.gateway.app import Gateway

SAMPLING = {"temperature": 1.0, "top_p": 1.0, "top_k": -1, "min_p": 0.0,
            "repetition_penalty": 1.0, "presence_penalty": 0.0, "frequency_penalty": 0.0,
            "n": 1, "logprobs": True, "top_logprobs": 0, "return_token_ids": True}
# Unknown generation extensions fail closed: otherwise a new logits processor could silently
# change the distribution whose log probabilities the trainer later recomputes.
INPUT_FIELDS = {"model", "messages", "tools", "tool_choice", "parallel_tool_calls", "stream",
                "stream_options", "max_tokens", "max_completion_tokens", "user", "metadata", "seed"}


class BindRollout(TrainingModel):
    agent_id: str = Field(min_length=1)
    run: str = Field(min_length=1)
    episode_id: str = Field(min_length=1)
    task: str = Field(min_length=1)
    round: int = Field(ge=1)
    session_id: str = Field(min_length=1)
    policy: PolicyIdentity
    run_id: str | None = None

    def context(self, binding_id: str) -> dict[str, Any]:
        return {"binding_id": binding_id,
                **self.model_dump(mode="json", exclude={"agent_id", "run", "run_id"})}


class UnbindRollout(TrainingModel):
    agent_id: str = Field(min_length=1)
    run: str = Field(min_length=1)
    episode_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    run_id: str | None = None


def sampling_request(body: dict[str, Any], policy: PolicyIdentity, kind: str) -> dict[str, Any]:
    if kind != "vllm" or body.get("model") != policy.model:
        raise ValueError("rollouts require the bound vLLM policy model")
    unsupported = set(body) - INPUT_FIELDS - SAMPLING.keys()
    if unsupported:
        raise ValueError(f"unsupported training request fields: {sorted(unsupported)}")
    if body.get("tool_choice", "auto") != "auto":
        raise ValueError("training requires automatic tool choice")
    return {**body, **SAMPLING}


def register_rollout_routes(app: FastAPI, gateway: Gateway) -> None:
    def available(agent_id: str, run: str) -> None:
        if not any(identity == {"agent": agent_id, "run": run} for identity in gateway.settings.agent_keys.values()):
            raise HTTPException(422, "rollout agent/run must match a configured agent key")
        lock = gateway.request_locks.get(agent_id)
        if lock is not None and lock.locked():
            raise HTTPException(409, "cannot change a rollout binding while a request lease is active")

    @app.post("/admin/rollout/bind")
    async def bind(req: BindRollout) -> dict[str, Any]:
        available(req.agent_id, req.run)
        key = (req.run, req.agent_id)
        if key in gateway.rollout_bindings:
            raise HTTPException(409, "unbind the previous rollout before binding another")
        if gateway.route(req.policy.model).kind != "vllm":
            raise HTTPException(422, "training requires a vLLM upstream")
        enforced = EnforcedAt(component="gateway")
        context = req.context(enforced.event_id)
        gateway.rollout_bindings[key] = context
        receipt = Receipt(op="rollout.bind", run_id=req.run_id, requested=req.model_dump(mode="json"),
                          enforced_at=enforced, result="applied",
                          verification=Verification(method="gateway_rollout_binding", ok=True, detail=context))
        gateway.log({"ts": time.time(), "event": "rollout.bound", "run": req.run, "agent": req.agent_id,
                     "rollout": context, "receipt": receipt.model_dump(mode="json")})
        return receipt.model_dump(mode="json")

    @app.post("/admin/rollout/unbind")
    async def unbind(req: UnbindRollout) -> dict[str, Any]:
        available(req.agent_id, req.run)
        key = (req.run, req.agent_id)
        context = gateway.rollout_bindings.get(key)
        if context is None or (context["episode_id"], context["session_id"]) != (req.episode_id, req.session_id):
            raise HTTPException(409, "rollout binding does not match the requested episode/session")
        del gateway.rollout_bindings[key]
        receipt = Receipt(op="rollout.unbind", run_id=req.run_id, requested=req.model_dump(mode="json"),
                          enforced_at=EnforcedAt(component="gateway"), result="applied",
                          verification=Verification(method="gateway_rollout_binding", ok=True, detail=context))
        gateway.log({"ts": time.time(), "event": "rollout.unbound", "run": req.run, "agent": req.agent_id,
                     "rollout": context, "receipt": receipt.model_dump(mode="json")})
        return receipt.model_dump(mode="json")
