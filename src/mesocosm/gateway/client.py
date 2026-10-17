"""Control-plane client for the gateway's admin endpoints."""

from __future__ import annotations

from typing import Any

import httpx

from mesocosm.core.receipts import Receipt


class GatewayAdmin:
    def __init__(self, base_url: str, transport: httpx.AsyncBaseTransport | None = None):
        self.client = httpx.AsyncClient(base_url=base_url, timeout=30.0, transport=transport)

    async def health(self) -> dict[str, Any]:
        r = await self.client.get("/mesocosm/health")
        r.raise_for_status()
        return r.json()

    async def set_budget(self, agent_id: str, budget: dict[str, int], run_id: str | None = None) -> Receipt:
        r = await self.client.post("/mesocosm/budget", json={"agent_id": agent_id, "budget": budget, "run_id": run_id})
        r.raise_for_status()
        return Receipt.model_validate(r.json())

    async def usage(self) -> dict[str, Any]:
        r = await self.client.get("/mesocosm/usage")
        r.raise_for_status()
        return r.json()

    async def records(self, run: str) -> list[dict[str, Any]]:
        r = await self.client.get("/mesocosm/records", params={"run": run})
        r.raise_for_status()
        return r.json()

    async def aclose(self) -> None:
        await self.client.aclose()
