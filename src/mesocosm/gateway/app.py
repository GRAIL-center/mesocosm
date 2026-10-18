"""Model gateway (ADR-0003): OpenAI-compatible reverse proxy between harnesses and vLLM.

* stamps every request with X-Mesocosm-Request-Id; reads X-Mesocosm-Agent / X-Mesocosm-Run;
* logs full request and response to <log_dir>/gateway.jsonl (actual information exposure);
* pauses, resumes and stops runs: a paused run's requests wait here, a stopped run's are refused;
* enforces per-agent budgets and answers an explicit 429 when one is exhausted;
* `POST /mesocosm/budget` sets a budget and returns a receipt (the enforcement point for
  the `budget` condition);
* optionally asks vLLM for prompt/completion token ids.

Run: `python -m mesocosm.gateway` (settings from MESOCOSM_* env vars) or `spike/02_gateway.py`.
"""

from __future__ import annotations

import asyncio
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
    max_hold_seconds: float = 600.0   # a paused run's requests wait this long at most, then get 503

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            upstream=os.environ.get("MESOCOSM_UPSTREAM", cls.upstream),
            log_dir=pathlib.Path(os.environ.get("MESOCOSM_LOG_DIR", str(cls.log_dir))),
            budgets=json.loads(os.environ.get("MESOCOSM_BUDGET_JSON", "{}") or "{}"),
            return_token_ids=os.environ.get("MESOCOSM_RETURN_TOKEN_IDS", "0") == "1",
            max_hold_seconds=float(os.environ.get("MESOCOSM_MAX_HOLD_SECONDS", "600")),
        )


@dataclass
class Hold:
    """A paused run (or one agent of it): chat requests wait here until resume or stop."""
    run: str
    agent_id: str | None
    reason: str | None
    since: float = field(default_factory=time.time)
    event: asyncio.Event = field(default_factory=asyncio.Event)
    held: int = 0
    total_held: int = 0
    max_wait_ms: float = 0.0


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
        self.holds: dict[tuple[str, str | None], Hold] = {}
        self.stopped: dict[str, float] = {}
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


    # -- pause / resume / stop: the run manager cannot pause a harness turn, the chokepoint can ------

    def hold_for(self, run: str, agent: str) -> Hold | None:
        return self.holds.get((run, agent)) or self.holds.get((run, None))

    async def wait_while_paused(self, hold: Hold) -> tuple[float, bool]:
        """Block a request on a hold. Returns (seconds waited, released); not released = the hold expired."""
        t0 = time.time()
        hold.held += 1
        try:
            await asyncio.wait_for(hold.event.wait(), timeout=self.settings.max_hold_seconds)
            released = True
        except TimeoutError:
            released = False
        finally:
            hold.held -= 1
            hold.total_held += 1
            hold.max_wait_ms = max(hold.max_wait_ms, (time.time() - t0) * 1000)
        return time.time() - t0, released

    def pause(self, run: str, agent_id: str | None, reason: str | None, run_id: str | None = None) -> Receipt:
        key = (run, agent_id)
        already = key in self.holds
        hold = self.holds.setdefault(key, Hold(run=run, agent_id=agent_id, reason=reason))
        receipt = Receipt(op="run.pause", run_id=run_id,
                          requested={"run": run, "agent_id": agent_id, "reason": reason},
                          previous={"paused": already}, enforced_at=EnforcedAt(component="gateway"),
                          result="applied",
                          verification=Verification(method="gateway_hold", ok=True,
                                                    detail={"held_requests": hold.held,
                                                            "max_hold_seconds": self.settings.max_hold_seconds}))
        self.log({"ts": time.time(), "event": "receipt", **receipt.model_dump(mode="json")})
        return receipt

    def resume(self, run: str, agent_id: str | None, run_id: str | None = None) -> Receipt:
        hold = self.holds.pop((run, agent_id), None)
        detail: dict[str, Any] = {"paused": hold is not None}
        if hold is not None:
            hold.event.set()
            detail.update({"released": hold.held, "total_held": hold.total_held, "max_wait_ms": round(hold.max_wait_ms),
                           "paused_seconds": round(time.time() - hold.since, 3)})
        receipt = Receipt(op="run.resume", run_id=run_id, requested={"run": run, "agent_id": agent_id},
                          previous={"paused": hold is not None}, enforced_at=EnforcedAt(component="gateway"),
                          result="applied", verification=Verification(method="gateway_hold", ok=True, detail=detail))
        self.log({"ts": time.time(), "event": "receipt", **receipt.model_dump(mode="json")})
        return receipt

    def stop(self, run: str, run_id: str | None = None) -> Receipt:
        already = run in self.stopped
        self.stopped.setdefault(run, time.time())
        released = 0
        for key in [k for k in self.holds if k[0] == run]:      # held requests wake up and are refused
            hold = self.holds.pop(key)
            released += hold.held
            hold.event.set()
        receipt = Receipt(op="run.stop", run_id=run_id, requested={"run": run}, previous={"stopped": already},
                          enforced_at=EnforcedAt(component="gateway"), result="applied",
                          verification=Verification(method="gateway_reject", ok=True,
                                                    detail={"released_held_requests": released}))
        self.log({"ts": time.time(), "event": "receipt", **receipt.model_dump(mode="json")})
        return receipt

    def control_state(self) -> dict[str, Any]:
        return {"paused": [{"run": h.run, "agent_id": h.agent_id, "since": h.since, "held": h.held, "reason": h.reason}
                           for h in self.holds.values()],
                "stopped": sorted(self.stopped)}


