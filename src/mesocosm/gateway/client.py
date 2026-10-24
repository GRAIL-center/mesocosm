"""Control-plane client for the gateway's admin endpoints."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from mesocosm.core.receipts import Receipt
from mesocosm.learning.config import PolicyIdentity


class GatewayAdmin:
    def __init__(self, base_url: str, transport: httpx.AsyncBaseTransport | None = None,
                 api_key: str = "mesocosm-local"):
        self.client = httpx.AsyncClient(base_url=base_url, timeout=30.0, transport=transport,
                                        headers={"Authorization": f"Bearer {api_key}"})

    async def health(self) -> dict[str, Any]:
        r = await self.client.get("/admin/health")
        r.raise_for_status()
        return r.json()

    async def set_budget(self, agent_id: str, budget: dict[str, int], run_id: str | None = None) -> Receipt:
        r = await self.client.post("/admin/budget", json={"agent_id": agent_id, "budget": budget, "run_id": run_id})
        r.raise_for_status()
        return Receipt.model_validate(r.json())

    async def _control(self, verb: str, **payload: Any) -> Receipt:
        r = await self.client.post(f"/admin/{verb}", json=payload)
        r.raise_for_status()
        return Receipt.model_validate(r.json())

    async def pause(self, run: str, agent_id: str | None = None, reason: str | None = None,
                    run_id: str | None = None) -> Receipt:
        return await self._control("pause", run=run, agent_id=agent_id, reason=reason, run_id=run_id)

    async def resume(self, run: str, agent_id: str | None = None, run_id: str | None = None) -> Receipt:
        return await self._control("resume", run=run, agent_id=agent_id, run_id=run_id)

    async def stop(self, run: str, run_id: str | None = None) -> Receipt:
        return await self._control("stop", run=run, run_id=run_id)

    async def usage(self) -> dict[str, Any]:
        r = await self.client.get("/admin/usage")
        r.raise_for_status()
        return r.json()

    async def terminal_refusal(self, run: str, agent_id: str, *, after: int = 0) -> dict[str, Any]:
        """Small authenticated denial receipt; reaching a quota without a rejected call is not terminal."""
        r = await self.client.get("/admin/terminal-refusal", params={"run": run, "agent_id": agent_id, "after": after})
        r.raise_for_status()
        return r.json()

    async def bind_rollout(self, agent_id: str, run: str, episode_id: str, task: str, round: int,
                           session_id: str, policy: PolicyIdentity, run_id: str | None = None) -> Receipt:
        return await self._control("rollout/bind", agent_id=agent_id, run=run, episode_id=episode_id,
                                   task=task, round=round, session_id=session_id,
                                   policy=policy.model_dump(mode="json"), run_id=run_id)

    async def unbind_rollout(self, agent_id: str, run: str, episode_id: str, session_id: str,
                             run_id: str | None = None) -> Receipt:
        return await self._control("rollout/unbind", agent_id=agent_id, run=run,
                                   episode_id=episode_id, session_id=session_id, run_id=run_id)

    async def records(self, run: str, since: float | None = None) -> AsyncIterator[dict[str, Any]]:
        """This run's exposure records, streamed one by one (NDJSON)."""
        params: dict[str, Any] = {"run": run}
        if since is not None:
            params["since"] = since
        async with self.client.stream("GET", "/admin/records", params=params) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if line.strip():
                    yield json.loads(line)

    async def aclose(self) -> None:
        await self.client.aclose()
