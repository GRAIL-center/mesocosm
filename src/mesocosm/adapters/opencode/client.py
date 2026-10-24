"""OpenCode harness adapter (ADR-0002): drive `opencode serve` over HTTP, observe its event stream.

Endpoints used (OpenCode server docs): GET /global/health, GET /config/providers, POST /session,
POST /session/{id}/message, POST /session/{id}/abort, GET /session/{id}/message, GET /event (SSE).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from mesocosm.adapters.opencode.trajectory import TaskTurn, send_turn
from mesocosm.core.records import Event

CONTEXT_CONFIG = '{"compaction":{"auto":false,"prune":false}}'


class OpenCodeClient:
    def __init__(self, base_url: str, *, auth: tuple[str, str] | None = None, timeout: float | None = 900.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = base_url
        self.client = httpx.AsyncClient(base_url=base_url, timeout=httpx.Timeout(timeout, connect=10.0),
                                        auth=auth, transport=transport)

    async def health(self) -> dict[str, Any]:
        r = await self.client.get("/global/health")
        r.raise_for_status()
        return r.json()

    async def prepare_action_hook(self) -> dict[str, Any]:
        """Initialize supported plugins before any prompt, and reject unverified harness versions.

        Tool discovery loads the official plugin registry. Gateway readiness independently checks
        the mounted plugin's actual file digest and binds its process instance to the session.
        """
        health = await self.health()
        if health.get("healthy") is not True or health.get("version") != "1.19.3":
            raise ValueError("rejected_unsupported: before-tool intervention requires OpenCode 1.19.3")
        response = await self.client.get("/experimental/tool/ids")
        response.raise_for_status()
        ids = response.json()
        if not isinstance(ids, list) or not all(isinstance(item, str) for item in ids):
            raise ValueError("rejected_unsupported: harness tool discovery is unavailable")
        return {"version": health["version"], "tool_ids": ids, "hook": "tool.execute.before"}

    async def providers(self) -> dict[str, Any]:
        r = await self.client.get("/config/providers")
        r.raise_for_status()
        return r.json()

    async def create_session(self, title: str) -> str:
        r = await self.client.post("/session", json={"title": title})
        r.raise_for_status()
        return r.json()["id"]

    async def send_message(self, session_id: str, text: str, *, provider_id: str, model_id: str) -> dict[str, Any]:
        """Return the raw final HTTP reply ({info, parts}); use send_turn for the whole report."""
        r = await self.client.post(f"/session/{session_id}/message", json={
            "model": {"providerID": provider_id, "modelID": model_id},
            "parts": [{"type": "text", "text": text}],
        })
        r.raise_for_status()
        return r.json()

    async def send_turn(self, session_id: str, text: str, *, provider_id: str, model_id: str) -> TaskTurn:
        return await send_turn(self, session_id, text, provider_id=provider_id, model_id=model_id)

    async def list_messages(self, session_id: str) -> list[dict[str, Any]]:
        r = await self.client.get(f"/session/{session_id}/message")
        r.raise_for_status()
        return r.json()

    async def abort_session(self, session_id: str) -> bool:
        """Ask the server to abort its running turn; closing the message HTTP request is insufficient."""
        r = await self.client.post(f"/session/{session_id}/abort")
        r.raise_for_status()
        return r.json() is True

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


def render_opencode_config(*, gateway_url: str, model_id: str, api_key: str, provider_id: str = "internal",
                           provider_name: str = "Internal LLM API", agent_id: str | None = None,
                           run_id: str | None = None, context_limit: int = 32768,
                           output_limit: int = 8192, tools: dict[str, bool] | None = None) -> dict[str, Any]:
    """The opencode.json an agent runs with: all model traffic goes to the gateway (ADR-0003). A sandbox agent
    is identified by its own `api_key` (ADR-0019); a harness the control plane drives directly may instead
    name itself with `agent_id` / `run_id`, which become identity headers."""
    options: dict[str, Any] = {"baseURL": gateway_url.rstrip("/") + "/v1", "apiKey": api_key}
    if agent_id or run_id:
        options["headers"] = {"X-Mesocosm-Agent": agent_id or "unknown", "X-Mesocosm-Run": run_id or "unknown"}
    config: dict[str, Any] = {
        "$schema": "https://opencode.ai/config.json",
        "model": f"{provider_id}/{model_id}",
        "small_model": f"{provider_id}/{model_id}",
        "permission": "allow",
        # Automatic summaries can consume tool results before the working agent sees them.
        # Preserve the original history; a full context must end explicitly, not silently rewrite it.
        **json.loads(CONTEXT_CONFIG),
    }
    if tools:
        config["tools"] = dict(tools)         # OpenCode's documented `tools` option: built-ins switched off
    config["provider"] = {
        provider_id: {
            "npm": "@ai-sdk/openai-compatible",
            "name": provider_name,
            "options": options,
            "models": {model_id: {"name": model_id, "limit": {"context": context_limit, "output": output_limit}}},
        }
    }
    return config
