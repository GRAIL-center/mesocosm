"""Controller client for single-use before-tool approvals; no gateway-server dependencies."""
from __future__ import annotations

from typing import Any, Literal

import httpx

PROTOCOL = "tool-gate-v1"


class ActionGateFailure(RuntimeError):
    """The binding failed independently of the controller's assessments."""
    def __init__(self, message: str, failures: list[dict[str, Any]]):
        super().__init__(message)
        self.failures = failures


class ActionAdmin:
    """Uses the existing authenticated GatewayAdmin HTTP client; it does not own its lifetime."""
    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def bind(self, *, run: str, agent_id: str, session_id: str, task: str,
                   plugin_sha256: str, timeout_seconds: float) -> dict[str, Any]:
        response = await self.client.post("/admin/actions/bind", json={"run": run, "agent_id": agent_id,
            "session_id": session_id, "task": task, "plugin_sha256": plugin_sha256,
            "timeout_seconds": timeout_seconds})
        response.raise_for_status()
        result = response.json()
        expected = {"run": run, "agent_id": agent_id, "session_id": session_id, "task": task,
                    "plugin_sha256": plugin_sha256, "protocol": PROTOCOL}
        if not isinstance(result, dict) or any(result.get(key) != value for key, value in expected.items()) \
                or not all(isinstance(result.get(key), str) and result[key] for key in ("binding_id", "instance_id")):
            raise ValueError("invalid action binding acknowledgement")
        return dict(result)

    async def pending(self, binding_id: str) -> list[dict[str, Any]]:
        response = await self.client.get("/admin/actions", params={"binding_id": binding_id})
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict) or result.get("binding_id") != binding_id \
                or type(result.get("active")) is not bool \
                or not all(isinstance(result.get(key), list)
                           and all(isinstance(item, dict) for item in result[key]) for key in ("pending", "failures")):
            raise ValueError("invalid action binding status response")
        if result["failures"] or not result["active"]:
            raise ActionGateFailure("action binding has automatic failures or is no longer active", result["failures"])
        return list(result["pending"])

    async def decide(self, action_id: str, binding_id: str, decision: Literal["allow", "deny"],
                     reason: str) -> dict[str, Any]:
        response = await self.client.post(f"/admin/actions/{action_id}/decision", json={
            "binding_id": binding_id, "decision": decision, "reason": reason})
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict) or result.get("action_id") != action_id \
                or result.get("binding_id") != binding_id or result.get("decision") != decision:
            raise ValueError("invalid single-use decision acknowledgement")
        return dict(result)

    async def unbind(self, binding_id: str) -> None:
        response = await self.client.delete(f"/admin/actions/bind/{binding_id}")
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict) or type(result.get("closed")) is not bool:
            raise ValueError("invalid action close acknowledgement")
