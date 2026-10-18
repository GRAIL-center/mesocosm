"""Control-plane view of the world services: what they are enforcing right now."""

from __future__ import annotations

from typing import Any

import httpx


class ServicesAdmin:
    def __init__(self, base_url: str, transport: httpx.AsyncBaseTransport | None = None):
        self.client = httpx.AsyncClient(base_url=base_url, timeout=10.0, transport=transport)

    async def state(self) -> dict[str, Any]:
        r = await self.client.get("/admin/state")
        r.raise_for_status()
        return r.json()

    async def set_clock(self, started: float) -> dict[str, Any]:
        """Anchor the exposure schedule: `conditions.exposure` seconds count from `started`."""
        r = await self.client.post("/admin/clock", json={"started": started})
        r.raise_for_status()
        return r.json()

    async def set_epoch(self, epoch: str) -> dict[str, Any]:
        r = await self.client.post("/admin/epoch", json={"epoch": epoch})
        r.raise_for_status()
        return r.json()

    async def post_feedback(self, to: str, epoch: str, text: str) -> dict[str, Any]:
        r = await self.client.post("/admin/feedback", json={"to": to, "epoch": epoch, "text": text})
        r.raise_for_status()
        return r.json()

    async def world_event(self, event: dict[str, Any]) -> dict[str, Any]:
        """Make the world act (ADR-0020): a post, a reply, a chat message, a wiki publish or update."""
        r = await self.client.post("/admin/world", json=event)
        r.raise_for_status()
        return r.json()

    async def verdicts(self, epoch: str | None = None) -> list[dict[str, Any]]:
        r = await self.client.get("/admin/verdicts", params={"epoch": epoch} if epoch else None)
        r.raise_for_status()
        return r.json()["verdicts"]

    async def aclose(self) -> None:
        await self.client.aclose()
