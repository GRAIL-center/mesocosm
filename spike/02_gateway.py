#!/usr/bin/env python3
"""Mesocosm model gateway, spike version.

An OpenAI-compatible reverse proxy that sits between a harness (OpenCode) and vLLM.

What it does (ADR-0003):
  * stamps every request with X-Mesocosm-Request-Id and reads X-Mesocosm-Agent /
    X-Mesocosm-Run from headers that the harness config sets per agent;
  * logs the full request and the full response to <log_dir>/gateway.jsonl
    (the "actual information exposure" record);
  * enforces per-agent budgets and answers an explicit 429 when one is exhausted
    (a recorded event, and the enforcement point for resource conditions);
  * optionally asks vLLM for prompt/completion token ids (exact token sequences).

Run on the host:
    MESOCOSM_UPSTREAM=http://127.0.0.1:8000 uvicorn 02_gateway:app --host 127.0.0.1 --port 8080
or:
    python 02_gateway.py

Environment:
    MESOCOSM_UPSTREAM            vLLM base URL (default http://127.0.0.1:8000)
    MESOCOSM_LOG_DIR             where gateway.jsonl is written (default ./runs/spike)
    MESOCOSM_BUDGET_JSON         e.g. {"agent-1": {"completion_tokens": 50000, "requests": 300}}
    MESOCOSM_RETURN_TOKEN_IDS    "1" to add return_token_ids=true to chat requests (vLLM extra param)
    MESOCOSM_GATEWAY_PORT        port for `python 02_gateway.py` (default 8080)

Only stdlib + fastapi + httpx + uvicorn. The parse_sse() function is pure and unit-tested.
"""

from __future__ import annotations

import json
import os
import pathlib
import time
import uuid
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

UPSTREAM = os.environ.get("MESOCOSM_UPSTREAM", "http://127.0.0.1:8000")
LOG_DIR = pathlib.Path(os.environ.get("MESOCOSM_LOG_DIR", "./runs/spike"))
BUDGET: dict[str, dict[str, int]] = json.loads(os.environ.get("MESOCOSM_BUDGET_JSON", "{}") or "{}")
RETURN_TOKEN_IDS = os.environ.get("MESOCOSM_RETURN_TOKEN_IDS", "0") == "1"

HOP_BY_HOP = {"content-length", "transfer-encoding", "connection", "keep-alive", "host"}

app = FastAPI(title="mesocosm-gateway (spike)")
_client: httpx.AsyncClient | None = None
_usage: dict[str, dict[str, int]] = {}
_log_file = None


def _log(record: dict[str, Any]) -> None:
    global _log_file
    if _log_file is None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        _log_file = open(LOG_DIR / "gateway.jsonl", "a", buffering=1, encoding="utf-8")
    _log_file.write(json.dumps(record, ensure_ascii=False) + "\n")


def _client_get() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(base_url=UPSTREAM, timeout=httpx.Timeout(None))
    return _client


def _usage_for(agent: str) -> dict[str, int]:
    return _usage.setdefault(agent, {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0})


def _budget_exhausted(agent: str) -> str | None:
    """Return the name of the exhausted budget dimension, or None."""
    budget = BUDGET.get(agent)
    if not budget:
        return None
    used = _usage_for(agent)
    for key in ("requests", "prompt_tokens", "completion_tokens"):
        limit = budget.get(key)
        if limit is not None and used[key] >= limit:
            return key
    return None


