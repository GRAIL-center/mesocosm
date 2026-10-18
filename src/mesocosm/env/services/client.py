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

    async def aclose(self) -> None:
        await self.client.aclose()