def budget_filtered(budget: dict[str, Any]) -> dict[str, int]:
    return {k: int(v) for k, v in budget.items() if k in BUDGET_KEYS and v is not None}


class BudgetRequest(BaseModel):
    agent_id: str
    budget: dict[str, int]
    run_id: str | None = None


class ControlRequest(BaseModel):
    run: str                       # the X-Mesocosm-Run label the agents send
    agent_id: str | None = None    # pause one agent only; stop ignores it
    reason: str | None = None
    run_id: str | None = None


def create_app(settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    gw = Gateway(settings or Settings.from_env(), transport)
    app = FastAPI(title="mesocosm-gateway")
    app.state.gateway = gw

    @app.get("/mesocosm/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "upstream": gw.settings.upstream, "return_token_ids": gw.settings.return_token_ids,
                "budget": gw.budgets, **gw.control_state()}

    @app.get("/mesocosm/usage")
    async def usage() -> dict[str, Any]:
        return {"usage": gw.usage, "budget": gw.budgets}

    @app.post("/mesocosm/budget")
    async def set_budget(req: BudgetRequest) -> dict[str, Any]:
        return gw.set_budget(req.agent_id, req.budget, req.run_id).model_dump(mode="json")

    @app.post("/mesocosm/pause")
    async def pause(req: ControlRequest) -> dict[str, Any]:
        return gw.pause(req.run, req.agent_id, req.reason, req.run_id).model_dump(mode="json")

    @app.post("/mesocosm/resume")
    async def resume(req: ControlRequest) -> dict[str, Any]:
        return gw.resume(req.run, req.agent_id, req.run_id).model_dump(mode="json")

    @app.post("/mesocosm/stop")
    async def stop(req: ControlRequest) -> dict[str, Any]:
        return gw.stop(req.run, req.run_id).model_dump(mode="json")

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
            hold = gw.hold_for(run, agent)
            if hold is not None:
                waited, released = await gw.wait_while_paused(hold)
                gw.log({**base, "event": "held" if released else "hold_expired", "held_ms": round(waited * 1000),
                        "reason": hold.reason})
                if not released:
                    return JSONResponse(status_code=503, content={"error": {
                        "message": f"mesocosm: run {run} paused longer than {gw.settings.max_hold_seconds:.0f}s",
                        "type": "mesocosm_paused", "code": "paused"}})
            if run in gw.stopped:
                gw.log({**base, "event": "run_stopped", "request_summary": request_summary(body)})
                return JSONResponse(status_code=409, content={"error": {
                    "message": f"mesocosm: run {run} was stopped", "type": "mesocosm_run_stopped",
                    "code": "run_stopped"}})
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
