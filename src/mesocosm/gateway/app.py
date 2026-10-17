"""Model gateway (ADR-0003): OpenAI-compatible reverse proxy between harnesses and vLLM.

* stamps every request with X-Mesocosm-Request-Id; reads X-Mesocosm-Agent / X-Mesocosm-Run;
* logs full request and response to <log_dir>/gateway.jsonl (actual information exposure);
* enforces per-agent budgets and answers an explicit 429 when one is exhausted;
* `POST /mesocosm/budget` sets a budget and returns a receipt (the enforcement point for
  the `budget` condition);
* optionally asks vLLM for prompt/completion token ids.

Run: `python -m mesocosm.gateway` (settings from MESOCOSM_* env vars) or `spike/02_gateway.py`.
"""

from __future__ import annotations

import json
import os
import pathlib
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from mesocosm.core.receipts import EnforcedAt, Receipt, Verification

HOP_BY_HOP = {"content-length", "transfer-encoding", "connection", "keep-alive", "host"}
BUDGET_KEYS = ("requests", "prompt_tokens", "completion_tokens")


@dataclass
class Settings:
    upstream: str = "http://127.0.0.1:8000"
    log_dir: pathlib.Path = pathlib.Path("./runs/spike")
    budgets: dict[str, dict[str, int]] = field(default_factory=dict)
    return_token_ids: bool = False

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            upstream=os.environ.get("MESOCOSM_UPSTREAM", cls.upstream),
            log_dir=pathlib.Path(os.environ.get("MESOCOSM_LOG_DIR", str(cls.log_dir))),
            budgets=json.loads(os.environ.get("MESOCOSM_BUDGET_JSON", "{}") or "{}"),
            return_token_ids=os.environ.get("MESOCOSM_RETURN_TOKEN_IDS", "0") == "1",
        )


