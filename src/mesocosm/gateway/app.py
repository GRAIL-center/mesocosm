"""Model gateway (ADR-0003): OpenAI-compatible reverse proxy between harnesses and vLLM.

* identifies the agent by its own bearer key (or by X-Mesocosm-Agent / X-Mesocosm-Run headers from a
  control-plane harness) and stamps every upstream request with X-Mesocosm-Request-Id;
* logs full request and response to <log_dir>/gateway.jsonl (actual information exposure);
* pauses, resumes and stops runs: a paused run's requests wait here, a stopped run's are refused;
* enforces per-agent budgets and answers an explicit 429 when one is exhausted;
* routes each chat request to the upstream serving the model it names (MESOCOSM_UPSTREAMS, ADR-0029): vLLM on
  the host by default, a commercial API for a baseline agent; keys come from the gateway's environment;
* `POST /admin/budget` (control key only) sets a budget and returns a receipt (the enforcement point for
  the `budget` condition);
* optionally asks vLLM for prompt/completion token ids.

Run: `python -m mesocosm.gateway` (settings from MESOCOSM_* env vars) or `spike/02_gateway.py`.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import pathlib
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, TextIO

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from mesocosm.core.receipts import EnforcedAt, Receipt, Verification
from mesocosm.gateway.actions import register_action_routes
from mesocosm.gateway.resources import ResourceExhausted, TeamResources
from mesocosm.gateway.rollout import register_rollout_routes, sampling_request
from mesocosm.learning.config import PolicyIdentity

HOP_BY_HOP = {"content-length", "transfer-encoding", "connection", "keep-alive", "host"}
RECORDS_CHUNK = 1 << 20   # bytes of NDJSON per streamed chunk of /admin/records
BUDGET_KEYS = ("requests", "prompt_tokens", "completion_tokens")


@dataclass
class Settings:
    upstream: str = "http://127.0.0.1:8000"
    log_dir: pathlib.Path = pathlib.Path("./runs/spike")
    budgets: dict[str, dict[str, int]] = field(default_factory=dict)
    return_token_ids: bool = False
    require_rollout_binding: bool = False
    max_hold_seconds: float = 600.0   # a paused run's requests wait this long at most, then get 503
    control_key: str = "mesocosm-local"                    # bearer key of the /admin endpoints (the control plane)
    agent_keys: dict[str, dict[str, str]] = field(default_factory=dict)   # bearer key -> {agent, run} (ADR-0019)
    upstream_key: str | None = None                        # the inference server's key; agents never hold it
    # model id -> {url, key_env, kind}: where requests for that model go (ADR-0029); other models go to `upstream`
    upstreams: dict[str, dict[str, Any]] = field(default_factory=dict)
    team_resources: dict[str, int | None] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            upstream=os.environ.get("MESOCOSM_UPSTREAM", cls.upstream),
            log_dir=pathlib.Path(os.environ.get("MESOCOSM_LOG_DIR", str(cls.log_dir))),
            budgets=json.loads(os.environ.get("MESOCOSM_BUDGET_JSON", "{}") or "{}"),
            return_token_ids=os.environ.get("MESOCOSM_RETURN_TOKEN_IDS", "0") == "1",
            require_rollout_binding=os.environ.get("MESOCOSM_REQUIRE_ROLLOUT_BINDING", "0") == "1",
            max_hold_seconds=float(os.environ.get("MESOCOSM_MAX_HOLD_SECONDS", "600")),
            control_key=os.environ.get("MESOCOSM_CONTROL_KEY", cls.control_key),
            agent_keys=json.loads(os.environ.get("MESOCOSM_AGENT_KEYS", "{}") or "{}"),
            upstream_key=os.environ.get("MESOCOSM_UPSTREAM_KEY") or None,
            upstreams=json.loads(os.environ.get("MESOCOSM_UPSTREAMS", "{}") or "{}"),
            team_resources=json.loads(os.environ.get("MESOCOSM_TEAM_RESOURCES", "{}") or "{}"),
        )


# What a refused request looks like to the harness: the shapes an OpenAI-compatible API returns for
# the same situations, with nothing about the run in them (ADR-0019). The gateway log says why.
REFUSALS = {
    "auth": (401, {"message": "Incorrect API key provided.", "type": "invalid_request_error",
                   "code": "invalid_api_key"}),
    "invalid": (400, {"message": "Invalid chat completion request.", "type": "invalid_request_error",
                      "code": "invalid_request"}),
    "paused": (503, {"message": "The server is temporarily unable to handle the request. Please try again later.",
                     "type": "server_error", "code": "service_unavailable"}),
    "stopped": (403, {"message": "Your account has been deactivated.", "type": "invalid_request_error",
                      "code": "account_deactivated"}),
    "budget": (429, {"message": "You exceeded your current quota, please check your plan and billing details.",
                     "type": "insufficient_quota", "code": "insufficient_quota"}),
    "upstream": (502, {"message": "The server had an error while processing your request.",
                       "type": "server_error", "code": "upstream_error"}),
}


def refusal(kind: str, **extra: Any) -> JSONResponse:
    status, body = REFUSALS[kind]
    return JSONResponse(status_code=status, content={"error": {**body, **extra}})


def bearer(request: Request) -> str | None:
    header = request.headers.get("authorization") or ""
    return header[7:].strip() if header.lower().startswith("bearer ") else None


def public_model_list(content: bytes, bound_model: str | None) -> dict[str, Any]:
    """Publish model identifiers, never upstream checkpoint paths or policy bookkeeping."""
    data = json.loads(content)
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        raise ValueError("invalid upstream model listing")
    models, seen = [], set()
    for entry in data["data"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not entry["id"]:
            raise ValueError("invalid upstream model entry")
        model = entry["id"]
        if model in seen:
            raise ValueError("duplicate upstream model identifier")
        seen.add(model)
        if bound_model is not None and model != bound_model:
            continue
        created = entry.get("created", 0)
        if type(created) is not int or created < 0:
            raise ValueError("invalid upstream model creation time")
        # Omitted creation time uses zero; ownership names this API, not the host artifact.
        models.append({"id": model, "object": "model", "created": created, "owned_by": "api"})
    if bound_model is not None and not models:
        raise ValueError("bound model is absent from upstream listing")
    return {"object": "list", "data": models}


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

    Returns content, reasoning, tool_calls (arguments concatenated per index), finish_reason, usage (if the
    server sent a usage chunk), and vLLM's prompt_token_ids / token_ids when present.
    Prefer current `reasoning` text, falling back to legacy `reasoning_content` without duplicating aliases.
    Unparseable lines are counted, not raised.
    """
    out: dict[str, Any] = {
        "id": None, "model": None, "content": "", "reasoning": "", "tool_calls": [],
        "finish_reason": None, "usage": None, "prompt_token_ids": None, "token_ids": [],
        "token_logprobs": [], "choice_indices": [], "stream_complete": False, "stream_errors": [],
        "chunks": 0, "unparsed_lines": 0,
    }
    tool_calls: dict[int, dict[str, Any]] = {}
    for line in raw.splitlines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            out["stream_complete"] = True
            continue
        if not data:
            continue
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            out["unparsed_lines"] += 1
            continue
        out["chunks"] += 1
        if chunk.get("error") is not None:
            out["stream_errors"].append(chunk["error"])
        out["id"] = out["id"] or chunk.get("id")
        out["model"] = out["model"] or chunk.get("model")
        if chunk.get("usage"):
            out["usage"] = chunk["usage"]
        if chunk.get("prompt_token_ids") is not None:
            out["prompt_token_ids"] = chunk["prompt_token_ids"]
        for choice in chunk.get("choices") or []:
            index = choice.get("index", 0)
            if index not in out["choice_indices"]:
                out["choice_indices"].append(index)
            if choice.get("token_ids"):
                out["token_ids"].extend(choice["token_ids"])
            for token in (choice.get("logprobs") or {}).get("content") or []:
                out["token_logprobs"].append(token.get("logprob") if isinstance(token, dict) else None)
            if choice.get("finish_reason"):
                out["finish_reason"] = choice["finish_reason"]
            delta = choice.get("delta") or {}
            if delta.get("content"):
                out["content"] += delta["content"]
            reasoning = delta.get("reasoning") or delta.get("reasoning_content")
            if reasoning:
                out["reasoning"] += reasoning
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
        "content": msg.get("content") or "",
        "reasoning": msg.get("reasoning") or msg.get("reasoning_content") or "",
        "tool_calls": msg.get("tool_calls") or [], "finish_reason": choice.get("finish_reason"),
        "usage": data.get("usage"), "prompt_token_ids": data.get("prompt_token_ids"),
        "token_ids": choice.get("token_ids"),
        "error": data.get("error"),
        "token_logprobs": [token.get("logprob") if isinstance(token, dict) else None
                           for token in (choice.get("logprobs") or {}).get("content") or []],
        "choice_indices": [c.get("index", 0) for c in data.get("choices") or []],
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


