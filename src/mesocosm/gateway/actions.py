"""Single-use approvals for the supported OpenCode before-tool plugin.

The agent key identifies the caller. Only the controller binds a harness session to an episode
or resolves a pending action. Unknown, expired, disconnected and replayed requests never allow tools.
This is a tool-call boundary, not mediation of subprocesses or syscalls inside an approved tool.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

PROTOCOL = "tool-gate-v1"
MAX_ACTION_BYTES = 1_000_000


class ActionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instance_id: str = Field(min_length=1, max_length=100)
    nonce: str = Field(min_length=1, max_length=100)
    session_id: str = Field(min_length=1, max_length=200)
    call_id: str = Field(min_length=1, max_length=200)
    tool: str = Field(min_length=1, max_length=200)
    args: dict[str, Any]


class ReadyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instance_id: str = Field(min_length=1, max_length=100)
    protocol: Literal["tool-gate-v1"]
    plugin_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class BindBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run: str
    agent_id: str
    session_id: str
    task: str
    plugin_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    timeout_seconds: float = Field(default=30, gt=0, le=600)


class DecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    binding_id: str
    decision: Literal["allow", "deny"]
    reason: str = Field(min_length=1, max_length=2000)


@dataclass
class PendingAction:
    data: dict[str, Any]
    deadline: float
    event: asyncio.Event = field(default_factory=asyncio.Event)
    decision: Literal["allow", "deny"] | None = None
    reason: str | None = None


def register_action_routes(app: FastAPI, *, identity_callback: Callable[[Request], tuple[str, str, str]],
                           log_callback: Callable[[dict[str, Any]], None], control_key: str) -> None:
    """Register before the gateway's catch-all route. State lives only in this gateway process."""
    ready: dict[tuple[str, str], dict[str, Any]] = {}
    bindings: dict[tuple[str, str, str], dict[str, Any]] = {}
    pending: dict[str, PendingAction] = {}
    failures: dict[str, list[dict[str, Any]]] = {}
    used: set[tuple[str, str, str, str]] = set()

    def log(event: str, **data: Any) -> None:
        log_callback({"ts": time.time(), "event": event, **data})

    def controller(request: Request) -> None:
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {control_key}":
            raise HTTPException(404, "Not Found")

    def actor(request: Request) -> tuple[str, str]:
        agent, run, how = identity_callback(request)
        if how != "key" or agent == "unknown" or run == "unknown":
            raise HTTPException(401, "Invalid API key")
        return run, agent

    def resolve(item: PendingAction, decision: Literal["allow", "deny"], reason: str,
                *, source: Literal["controller", "gateway"] = "gateway") -> None:
        if item.decision is not None:
            return
        item.decision, item.reason = decision, reason
        result = {**item.data, "decision": decision, "reason": reason, "source": source}
        if source == "gateway":
            failures[item.data["binding_id"]].append(result)
        log("action.decision", **result,
            held_ms=round((time.time() - item.data["created_at"]) * 1000))
        item.event.set()

    @app.post("/v1/actions/ready")
    async def plugin_ready(body: ReadyBody, request: Request) -> dict[str, Any]:
        run, agent = actor(request)
        ready[run, agent] = body.model_dump()
        # A restarted harness invalidates any previous in-flight actions and session bindings.
        for key, binding in list(bindings.items()):
            if key[:2] == (run, agent) and binding["instance_id"] != body.instance_id:
                del bindings[key]
                for item in pending.values():
                    if item.data["binding_id"] == binding["binding_id"]:
                        resolve(item, "deny", "harness_instance_replaced")
        log("action.plugin_ready", run=run, agent=agent, **body.model_dump())
        return {"ok": True, "protocol": PROTOCOL}

    @app.post("/admin/actions/bind")
    async def bind(body: BindBody, request: Request) -> dict[str, Any]:
        controller(request)
        plugin = ready.get((body.run, body.agent_id))
        if plugin is None or plugin["plugin_sha256"] != body.plugin_sha256:
            raise HTTPException(409, "rejected_unsupported: expected tool hook is not ready")
        key = body.run, body.agent_id, body.session_id
        if key in bindings:
            raise HTTPException(409, "session already bound")
        binding = {**body.model_dump(), "binding_id": f"bind_{uuid.uuid4().hex}",
                   "instance_id": plugin["instance_id"], "protocol": PROTOCOL}
        bindings[key] = binding
        failures[binding["binding_id"]] = []
        log("action.bound", run=body.run, agent=body.agent_id, **{k: v for k, v in binding.items()
                                                               if k not in {"run", "agent_id"}})
        return binding

    @app.delete("/admin/actions/bind/{binding_id}")
    async def unbind(binding_id: str, request: Request) -> dict[str, Any]:
        controller(request)
        found = False
        for key, binding in list(bindings.items()):
            if binding["binding_id"] == binding_id:
                del bindings[key]
                found = True
        for item in pending.values():
            if item.data["binding_id"] == binding_id:
                resolve(item, "deny", "session_closed")
        failures.pop(binding_id, None)
        return {"closed": found}

    @app.get("/admin/actions")
    async def list_pending(request: Request, binding_id: str) -> dict[str, Any]:
        controller(request)
        if binding_id not in failures:
            raise HTTPException(404, "unknown action binding")
        return {"binding_id": binding_id,
                "active": any(binding["binding_id"] == binding_id for binding in bindings.values()),
                "failures": failures[binding_id],
                "pending": [item.data for item in pending.values()
                            if item.data["binding_id"] == binding_id and item.decision is None]}

    @app.post("/admin/actions/{action_id}/decision")
    async def decide(action_id: str, body: DecisionBody, request: Request) -> dict[str, Any]:
        controller(request)
        item = pending.get(action_id)
        if item is None or item.data["binding_id"] != body.binding_id or item.decision is not None:
            raise HTTPException(409, "stale or already resolved action")
        if time.monotonic() >= item.deadline:
            resolve(item, "deny", "approval_timeout")
            raise HTTPException(409, "approval expired")
        resolve(item, body.decision, body.reason, source="controller")
        return {"action_id": action_id, "decision": item.decision, "binding_id": body.binding_id}

    @app.post("/v1/actions/authorize")
    async def authorize(body: ActionBody, request: Request) -> dict[str, Any]:
        run, agent = actor(request)
        binding = bindings.get((run, agent, body.session_id))
        if binding is None:
            log("action.rejected", run=run, agent=agent, reason="unbound_or_stale_session",
                session_id=body.session_id, call_id=body.call_id)
            raise HTTPException(403, "Action approval unavailable")
        encoded = json.dumps(body.args, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        encoded_bytes = encoded.encode()

        def reject(reason: str, status: int, detail: str) -> None:
            failure = {**body.model_dump(exclude={"args"}), "action_id": f"act_{uuid.uuid4().hex}",
                       "run": run, "agent": agent, "binding_id": binding["binding_id"], "task": binding["task"],
                       "args_sha256": hashlib.sha256(encoded_bytes).hexdigest(), "args_bytes": len(encoded_bytes),
                       "decision": "deny", "reason": reason, "source": "gateway", "identified_by": "key"}
            failures[binding["binding_id"]].append(failure)
            log("action.rejected", **failure)
            raise HTTPException(status, detail)

        if binding["instance_id"] != body.instance_id:
            reject("unbound_or_stale_session", 403, "Action approval unavailable")
        if len(encoded_bytes) > MAX_ACTION_BYTES:
            reject("action_input_exceeds_limit", 413, "Action input exceeds limit")
        replay_key = run, agent, body.session_id, body.call_id
        if replay_key in used:
            reject("action_replayed", 409, "Action has already been submitted")
        used.add(replay_key)
        action_id = f"act_{uuid.uuid4().hex}"
        data = {**body.model_dump(), "action_id": action_id, "run": run, "agent": agent,
                "binding_id": binding["binding_id"], "task": binding["task"], "created_at": time.time(),
                "args_sha256": hashlib.sha256(encoded_bytes).hexdigest(), "identified_by": "key"}
        item = PendingAction(data, time.monotonic() + binding["timeout_seconds"])
        pending[action_id] = item
        log("action.held", **data)
        try:
            while not item.event.is_set():
                remaining = item.deadline - time.monotonic()
                if remaining <= 0:
                    resolve(item, "deny", "approval_timeout")
                    break
                if await request.is_disconnected():
                    resolve(item, "deny", "client_disconnected")
                    break
                try:
                    await asyncio.wait_for(item.event.wait(), timeout=min(0.1, remaining))
                except TimeoutError:
                    pass
            return {"allow": item.decision == "allow", "reason": item.reason, "nonce": body.nonce,
                    "session_id": body.session_id, "call_id": body.call_id, "action_id": action_id}
        except asyncio.CancelledError:
            resolve(item, "deny", "request_cancelled")
            raise
        finally:
            pending.pop(action_id, None)
