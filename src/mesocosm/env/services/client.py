"""Control-plane view of the world services: what they are enforcing right now."""

from __future__ import annotations

from typing import Any

import httpx


class ServicesAdmin:
    def __init__(self, base_url: str, api_key: str, transport: httpx.AsyncBaseTransport | None = None):
        if not api_key:
            raise ValueError("services admin requires a nonempty control key")
        self.client = httpx.AsyncClient(base_url=base_url, timeout=10.0, transport=transport,
                                       headers={"Authorization": f"Bearer {api_key}"})

    async def state(self) -> dict[str, Any]:
        r = await self.client.get("/admin/state")
        r.raise_for_status()
        return r.json()

    async def set_clock(self, started: float) -> dict[str, Any]:
        """Anchor the exposure schedule: `conditions.exposure` seconds count from `started`."""
        r = await self.client.post("/admin/clock", json={"started": started})
        r.raise_for_status()
        return r.json()

    async def set_epoch(self, epoch: str, *, assignments: dict[str, list[str]] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"epoch": epoch}
        if assignments is not None:
            payload["assignments"] = assignments
        r = await self.client.post("/admin/epoch", json=payload)
        r.raise_for_status()
        return r.json()

    async def post_feedback(self, to: str, epoch: str, text: str) -> dict[str, Any]:
        r = await self.client.post("/admin/feedback", json={"to": to, "epoch": epoch, "text": text})
        r.raise_for_status()
        return r.json()

    async def world_state(self, agent_id: str | None = None) -> dict[str, Any]:
        """The world as it stands (ADR-0028), `viewer` set to `agent_id`: what a verifier gets as world.json."""
        r = await self.client.get("/admin/state/world", params={"agent": agent_id} if agent_id else None)
        r.raise_for_status()
        return r.json()

    async def credit(self, to: str, amount: float, memo: str = "") -> dict[str, Any]:
        """Move `amount` of the ledger's unit from the bank to a member (negative: a debit)."""
        r = await self.client.post("/admin/ledger", json={"to": to, "amount": amount, "memo": memo})
        r.raise_for_status()
        return r.json()

    async def verdicts(self, epoch: str | None = None) -> list[dict[str, Any]]:
        r = await self.client.get("/admin/verdicts", params={"epoch": epoch} if epoch else None)
        r.raise_for_status()
        return r.json()["verdicts"]

    async def aclose(self) -> None:
        await self.client.aclose()

    async def publish_artifacts(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Publish a captured snapshot after the original verifier completes; retain its receipt."""
        r = await self.client.post("/admin/artifacts", json=payload)
        r.raise_for_status()
        return r.json()