def _add_usage(agent: str, usage: dict[str, Any] | None) -> None:
    used = _usage_for(agent)
    used["requests"] += 1
    if usage:
        used["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
        used["completion_tokens"] += int(usage.get("completion_tokens") or 0)


def parse_sse(raw: str) -> dict[str, Any]:
    """Collapse an OpenAI-style chat completion SSE stream into one record.

    Returns content, tool_calls (arguments concatenated per index), finish_reason,
    usage (if the server sent a usage chunk), and vLLM's prompt_token_ids / token_ids
    when present. Unparseable lines are counted, not raised.
    """
    out: dict[str, Any] = {
        "id": None,
        "model": None,
        "content": "",
        "reasoning": "",
        "tool_calls": [],
        "finish_reason": None,
        "usage": None,
        "prompt_token_ids": None,
        "token_ids": [],
        "chunks": 0,
        "unparsed_lines": 0,
    }
    tool_calls: dict[int, dict[str, Any]] = {}
    for line in raw.splitlines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            out["unparsed_lines"] += 1
            continue
        out["chunks"] += 1
        out["id"] = out["id"] or chunk.get("id")
        out["model"] = out["model"] or chunk.get("model")
        if chunk.get("usage"):
            out["usage"] = chunk["usage"]
        if chunk.get("prompt_token_ids") is not None:
            out["prompt_token_ids"] = chunk["prompt_token_ids"]
        for choice in chunk.get("choices") or []:
            if choice.get("token_ids"):
                out["token_ids"].extend(choice["token_ids"])
            if choice.get("finish_reason"):
                out["finish_reason"] = choice["finish_reason"]
            delta = choice.get("delta") or {}
            if delta.get("content"):
                out["content"] += delta["content"]
            if delta.get("reasoning_content"):
                out["reasoning"] += delta["reasoning_content"]
            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index", 0)
                slot = tool_calls.setdefault(
                    idx, {"id": None, "type": "function", "function": {"name": "", "arguments": ""}}
                )
                slot["id"] = slot["id"] or tc.get("id")
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["function"]["name"] += fn["name"]
                if fn.get("arguments"):
                    slot["function"]["arguments"] += fn["arguments"]
    out["tool_calls"] = [tool_calls[i] for i in sorted(tool_calls)]
    if not out["token_ids"]:
        out["token_ids"] = None
    return out


def _summarize_response(data: dict[str, Any]) -> dict[str, Any]:
    """Non-streaming chat completion -> same shape as parse_sse()."""
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    return {
        "id": data.get("id"),
        "model": data.get("model"),
        "content": msg.get("content") or "",
        "reasoning": msg.get("reasoning_content") or "",
        "tool_calls": msg.get("tool_calls") or [],
        "finish_reason": choice.get("finish_reason"),
        "usage": data.get("usage"),
        "prompt_token_ids": data.get("prompt_token_ids"),
        "token_ids": choice.get("token_ids"),
    }


def _request_summary(body: dict[str, Any]) -> dict[str, Any]:
    msgs = body.get("messages") or []
    return {
        "n_messages": len(msgs),
        "roles": [m.get("role") for m in msgs],
        "n_tools": len(body.get("tools") or []),
        "tool_names": [((t.get("function") or {}).get("name")) for t in (body.get("tools") or [])],
        "stream": bool(body.get("stream")),
        "temperature": body.get("temperature"),
        "max_tokens": body.get("max_tokens") or body.get("max_completion_tokens"),
    }


@app.get("/mesocosm/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "upstream": UPSTREAM, "return_token_ids": RETURN_TOKEN_IDS, "budget": BUDGET}


@app.get("/mesocosm/usage")
async def usage() -> dict[str, Any]:
    return {"usage": _usage, "budget": BUDGET}


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
async def proxy(path: str, request: Request):
    t0 = time.time()
    request_id = str(uuid.uuid4())
    agent = request.headers.get("x-mesocosm-agent", "unknown")
    run = request.headers.get("x-mesocosm-run", "unknown")
    body_bytes = await request.body()
    body: dict[str, Any] | None = None
    if body_bytes:
        try:
            body = json.loads(body_bytes)
        except json.JSONDecodeError:
            body = None
    is_chat = path.rstrip("/").endswith("chat/completions") and isinstance(body, dict)

    base = {
        "ts": t0,
        "request_id": request_id,
        "run": run,
        "agent": agent,
        "path": "/" + path,
        "method": request.method,
    }

    if is_chat:
        exhausted = _budget_exhausted(agent)
        if exhausted:
            _log({**base, "event": "budget_exhausted", "dimension": exhausted,
                  "usage": _usage_for(agent), "budget": BUDGET.get(agent),
                  "request": body, "request_summary": _request_summary(body)})
            return JSONResponse(
                status_code=429,
                content={"error": {
                    "message": f"mesocosm: {exhausted} budget exhausted for agent {agent}",
                    "type": "mesocosm_budget_exhausted",
                    "code": "budget_exhausted",
                    "param": exhausted,
                }},
            )
        if body.get("stream"):
            body.setdefault("stream_options", {})["include_usage"] = True
        if RETURN_TOKEN_IDS:
            body["return_token_ids"] = True
        body_bytes = json.dumps(body).encode("utf-8")

    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP}
    headers["x-mesocosm-request-id"] = request_id
    if is_chat:
        headers["content-length"] = str(len(body_bytes))

    client = _client_get()
    upstream_req = client.build_request(
        request.method, "/" + path, content=body_bytes, headers=headers, params=request.query_params
    )
    try:
        upstream = await client.send(upstream_req, stream=True)
    except httpx.HTTPError as e:
        _log({**base, "event": "upstream_error", "error": repr(e), "request": body})
        return JSONResponse(status_code=502, content={"error": {"message": f"mesocosm: upstream error {e!r}",
                                                                 "type": "mesocosm_upstream_error"}})

    resp_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in HOP_BY_HOP}

    if is_chat and body.get("stream"):
        async def stream():
            chunks: list[bytes] = []
            try:
                async for chunk in upstream.aiter_raw():
                    chunks.append(chunk)
                    yield chunk
            finally:
                await upstream.aclose()
                raw = b"".join(chunks).decode("utf-8", "replace")
                parsed = parse_sse(raw)
                _add_usage(agent, parsed.get("usage"))
                _log({**base, "event": "chat_completion", "stream": True, "status": upstream.status_code,
                      "latency_ms": round((time.time() - t0) * 1000),
                      "request": body, "request_summary": _request_summary(body),
                      "response": parsed, "usage_total": _usage_for(agent)})
        return StreamingResponse(stream(), status_code=upstream.status_code, headers=resp_headers,
                                 media_type=upstream.headers.get("content-type"))

    content = await upstream.aread()
    await upstream.aclose()
    resp_headers.pop("content-encoding", None)  # httpx already decoded the body
    if is_chat:
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            data = None
        parsed = _summarize_response(data) if isinstance(data, dict) and "choices" in data else {"raw": content[:2000].decode("utf-8", "replace")}
        _add_usage(agent, (data or {}).get("usage") if isinstance(data, dict) else None)
        _log({**base, "event": "chat_completion", "stream": False, "status": upstream.status_code,
              "latency_ms": round((time.time() - t0) * 1000),
              "request": body, "request_summary": _request_summary(body),
              "response": parsed, "usage_total": _usage_for(agent)})
    else:
        _log({**base, "event": "passthrough", "status": upstream.status_code,
              "latency_ms": round((time.time() - t0) * 1000)})
    return Response(content=content, status_code=upstream.status_code, headers=resp_headers,
                    media_type=upstream.headers.get("content-type"))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.environ.get("MESOCOSM_GATEWAY_HOST", "127.0.0.1"),
                port=int(os.environ.get("MESOCOSM_GATEWAY_PORT", "8080")))