def parse_sse(raw: str) -> dict[str, Any]:
    """Collapse an OpenAI-style chat completion SSE stream into one record.

    Returns content, tool_calls (arguments concatenated per index), finish_reason, usage (if the
    server sent a usage chunk), and vLLM's prompt_token_ids / token_ids when present.
    Unparseable lines are counted, not raised.
    """
    out: dict[str, Any] = {
        "id": None, "model": None, "content": "", "reasoning": "", "tool_calls": [],
        "finish_reason": None, "usage": None, "prompt_token_ids": None, "token_ids": [],
        "chunks": 0, "unparsed_lines": 0,
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


def summarize_response(data: dict[str, Any]) -> dict[str, Any]:
    """Non-streaming chat completion -> same shape as parse_sse()."""
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    return {
        "id": data.get("id"), "model": data.get("model"),
        "content": msg.get("content") or "", "reasoning": msg.get("reasoning_content") or "",
        "tool_calls": msg.get("tool_calls") or [], "finish_reason": choice.get("finish_reason"),
        "usage": data.get("usage"), "prompt_token_ids": data.get("prompt_token_ids"),
        "token_ids": choice.get("token_ids"),
    }


def request_summary(body: dict[str, Any]) -> dict[str, Any]:
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


class Gateway:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.client = httpx.AsyncClient(base_url=settings.upstream, timeout=httpx.Timeout(None),
                                        transport=transport)
        self.budgets: dict[str, dict[str, int]] = {k: dict(v) for k, v in settings.budgets.items()}
        self.usage: dict[str, dict[str, int]] = {}
        self._log_file = None

    def log(self, record: dict[str, Any]) -> None:
        if self._log_file is None:
            self.settings.log_dir.mkdir(parents=True, exist_ok=True)
            self._log_file = open(self.settings.log_dir / "gateway.jsonl", "a", buffering=1, encoding="utf-8")
        self._log_file.write(json.dumps(record, ensure_ascii=False) + "\n")

    def records_for_run(self, run: str) -> list[dict[str, Any]]:
        path = self.settings.log_dir / "gateway.jsonl"
        if not path.exists():
            return []
        out = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("run") == run:
                out.append(rec)
        return out

    def usage_for(self, agent: str) -> dict[str, int]:
        return self.usage.setdefault(agent, {k: 0 for k in BUDGET_KEYS})

    def budget_exhausted(self, agent: str) -> str | None:
        """Name of the exhausted budget dimension, or None."""
        budget = self.budgets.get(agent)
        if not budget:
            return None
        used = self.usage_for(agent)
        for key in BUDGET_KEYS:
            limit = budget.get(key)
            if limit is not None and used[key] >= limit:
                return key
        return None

    def add_usage(self, agent: str, usage: dict[str, Any] | None) -> None:
        used = self.usage_for(agent)
        used["requests"] += 1
        if usage:
            used["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
            used["completion_tokens"] += int(usage.get("completion_tokens") or 0)

    def set_budget(self, agent: str, budget: dict[str, int], run_id: str | None = None) -> Receipt:
        """Apply a budget and return the receipt. Enforcement is this object's counters."""
        previous = self.budgets.get(agent)
        self.budgets[agent] = {k: int(v) for k, v in budget.items() if k in BUDGET_KEYS and v is not None}
        receipt = Receipt(
            op="conditions.set", run_id=run_id,
            requested={"condition": "budget", "agent_id": agent, "value": self.budgets[agent]},
            previous={"value": previous} if previous is not None else None,
            enforced_at=EnforcedAt(component="gateway"),
            result="applied",
            verification=Verification(method="gateway_budget_state", ok=self.budgets[agent] == budget_filtered(budget),
                                      detail={"usage": dict(self.usage_for(agent))}),
        )
        self.log({"ts": time.time(), "event": "receipt", **receipt.model_dump(mode="json")})
        return receipt


def budget_filtered(budget: dict[str, Any]) -> dict[str, int]:
    return {k: int(v) for k, v in budget.items() if k in BUDGET_KEYS and v is not None}


class BudgetRequest(BaseModel):
    agent_id: str
    budget: dict[str, int]
    run_id: str | None = None


def create_app(settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    gw = Gateway(settings or Settings.from_env(), transport)
    app = FastAPI(title="mesocosm-gateway")
    app.state.gateway = gw

    @app.get("/mesocosm/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "upstream": gw.settings.upstream, "return_token_ids": gw.settings.return_token_ids,
                "budget": gw.budgets}

    @app.get("/mesocosm/usage")
    async def usage() -> dict[str, Any]:
        return {"usage": gw.usage, "budget": gw.budgets}

    @app.post("/mesocosm/budget")
    async def set_budget(req: BudgetRequest) -> dict[str, Any]:
        return gw.set_budget(req.agent_id, req.budget, req.run_id).model_dump(mode="json")

    @app.get("/mesocosm/records")
    async def records(run: str) -> list[dict[str, Any]]:
        """Exposure records for one run label, so the control plane can copy them into the bundle."""
        return gw.records_for_run(run)

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
        base = {"ts": t0, "request_id": request_id, "run": run, "agent": agent,
                "path": "/" + path, "method": request.method}

        if is_chat:
            exhausted = gw.budget_exhausted(agent)
            if exhausted:
                gw.log({**base, "event": "budget_exhausted", "dimension": exhausted,
                        "usage": gw.usage_for(agent), "budget": gw.budgets.get(agent),
                        "request": body, "request_summary": request_summary(body)})
                return JSONResponse(status_code=429, content={"error": {
                    "message": f"mesocosm: {exhausted} budget exhausted for agent {agent}",
                    "type": "mesocosm_budget_exhausted", "code": "budget_exhausted", "param": exhausted}})
            if body.get("stream"):
                body.setdefault("stream_options", {})["include_usage"] = True
            if gw.settings.return_token_ids:
                body["return_token_ids"] = True
            body_bytes = json.dumps(body).encode("utf-8")

        headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP}
        headers["x-mesocosm-request-id"] = request_id
        if is_chat:
            headers["content-length"] = str(len(body_bytes))

        upstream_req = gw.client.build_request(request.method, "/" + path, content=body_bytes,
                                               headers=headers, params=request.query_params)
        try:
            upstream = await gw.client.send(upstream_req, stream=True)
        except httpx.HTTPError as e:
            gw.log({**base, "event": "upstream_error", "error": repr(e), "request": body})
            return JSONResponse(status_code=502, content={"error": {
                "message": f"mesocosm: upstream error {e!r}", "type": "mesocosm_upstream_error"}})

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
                    # An error status carries a JSON body, not an event stream: keep it verbatim.
                    parsed = parse_sse(raw) if upstream.status_code < 400 else {"raw": raw[:4000]}
                    gw.add_usage(agent, parsed.get("usage"))
                    gw.log({**base, "event": "chat_completion", "stream": True, "status": upstream.status_code,
                            "latency_ms": round((time.time() - t0) * 1000), "request": body,
                            "request_summary": request_summary(body), "response": parsed,
                            "usage_total": gw.usage_for(agent)})
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
            if isinstance(data, dict) and "choices" in data:
                parsed = summarize_response(data)
            else:
                parsed = {"raw": content[:2000].decode("utf-8", "replace")}
            gw.add_usage(agent, data.get("usage") if isinstance(data, dict) else None)
            gw.log({**base, "event": "chat_completion", "stream": False, "status": upstream.status_code,
                    "latency_ms": round((time.time() - t0) * 1000), "request": body,
                    "request_summary": request_summary(body), "response": parsed,
                    "usage_total": gw.usage_for(agent)})
        else:
            gw.log({**base, "event": "passthrough", "status": upstream.status_code,
                    "latency_ms": round((time.time() - t0) * 1000)})
        return Response(content=content, status_code=upstream.status_code, headers=resp_headers,
                        media_type=upstream.headers.get("content-type"))

    return app


def main() -> None:
    import uvicorn

    uvicorn.run(create_app(), host=os.environ.get("MESOCOSM_GATEWAY_HOST", "127.0.0.1"),
                port=int(os.environ.get("MESOCOSM_GATEWAY_PORT", "8080")))
