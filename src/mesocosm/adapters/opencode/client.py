"""OpenCode harness adapter (ADR-0002): drive `opencode serve` over HTTP, observe its event stream.

Endpoints used (OpenCode server docs): GET /global/health, GET /config/providers, POST /session,
POST /session/{id}/message, GET /session/{id}/message, GET /event (SSE).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from mesocosm.core.records import Event


class OpenCodeClient:
    def __init__(self, base_url: str, *, auth: tuple[str, str] | None = None, timeout: float = 900.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = base_url
        self.client = httpx.AsyncClient(base_url=base_url, timeout=httpx.Timeout(timeout, connect=10.0),
                                        auth=auth, transport=transport)

    async def health(self) -> dict[str, Any]:
        r = await self.client.get("/global/health")
        r.raise_for_status()
        return r.json()

    async def providers(self) -> dict[str, Any]:
        r = await self.client.get("/config/providers")
        r.raise_for_status()
        return r.json()

    async def create_session(self, title: str) -> str:
        r = await self.client.post("/session", json={"title": title})
        r.raise_for_status()
        return r.json()["id"]

    async def send_message(self, session_id: str, text: str, *, provider_id: str, model_id: str) -> dict[str, Any]:
        """Send a prompt and wait for the assistant's reply ({info, parts})."""
        r = await self.client.post(f"/session/{session_id}/message", json={
            "model": {"providerID": provider_id, "modelID": model_id},
            "parts": [{"type": "text", "text": text}],
        })
        r.raise_for_status()
        return r.json()

    async def list_messages(self, session_id: str) -> list[dict[str, Any]]:
        r = await self.client.get(f"/session/{session_id}/message")
        r.raise_for_status()
        return r.json()

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        """Yield parsed `data:` payloads from GET /event until the stream ends or the task is cancelled.

        The real server's stream never ends; the caller cancels the consuming task."""
        async with self.client.stream("GET", "/event") as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data:
                    continue
                try:
                    yield json.loads(data)
                except json.JSONDecodeError:
                    yield {"type": "unparsed", "raw": data}

    async def aclose(self) -> None:
        await self.client.aclose()


def event_session_id(raw: dict[str, Any]) -> str | None:
    props = raw.get("properties") or {}
    if props.get("sessionID"):
        return props["sessionID"]
    for key in ("info", "part"):
        holder = props.get(key)
        if isinstance(holder, dict) and holder.get("sessionID"):
            return holder["sessionID"]
    session = props.get("session")
    if isinstance(session, dict) and session.get("id"):
        return session["id"]
    return None


def normalize_event(raw: dict[str, Any], *, run_id: str, session_to_agent: dict[str, str],
                    step: int | None = None) -> Event:
    """Map an OpenCode bus event to the common schema. The raw event is kept verbatim.

    The event vocabulary is not pinned by OpenCode's docs; unknown types become
    `opencode.<type>` with whatever summary fields are present. The matrix row for this
    adapter is `unverified` until the spike records real events.
    """
    etype = raw.get("type") or "unknown"
    props = raw.get("properties") or {}
    sid = event_session_id(raw)
    summary: dict[str, Any] = {}
    part = props.get("part")
    if isinstance(part, dict):
        summary["part_type"] = part.get("type")
        if part.get("type") == "tool":
            summary["tool"] = part.get("tool")
            state = part.get("state") or {}
            summary["tool_status"] = state.get("status")
        if part.get("type") == "text" and part.get("text"):
            summary["text_len"] = len(part["text"])
    if "status" in props:
        summary["status"] = props["status"]
    return Event(run_id=run_id, kind=f"opencode.{etype}", agent_id=session_to_agent.get(sid) if sid else None,
                 step=step, data={"session_id": sid, "summary": summary, "raw": raw})


def render_opencode_config(*, agent_id: str, run_id: str, gateway_url: str, model_id: str, api_key: str,
                           context_limit: int = 32768, output_limit: int = 8192) -> dict[str, Any]:
    """The opencode.json an agent container runs with: all model traffic goes to the gateway,
    tagged with the agent's identity (ADR-0003). spike/docker/agent-entrypoint.sh renders the same."""
    return {
        "$schema": "https://opencode.ai/config.json",
        "model": f"mesocosm/{model_id}",
        "small_model": f"mesocosm/{model_id}",
        "permission": "allow",
        "provider": {
            "mesocosm": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Mesocosm gateway",
                "options": {
                    "baseURL": gateway_url.rstrip("/") + "/v1",
                    "apiKey": api_key,
                    "headers": {"X-Mesocosm-Agent": agent_id, "X-Mesocosm-Run": run_id},
                },
                "models": {model_id: {"name": f"{model_id} via gateway",
                                      "limit": {"context": context_limit, "output": output_limit}}},
            }
        },
    }
