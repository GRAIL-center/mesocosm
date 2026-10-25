"""Cost measurements from completed gateway requests, with missing coverage explicit."""
from __future__ import annotations

from typing import Any

from mesocosm.gateway.client import GatewayAdmin


async def request_boundary(gateway: GatewayAdmin, run: str, agent: str) -> set[str]:
    return {record["request_id"] async for record in gateway.records(run)
            if record.get("agent") == agent and isinstance(record.get("request_id"), str)}


async def measured_usage(gateway: GatewayAdmin, run: str, agent: str, before: set[str]) -> dict[str, Any]:
    records = [record async for record in gateway.records(run)
               if record.get("agent") == agent and record.get("request_id") not in before]
    admitted = {record["request_id"] for record in records if record.get("event") == "request_admitted"}
    completed = [record for record in records if record.get("event") == "chat_completion"]
    ids = [record["request_id"] for record in completed]
    valid = bool(ids) and len(ids) == len(set(ids)) and set(ids) == admitted
    valid &= all(record.get("status") == 200 for record in completed)
    tokens = [(record.get("response") or {}).get("usage", {}).get("completion_tokens")
              if isinstance((record.get("response") or {}).get("usage"), dict) else None for record in completed]
    return {"model_requests": len(ids) if valid else None,
            "completion_tokens": sum(tokens) if valid and all(type(t) is int and t >= 0 for t in tokens) else None,
            "request_ids": sorted(ids)}