@dataclass
class Route:
    """Where a request goes: the upstream's client, the key the gateway presents there, what kind of server it is."""
    client: httpx.AsyncClient
    upstream: str
    key: str | None
    kind: str          # vllm: token ids can be requested; openai: any other OpenAI-compatible API


class Gateway:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self._transport = transport
        self.client = httpx.AsyncClient(base_url=settings.upstream, timeout=httpx.Timeout(None),
                                        transport=transport)
        self.clients: dict[str, httpx.AsyncClient] = {settings.upstream: self.client}
        self.budgets: dict[str, dict[str, int]] = {k: dict(v) for k, v in settings.budgets.items()}
        self.usage: dict[str, dict[str, int]] = {}
        self.request_locks: dict[str, asyncio.Lock] = {}
        self.team_resources: dict[str, TeamResources] = {}
        self.rollout_bindings: dict[tuple[str, str], dict[str, Any]] = {}
        self.missing_usage: set[str] = set()
        self.holds: dict[tuple[str, str | None], Hold] = {}
        self.stopped: dict[str, float] = {}
        self.refusal_sequence = 0
        self.terminal_refusals: dict[tuple[str, str], dict[str, Any]] = {}
        self._log_file: TextIO | None = None
        self._restore_state()

    def _restore_state(self) -> None:
        path = self.settings.log_dir / "gateway.jsonl"
        if not path.is_file():
            return
        admitted: dict[str, str] = {}
        pending: dict[str, str] = {}
        completed: set[str] = set()
        with path.open() as stream:
            for line in stream:
                record = json.loads(line)
                self._restore_agent_record(record, admitted, pending, completed)
                if record.get("event") == "terminal_refusal":
                    sequence = record.get("sequence")
                    if (type(sequence) is not int or sequence <= self.refusal_sequence
                            or not isinstance(record.get("run"), str) or not isinstance(record.get("agent"), str)):
                        raise ValueError("invalid persisted terminal refusal")
                    self.refusal_sequence = sequence
                    self.terminal_refusals[(record["run"], record["agent"])] = record
                    continue
                if record.get("event") not in {"resource_admitted", "resource_released", "resource_close_unknown"}:
                    continue
                for key in ("requests", "completion_tokens", "active", "reserved_completion_tokens"):
                    if type(record.get(key)) is not int or record[key] < 0:
                        raise ValueError("invalid persisted team resource counters")
                run = record.get("run")
                if not isinstance(run, str):
                    raise ValueError("persisted resource identity is missing")
                self.team_resources[run] = TeamResources(
                    requests=self.settings.team_resources.get("requests"),
                    completion_tokens=self.settings.team_resources.get("completion_tokens"),
                    concurrency=self.settings.team_resources.get("concurrency"),
                    used_requests=record["requests"], used_completion_tokens=record["completion_tokens"],
                    active=record["active"], reserved=record["reserved_completion_tokens"],
                    unknown_usage=bool(record.get("unknown_usage")) or record["active"] > 0)
        # An admission without settlement may already have reached the upstream.
        # Its request is spent; missing token measurements never become a refund.
        self.missing_usage.update(pending.values())

    def _restore_agent_record(self, record: dict[str, Any], admitted: dict[str, str],
                              pending: dict[str, str], completed: set[str]) -> None:
        event = record.get("event")
        if event == "receipt" and record.get("op") == "conditions.set" and record.get("result") == "applied":
            requested = record.get("requested") or {}
            if requested.get("condition") == "budget":
                agent, budget = requested.get("agent_id"), requested.get("value")
                if (not isinstance(agent, str) or not agent or not isinstance(budget, dict)
                        or any(key not in BUDGET_KEYS or type(value) is not int or value < 0
                               for key, value in budget.items())):
                    raise ValueError("invalid persisted agent budget")
                self.budgets[agent] = dict(budget)
            return
        chat_error = (event == "upstream_error" and record.get("method") == "POST"
                      and str(record.get("path", "")).rstrip("/") == "/v1/chat/completions")
        if event not in {"request_admitted", "request_usage_unknown", "chat_completion", "resource_admitted"}:
            if not chat_error:
                return
        agent, request_id = record.get("agent"), record.get("request_id")
        if not isinstance(agent, str) or not agent or not isinstance(request_id, str) or not request_id:
            raise ValueError("persisted agent accounting identity is missing")
        if request_id in admitted and admitted[request_id] != agent:
            raise ValueError("conflicting persisted request identity")
        if event in {"request_admitted", "request_usage_unknown", "chat_completion"}:
            counters = record.get("usage_total")
            if (not isinstance(counters, dict)
                    or any(type(counters.get(key)) is not int or counters[key] < 0 for key in BUDGET_KEYS)):
                raise ValueError("invalid persisted agent usage counters")
            previous = self.usage_for(agent)
            if any(counters[key] < previous[key] for key in BUDGET_KEYS):
                raise ValueError("persisted agent usage went backwards; cannot recover this legacy log safely")
            expected_requests = previous["requests"] + (request_id not in admitted)
            if counters["requests"] < expected_requests:
                raise ValueError("persisted agent usage does not include the admitted request")
            if counters["requests"] > expected_requests:
                # A legacy cancellation could leave no settlement record at all.
                # Its later cumulative count is known, but its token cost is not.
                self.missing_usage.add(agent)
            self.usage[agent] = {key: counters[key] for key in BUDGET_KEYS}
            unknown = record.get("missing_usage")
            if unknown is not None and type(unknown) is not bool:
                raise ValueError("invalid persisted missing-usage state")
            if event == "chat_completion" and unknown is None:
                # Older completed records already contain cumulative counters. Do not
                # count their requests twice; absent usage retains the old fail-closed policy.
                unknown = not bool((record.get("response") or {}).get("usage"))
            if unknown or event == "request_usage_unknown":
                self.missing_usage.add(agent)
        elif request_id not in admitted:
            # Legacy team admissions and chat transport errors establish a real
            # attempt even when their old format lacks a per-agent counter snapshot.
            self.usage_for(agent)["requests"] += 1
        admitted[request_id] = agent
        if event in {"request_admitted", "resource_admitted"}:
            if request_id in completed:
                raise ValueError("persisted request was admitted after completion")
            pending[request_id] = agent
        else:
            pending.pop(request_id, None)
            if chat_error or event == "request_usage_unknown":
                self.missing_usage.add(agent)
            if event == "chat_completion":
                if request_id in completed:
                    raise ValueError("duplicate persisted chat completion")
                completed.add(request_id)

    def record_admission(self, base: dict[str, Any]) -> None:
        """Persist the spent request before forwarding any bytes to the upstream."""
        agent = base["agent"]
        self.usage_for(agent)["requests"] += 1
        self.log({**base, "event": "request_admitted", "usage_total": self.usage_for(agent),
                  "missing_usage": agent in self.missing_usage})
        assert self._log_file is not None
        self._log_file.flush()
        os.fsync(self._log_file.fileno())

    def route(self, model: str | None) -> Route:
        """The upstream for a model id (ADR-0029): a configured one, else the default inference server."""
        spec = self.settings.upstreams.get(model or "")
        if spec is None:
            return Route(self.client, self.settings.upstream, self.settings.upstream_key, "vllm")
        url = str(spec["url"]).rstrip("/")
        client = self.clients.get(url)
        if client is None:
            client = self.clients[url] = httpx.AsyncClient(base_url=url, timeout=httpx.Timeout(None),
                                                           transport=self._transport)
        key = os.environ.get(spec["key_env"]) if spec.get("key_env") else None
        return Route(client, url, key or None, str(spec.get("kind") or "vllm"))

    def log(self, record: dict[str, Any]) -> None:
        if self._log_file is None:
            self.settings.log_dir.mkdir(parents=True, exist_ok=True)
            self._log_file = open(self.settings.log_dir / "gateway.jsonl", "a", buffering=1, encoding="utf-8")
        self._log_file.write(json.dumps(record, ensure_ascii=False) + "\n")

    def records_for_run(self, run: str, since: float | None = None) -> Iterator[str]:
        """The log lines of one run label (and not older than `since`), one at a time: the log holds
        every run's full prompts and is read line by line, never loaded whole."""
        path = self.settings.log_dir / "gateway.jsonl"
        if not path.exists():
            return
        with path.open(encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                rec = json.loads(line)
                if rec.get("run") == run and (since is None or float(rec.get("ts") or 0) >= since):
                    yield line if line.endswith("\n") else line + "\n"

    def terminal_refusal(self, base: dict[str, Any], dimension: str, reason: str) -> None:
        """Publish an actual terminal denial, never a prediction from a full but still active quota.

        Keep one small receipt per run/agent. The append-only log retains every denial; polling
        controllers do not need to repeatedly download prompts or tool outputs to stop retries.
        """
        sequence = self.refusal_sequence + 1
        record = {"event": "terminal_refusal", "sequence": sequence, "event_id": str(uuid.uuid4()),
                  "ts": time.time(), "run": base["run"], "agent": base["agent"],
                  "request_id": base["request_id"], "dimension": dimension, "reason": reason}
        self.log(record)
        self.refusal_sequence = sequence
        self.terminal_refusals[(base["run"], base["agent"])] = record

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
            if limit is not None and key != "requests" and agent in self.missing_usage:
                return key
            if limit is not None and used[key] >= limit:
                return key
        return None

    def add_usage(self, agent: str, usage: dict[str, Any] | None) -> None:
        used = self.usage_for(agent)
        if usage:
            used["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
            used["completion_tokens"] += int(usage.get("completion_tokens") or 0)
        else:
            self.missing_usage.add(agent)

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
    run: str                       # the run label the agents' keys are bound to
    agent_id: str | None = None    # pause one agent only; stop ignores it
    reason: str | None = None
    run_id: str | None = None


def create_app(settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    gw = Gateway(settings or Settings.from_env(), transport)
    app = FastAPI(title="gateway", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.gateway = gw

    def identity(request: Request) -> tuple[str, str, str]:
        """Only the agent's key or an authenticated control-plane caller may name an identity."""
        known = gw.settings.agent_keys.get(bearer(request) or "")
        if known:
            return known["agent"], known["run"], "key"
        if bearer(request) == gw.settings.control_key:
            return request.headers.get("x-mesocosm-agent", "unknown"), request.headers.get("x-mesocosm-run",
                                                                                            "unknown"), "header"
        return "unknown", "unknown", "none"

    @app.middleware("http")
    async def admin_only(request: Request, call_next):
        """The control endpoints answer the control plane only; to anyone else they do not exist, and
        the attempt is logged with whatever identity the caller has."""
        if request.url.path.startswith("/admin/") and bearer(request) != gw.settings.control_key:
            agent, run, how = identity(request)
            gw.log({"ts": time.time(), "request_id": str(uuid.uuid4()), "run": run, "agent": agent,
                    "path": request.url.path, "method": request.method, "event": "admin_denied", "identified_by": how})
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
        return await call_next(request)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        """Liveness, for the agents' egress check and Compose; says nothing about the run."""
        return {"ok": True}

    @app.get("/admin/health")
    async def admin_health() -> dict[str, Any]:
        upstreams = {m: {"url": s["url"], "kind": s.get("kind", "vllm"),
                         "key": bool(s.get("key_env") and os.environ.get(s["key_env"]))}   # present, never shown
                     for m, s in gw.settings.upstreams.items()}
        return {"ok": True, "upstream": gw.settings.upstream, "return_token_ids": gw.settings.return_token_ids,
                "upstreams": upstreams, "require_rollout_binding": gw.settings.require_rollout_binding,
                "budget": gw.budgets, "agents": sorted(v["agent"] for v in gw.settings.agent_keys.values()),
                "team_limits": gw.settings.team_resources,
                **gw.control_state()}

    @app.get("/admin/usage")
    async def usage() -> dict[str, Any]:
        return {"usage": gw.usage, "budget": gw.budgets,
                "team": {run: pool.snapshot() for run, pool in gw.team_resources.items()},
                "team_limits": gw.settings.team_resources}

    @app.get("/admin/terminal-refusal")
    async def terminal_refusal(run: str, agent_id: str, after: int = 0) -> Response:
        if after < 0 or after > gw.refusal_sequence:
            return JSONResponse(status_code=409, content={"detail": "terminal refusal cursor unavailable"})
        record = gw.terminal_refusals.get((run, agent_id))
        return JSONResponse({"cursor": gw.refusal_sequence,
                             "refusal": record if record is not None and record["sequence"] > after else None})

    @app.post("/admin/budget")
    async def set_budget(req: BudgetRequest) -> dict[str, Any]:
        return gw.set_budget(req.agent_id, req.budget, req.run_id).model_dump(mode="json")

    @app.post("/admin/pause")
    async def pause(req: ControlRequest) -> dict[str, Any]:
        return gw.pause(req.run, req.agent_id, req.reason, req.run_id).model_dump(mode="json")

    @app.post("/admin/resume")
    async def resume(req: ControlRequest) -> dict[str, Any]:
        return gw.resume(req.run, req.agent_id, req.run_id).model_dump(mode="json")

    @app.post("/admin/stop")
    async def stop(req: ControlRequest) -> dict[str, Any]:
        return gw.stop(req.run, req.run_id).model_dump(mode="json")

    @app.get("/admin/records")
    async def records(run: str, since: float | None = None) -> StreamingResponse:
        """Exposure records for one run label as NDJSON, so the control plane can copy them into the
        bundle without either side holding the whole log in memory."""
        lines = gw.records_for_run(run, since)

        def chunk() -> str | None:
            buf: list[str] = []
            size = 0
            for line in lines:
                buf.append(line)
                size += len(line)
                if size >= RECORDS_CHUNK:
                    break
            return "".join(buf) if buf else None

        async def stream():
            while (data := await asyncio.to_thread(chunk)) is not None:
                yield data

        return StreamingResponse(stream(), media_type="application/x-ndjson")

    register_rollout_routes(app, gw)
    register_action_routes(app, identity_callback=identity, log_callback=gw.log, control_key=gw.settings.control_key)

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
    async def proxy(path: str, request: Request):
        t0 = time.time()
        request_id = str(uuid.uuid4())
        agent, run, identified_by = identity(request)
        base = {"ts": t0, "request_id": request_id, "run": run, "agent": agent, "identified_by": identified_by,
                "path": "/" + path, "method": request.method}
        if identified_by == "none":
            gw.log({**base, "event": "auth_denied"})
            return refusal("auth")
        is_chat = path.rstrip("/") == "v1/chat/completions" and request.method == "POST"
        is_models = path.rstrip("/") == "v1/models" and request.method in ("GET", "HEAD")
        if not is_chat and not is_models:
            gw.log({**base, "event": "endpoint_denied"})
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
        body_bytes = await request.body()
        body: dict[str, Any] | None = None
        if body_bytes:
            try:
                body = json.loads(body_bytes)
            except json.JSONDecodeError:
                body = None

        def invalid_request(reason: str) -> JSONResponse:
            gw.log({**base, "event": "invalid_request", "reason": reason, "request": body})
            return refusal("invalid")

        model = None
        if is_chat:
            if not isinstance(body, dict):
                return invalid_request("request must be a JSON object")
            if (not isinstance(body.get("model", ""), str)
                    or not isinstance(body.get("messages", []), list)
                    or any(not isinstance(m, dict) for m in body.get("messages", []))):
                return invalid_request("invalid model/messages shape")
            model = body.get("model")
        route = gw.route(model)
        base.update({"upstream": route.upstream, "model_kind": route.kind})
        # A lease lasts through the final stream chunk. Other agents keep their own independent leases.
        lock = gw.request_locks.setdefault(agent, asyncio.Lock())
        lock_released = False
        streaming = False
        pool: TeamResources | None = None
        response_usage: dict[str, Any] | None = None
        transport_closed = True
        admitted = False
        usage_recorded = False

        def release() -> None:
            nonlocal lock_released
            if pool is not None and request_id in pool.leases:
                if transport_closed:
                    pool.release(request_id, response_usage)
                else:
                    pool.unknown_usage = True
                gw.log({**base, "event": "resource_released" if transport_closed else "resource_close_unknown",
                        "resource": "team_model", **pool.snapshot()})
            if lock is not None and not lock_released:
                lock_released = True
                lock.release()

        if lock is not None:
            await lock.acquire()
        try:
            context = gw.rollout_bindings.get((run, agent)) if identified_by == "key" else None
            if identified_by == "key" and gw.settings.require_rollout_binding and context is None:
                gw.log({**base, "event": "rollout_denied", "reason": "missing_binding", "request": body})
                return refusal("invalid")
            bound_model = context["policy"]["model"] if context is not None else None
            if is_models and bound_model is not None:
                route = gw.route(bound_model)
                base.update({"upstream": route.upstream, "model_kind": route.kind})
            if is_chat:
                assert body is not None
                if context is not None:
                    base["rollout"] = copy.deepcopy(context)
                    try:
                        body = sampling_request(body, PolicyIdentity.model_validate(context["policy"]), route.kind)
                    except ValueError as exc:
                        gw.log({**base, "event": "rollout_denied", "reason": str(exc), "request": body})
                        return refusal("invalid")
                while (hold := gw.hold_for(run, agent)) is not None:
                    waited, released = await gw.wait_while_paused(hold)
                    gw.log({**base, "event": "held" if released else "hold_expired", "held_ms": round(waited * 1000),
                            "reason": hold.reason})
                    if not released:
                        return refusal("paused")
                if run in gw.stopped:
                    gw.log({**base, "event": "run_stopped", "request_summary": request_summary(body)})
                    return refusal("stopped")
                exhausted = gw.budget_exhausted(agent)
                if exhausted:
                    gw.terminal_refusal(base, exhausted,
                        "usage_unknown" if exhausted != "requests" and agent in gw.missing_usage else "quota_exhausted")
                    gw.log({**base, "event": "budget_exhausted", "dimension": exhausted,
                            "usage": gw.usage_for(agent), "budget": gw.budgets.get(agent),
                            "request": body, "request_summary": request_summary(body)})
                    return refusal("budget", param=exhausted)
                remaining = gw.budgets.get(agent, {}).get("completion_tokens")
                if remaining is not None:
                    remaining -= gw.usage_for(agent)["completion_tokens"]
                    count = body.get("n", 1)
                    if not isinstance(count, int) or count < 1:
                        return invalid_request("invalid completion count")
                    allowance = remaining // count
                    if allowance < 1:
                        gw.terminal_refusal(base, "completion_tokens", "request_exceeds_remaining_budget")
                        return refusal("budget", param="completion_tokens")
                    limit_keys = [k for k in ("max_tokens", "max_completion_tokens") if k in body] or ["max_tokens"]
                    for limit_key in limit_keys:
                        requested = body.get(limit_key)
                        if requested is not None and (not isinstance(requested, int) or requested < 1):
                            return invalid_request("invalid completion limit")
                        body[limit_key] = min(requested, allowance) if requested is not None else allowance
                if body.get("stream"):
                    options = body.get("stream_options") or {}
                    if not isinstance(options, dict):
                        return invalid_request("invalid stream options")
                    body["stream_options"] = {**options, "include_usage": True}
                if gw.settings.return_token_ids and route.kind == "vllm":     # only vLLM understands the field
                    body["return_token_ids"] = True
                if identified_by == "key" and any(v is not None for v in gw.settings.team_resources.values()):
                    pool = gw.team_resources.setdefault(run, TeamResources(
                        requests=gw.settings.team_resources.get("requests"),
                        completion_tokens=gw.settings.team_resources.get("completion_tokens"),
                        concurrency=gw.settings.team_resources.get("concurrency")))
                    try:
                        pool.acquire(request_id, body)
                    except ResourceExhausted as exc:
                        gw.log({**base, "event": "resource_denied", "reason": str(exc), **pool.snapshot()})
                        if str(exc) == "team_requests":
                            gw.terminal_refusal(base, str(exc), "quota_exhausted")
                        elif str(exc) == "team_completion_tokens":
                            count = body.get("n", 1)
                            available = ((pool.completion_tokens or 0) - pool.used_completion_tokens)
                            if pool.unknown_usage:
                                gw.terminal_refusal(base, str(exc), "usage_unknown")
                            elif available <= 0:
                                gw.terminal_refusal(base, str(exc), "quota_exhausted")
                            elif type(count) is int and available < count:
                                gw.terminal_refusal(base, str(exc), "request_exceeds_remaining_budget")
                        return refusal("budget", param=str(exc))
                    except ValueError as exc:
                        return invalid_request(str(exc))
                    gw.log({**base, "event": "resource_admitted", "resource": "team_model", **pool.snapshot()})
                body_bytes = json.dumps(body).encode("utf-8")
                # No await between the quota check and admission: an upstream attempt consumes one request.
                gw.record_admission(base)
                admitted = True

            headers = {k: v for k, v in request.headers.items()
                       if k.lower() not in HOP_BY_HOP and k.lower() not in ("authorization", "x-mesocosm-agent",
                                                                              "x-mesocosm-run")}
            if route.key:                           # the gateway authenticates to the inference server itself
                headers["authorization"] = f"Bearer {route.key}"
            headers["x-mesocosm-request-id"] = request_id
            if is_chat:
                headers["content-length"] = str(len(body_bytes))

            # Validate the same listing for HEAD; its public response below has no body.
            upstream_req = route.client.build_request("GET" if is_models else request.method, "/" + path,
                                                      content=b"" if is_models else body_bytes,
                                                      headers=headers, params=request.query_params)
            try:
                upstream = await route.client.send(upstream_req, stream=True)
                transport_closed = False
            except httpx.HTTPError as e:
                gw.log({**base, "event": "upstream_error", "error": repr(e), "request": body})
                return refusal("upstream")

            if is_models:
                try:
                    try:
                        content = await upstream.aread()
                    finally:
                        await upstream.aclose()
                        transport_closed = True
                    if upstream.status_code != 200:
                        raise ValueError(f"upstream model listing status {upstream.status_code}")
                    public = public_model_list(content, bound_model)
                except (httpx.HTTPError, ValueError) as exc:
                    gw.log({**base, "event": "upstream_error", "error": repr(exc), "status": upstream.status_code})
                    return refusal("upstream")
                gw.log({**base, "event": "passthrough", "status": 200,
                        "latency_ms": round((time.time() - t0) * 1000)})
                return (Response(media_type="application/json") if request.method == "HEAD"
                        else JSONResponse(public))

            resp_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in HOP_BY_HOP}

            if is_chat and body is not None and body.get("stream"):
                chunks: list[bytes] = []
                finalized = False

                async def finish_stream():
                    nonlocal finalized, response_usage, transport_closed, usage_recorded
                    if finalized:
                        return
                    finalized = True
                    # Account and log before an awaited close: disconnect cancellation cannot skip them.
                    try:
                        raw = b"".join(chunks).decode("utf-8", "replace")
                        parsed = parse_sse(raw) if upstream.status_code < 400 else {"raw": raw[:4000]}
                        response_usage = parsed.get("usage")
                        gw.add_usage(agent, parsed.get("usage"))
                        usage_recorded = True
                        gw.log({**base, "event": "chat_completion", "stream": True, "status": upstream.status_code,
                                "latency_ms": round((time.time() - t0) * 1000), "request": body,
                                "request_summary": request_summary(body), "response": parsed,
                                "usage_total": gw.usage_for(agent), "missing_usage": agent in gw.missing_usage})
                    finally:
                        try:
                            await upstream.aclose()
                            transport_closed = True
                        finally:
                            release()

                async def stream():
                    try:
                        async for chunk in upstream.aiter_raw():
                            chunks.append(chunk)
                            yield chunk
                    finally:
                        await finish_stream()
                streaming = True
                return StreamingResponse(stream(), status_code=upstream.status_code, headers=resp_headers,
                                         media_type=upstream.headers.get("content-type"),
                                         background=BackgroundTask(finish_stream))

            try:
                content = await upstream.aread()
            except httpx.HTTPError as exc:
                gw.log({**base, "event": "upstream_error", "error": repr(exc), "status": upstream.status_code,
                        "request": body, "response_complete": False})
                return refusal("upstream")
            finally:
                await upstream.aclose()
                transport_closed = True
            resp_headers.pop("content-encoding", None)  # httpx already decoded the body
            if is_chat:
                assert body is not None
                try:
                    data = json.loads(content)
                except json.JSONDecodeError:
                    data = None
                if isinstance(data, dict) and "choices" in data:
                    parsed = summarize_response(data)
                else:
                    parsed = {"raw": content[:2000].decode("utf-8", "replace")}
                gw.add_usage(agent, data.get("usage") if isinstance(data, dict) else None)
                usage_recorded = True
                response_usage = data.get("usage") if isinstance(data, dict) else None
                gw.log({**base, "event": "chat_completion", "stream": False, "status": upstream.status_code,
                        "latency_ms": round((time.time() - t0) * 1000), "request": body,
                        "request_summary": request_summary(body), "response": parsed,
                        "usage_total": gw.usage_for(agent), "missing_usage": agent in gw.missing_usage})
            return Response(content=content, status_code=upstream.status_code, headers=resp_headers,
                            media_type=upstream.headers.get("content-type"))
        finally:
            if not streaming:
                if admitted and not usage_recorded:
                    gw.add_usage(agent, None)
                    gw.log({**base, "event": "request_usage_unknown", "usage_total": gw.usage_for(agent),
                            "missing_usage": True})
                release()

    return app


def main() -> None:
    import uvicorn

    uvicorn.run(create_app(), host=os.environ.get("MESOCOSM_GATEWAY_HOST", "127.0.0.1"),
                port=int(os.environ.get("MESOCOSM_GATEWAY_PORT", "8080")))
