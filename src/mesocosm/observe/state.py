"""Live view of a run, built from the run bundle and the gateway log (ARCHITECTURE.md, observation).

`Observer` is a pure state machine: feed it bundle events (`events.jsonl`), receipts
(`receipts.jsonl`) and gateway records (`gateway.jsonl`) in any interleaving and read
`snapshot()` for the screen, `session_details()` / `request_record()` for the drill-down.
The same code serves a live tail and an offline replay.

Everything it keeps is bounded (deques and capped ordered dicts), so a long run cannot grow the
observer without limit; what falls off the end is still in the files on disk.
"""

from __future__ import annotations

import pathlib
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Any

TIMELINE_MAX = 2000      # text/status/file items kept per session
TOOLS_MAX = 500          # tool calls kept per session (latest state per call id)
REQUESTS_MAX = 1000      # model-request summaries kept per agent
INTERACTIONS_MAX = 500   # read-after-write pairs between agents kept per run
RUN_START_SLACK = 5.0    # seconds of clock skew allowed between the control plane and the gateway/services hosts
WRITERS_MAX = 5000       # (service, key) -> writer entries kept for joining reads to writes
REQUEST_BODIES_MAX = 50  # full request/response records kept per agent
SESSIONS_MAX = 5000      # sessions kept per run; finished ones are evicted first
LIVE_TEXT_MAX = 2000     # characters of streaming text kept per session
SUMMARY_LEN = 90

# Session status vocabulary used on screen. OpenCode reports busy | idle | retry.
IDLE, BUSY, TOOL, ERROR, DONE = "idle", "busy", "tool", "error", "done"
# Agent status adds blocked (budget exhausted) on top of the session vocabulary.
BLOCKED = "blocked"


def _put(od: OrderedDict, key: Any, value: Any, maxlen: int) -> None:
    """Insert or replace; evict the oldest key beyond maxlen."""
    if key in od:
        od.move_to_end(key)
    od[key] = value
    while len(od) > maxlen:
        od.popitem(last=False)


def _clip(text: str, n: int = SUMMARY_LEN) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def tool_input_summary(tool: str | None, inp: dict[str, Any] | None) -> str:
    """One readable phrase for a tool call's input, per OpenCode tool."""
    inp = inp or {}
    if tool == "bash":
        return _clip(inp.get("command") or inp.get("description") or "", 70)
    if tool in ("read", "write", "edit"):
        path = inp.get("filePath") or inp.get("path") or ""
        return pathlib.PurePosixPath(path).name if path else ""
    if tool in ("glob", "grep"):
        return _clip(inp.get("pattern") or "", 60)
    if tool == "task":
        return _clip(inp.get("description") or inp.get("prompt") or "", 70)
    if tool == "webfetch":
        return _clip(inp.get("url") or "", 70)
    if tool == "todowrite":
        return f"{len(inp.get('todos') or [])} todos"
    for v in inp.values():
        if isinstance(v, str) and v:
            return _clip(v, 60)
    return ""


def tool_phrase(tool: str | None, input_summary: str | None) -> str:
    arg = input_summary or ""
    verbs = {"bash": "Running", "read": "Reading", "write": "Writing", "edit": "Editing", "glob": "Searching for",
             "grep": "Searching for", "task": "Delegating:", "webfetch": "Fetching", "todowrite": "Updating the plan",
             "question": "Asking the user", "skill": "Loading a skill"}
    verb = verbs.get(tool or "", f"Using {tool}")
    if tool == "todowrite" or not arg:
        return verb.rstrip(":")
    return f"{verb} {arg}"


@dataclass
class SessionNode:
    session_id: str
    agent_id: str | None = None
    parent_id: str | None = None
    title: str = ""
    status: str = IDLE
    created_ts: float | None = None
    updated_ts: float | None = None
    tokens: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    live_text: str = ""
    children: list[str] = field(default_factory=list)
    tools: OrderedDict = field(default_factory=OrderedDict)     # call_id -> tool item
    items: deque = field(default_factory=lambda: deque(maxlen=TIMELINE_MAX))
    last_activity: dict[str, Any] | None = None                # the item the summary is built from
    model_summary: tuple[str, str] | None = None               # (activity signature, model-written line)
    tool_calls_total: int = 0
    text_parts_total: int = 0

    def rule_summary(self) -> str:
        """Plain words, present tense, no jargon: what this session is doing right now."""
        if self.status == ERROR and self.error:
            return _clip(f"Stopped: {self.error}")
        act = self.last_activity
        if act is None:
            return "Waiting for the model" if self.status == BUSY else "Idle"
        if act["kind"] == "tool":
            if act["status"] in ("pending", "running"):
                return _clip(tool_phrase(act["tool"], act.get("input_summary")))
            if self.status == BUSY:
                return "Waiting for the model"
            return _clip("Finished: " + tool_phrase(act["tool"], act.get("input_summary")).lower())
        if act["kind"] == "text":
            return _clip(act["text"].split(". ")[0], 70)
        if act["kind"] == "reasoning":
            return "Thinking"
        if act["kind"] == "file":
            return _clip(f"Edited {act['path']}")
        return "Waiting for the model" if self.status == BUSY else "Idle"

    def summary(self) -> str:
        """The model-written line when it matches the current activity, else the rule-based one."""
        if self.model_summary and self.model_summary[0] == self.signature():
            return self.model_summary[1]
        return self.rule_summary()

    def summary_source(self) -> str:
        return "model" if (self.model_summary and self.model_summary[0] == self.signature()) else "rules"

    def signature(self) -> str:
        from mesocosm.observe.summarize import signature  # local import: summarize imports this module
        return signature(self)

    def to_dict(self) -> dict[str, Any]:
        running = [t for t in self.tools.values() if t["status"] in ("pending", "running")]
        return {
            "session_id": self.session_id, "agent_id": self.agent_id, "parent_id": self.parent_id,
            "title": self.title, "status": TOOL if (self.status == BUSY and running) else self.status,
            "summary": self.summary(), "summary_source": self.summary_source(), "children": list(self.children),
            "created_ts": self.created_ts, "updated_ts": self.updated_ts, "tokens": self.tokens,
            "tool_calls": self.tool_calls_total, "text_parts": self.text_parts_total,
            "running_tool": running[-1]["tool"] if running else None,
        }


@dataclass
class AgentState:
    agent_id: str
    task: str | None = None
    budget: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)
    blocked: str | None = None               # budget dimension that ran out
    rewards: list[dict[str, Any]] = field(default_factory=list)
    root_sessions: list[str] = field(default_factory=list)
    requests: deque = field(default_factory=lambda: deque(maxlen=REQUESTS_MAX))
    request_bodies: OrderedDict = field(default_factory=OrderedDict)
    requests_total: int = 0
    completion_tokens_total: int = 0
    last_request_ts: float | None = None
    last_error: str | None = None


class Observer:
    def __init__(self) -> None:
        self.run_id: str | None = None
        self.started_ts: float | None = None
        self.last_ts: float | None = None
        self.status: str = "running"
        self.ingested = 0
        self.agents: dict[str, AgentState] = {}
        self.sessions: dict[str, SessionNode] = {}
        self.known_agents: set[str] = set()      # from manifest.json; empty = accept every caller
        self.ignored_records = 0                 # gateway records from callers outside the manifest
        self._seen_requests: set[str] = set()
        self.interactions: deque = deque(maxlen=INTERACTIONS_MAX)   # who read whose writing, through which service
        self._writers: OrderedDict = OrderedDict()                  # (service, key) -> writer agent id
        self.service_records = 0
        self._live_services = False              # set by the server when it tails services.jsonl directly
        self.run_conditions: dict[str, Any] = {} # conditions enforced at run level (channels), not per agent
        self.run_started: float | None = None    # first bundle event; older shared-log records are earlier runs

    # -- ingestion ------------------------------------------------------------------------

    def apply_bundle_event(self, ev: dict[str, Any]) -> None:
        """One line of events.jsonl (core.records.Event as JSON)."""
        self.ingested += 1
        ts = ev.get("ts")
        self._tick(ts)
        if self.run_id is None:
            self.run_id = ev.get("run_id")
        if self.run_started is None and ts is not None:
            self.run_started = ts - RUN_START_SLACK
        kind = ev.get("kind") or ""
        data = ev.get("data") or {}
        agent_id = ev.get("agent_id")
        if kind.startswith("opencode."):
            self._apply_opencode(kind[len("opencode."):], data, agent_id, ts)
        elif kind == "harness.session":
            node = self._session(data.get("session_id"), ts)
            node.agent_id = agent_id
            node.title = node.title or data.get("task") or ""
            agent = self._agent(agent_id)
            agent.task = data.get("task") or agent.task
            if node.session_id not in agent.root_sessions:
                agent.root_sessions.append(node.session_id)
        elif kind == "harness.reply":
            node = self._session(data.get("session_id"), ts)
            if node.status == BUSY:
                node.status = IDLE
        elif kind == "conditions.set":
            if agent_id:
                self._agent(agent_id).budget[data.get("condition", "?")] = data.get("result")
            else:
                self.run_conditions[data.get("condition", "?")] = {k: v for k, v in data.items() if k != "condition"}
        elif kind == "verifier.reward":
            agent = self._agent(agent_id)
            agent.rewards.append({"task": data.get("task"), "reward": data.get("reward"), "ts": ts})
        elif kind == "task.loaded":
            pass
        elif kind in ("run.failed", "run.aborted"):
            self.status = "failed" if kind == "run.failed" else data.get("reason") or "aborted"
        elif kind.startswith("service."):
            if not self._live_services:          # the end-of-run copy counts only when no live log is tailed
                self.apply_service_record({"ts": ts, "agent": agent_id, "op": kind[len("service."):], **data})
        if kind == "harness.events_ended" and agent_id:
            pass

    def apply_service_record(self, rec: dict[str, Any]) -> None:
        """One line of the world services' services.jsonl (or a `service.*` bundle event)."""
        self.ingested += 1
        ts = rec.get("ts")
        if self._before_run(ts):
            return
        self.service_records += 1
        self._tick(ts)
        agent_id = rec.get("agent") or "unknown"
        service, op = rec.get("service") or "?", rec.get("op") or "?"
        key = rec.get("post_id") or rec.get("paste_id") or rec.get("doc") or rec.get("repo") or rec.get("package") \
            or rec.get("file") or rec.get("site")
        if self.known_agents and agent_id not in self.known_agents:
            self.ignored_records += 1
            return
        agent = self._agent(agent_id)
        item = {"ts": ts, "kind": "service", "service": service, "op": op, "key": key,
                "summary": rec.get("summary"), "sha256": rec.get("sha256")}
        session = self._active_session(agent)
        node = self.sessions.get(session) if session else None
        if node is not None:
            node.items.append(item)
        if key is not None and op == "write":
            _put(self._writers, (service, str(key)), agent_id, WRITERS_MAX)
        elif key is not None and op == "read":
            writer = self._writers.get((service, str(key)))
            if writer and writer != agent_id:
                self.interactions.append({"ts": ts, "from": writer, "to": agent_id, "service": service, "key": str(key),
                                          "sha256": rec.get("sha256")})

    def apply_manifest(self, manifest_json: dict[str, Any]) -> None:
        """manifest.json of the bundle: the agents that belong to this run."""
        spec = manifest_json.get("manifest") or manifest_json
        self.known_agents = {a.get("id") for a in spec.get("agents") or [] if a.get("id")}
        for agent_id in sorted(self.known_agents):
            self._agent(agent_id)

    def apply_summary(self, summary_json: dict[str, Any]) -> None:
        """summary.json, written once when the run ends."""
        self.status = summary_json.get("status") or self.status

    def apply_receipt(self, receipt: dict[str, Any]) -> None:
        self.ingested += 1
        ts = (receipt.get("enforced_at") or {}).get("wallclock")
        self._tick(ts)
        requested = receipt.get("requested") or {}
        agent_id = requested.get("agent_id") or receipt.get("agent_id")
        if agent_id:
            agent = self._agent(agent_id)
            agent.budget.update({k: v for k, v in requested.items() if k != "agent_id"})
            agent.budget["receipt"] = receipt.get("result")

    def apply_gateway_record(self, rec: dict[str, Any]) -> None:
        """One line of the gateway's gateway.jsonl."""
        self.ingested += 1
        ts = rec.get("ts")
        if self._before_run(ts):
            return
        self._tick(ts)
        event = rec.get("event")
        if event == "receipt":
            return
        agent_id = rec.get("agent") or "unknown"
        if self.known_agents and agent_id not in self.known_agents:
            self.ignored_records += 1          # e.g. a curl smoke test sharing the gateway log
            return
        rid = rec.get("request_id")
        if rid and rid in self._seen_requests:
            return
        if rid:
            self._seen_requests.add(rid)
            if len(self._seen_requests) > REQUESTS_MAX * 4:
                self._seen_requests = set(list(self._seen_requests)[-REQUESTS_MAX * 2:])
        agent = self._agent(agent_id)
        resp = rec.get("response") or {}
        summary = rec.get("request_summary") or {}
        usage = resp.get("usage") or {}
        item = {
            "request_id": rid, "ts": ts, "event": event, "status": rec.get("status"),
            "latency_ms": rec.get("latency_ms"), "path": rec.get("path"),
            "n_messages": summary.get("n_messages"), "n_tools": summary.get("n_tools"),
            "tool_calls": [((c.get("function") or {}).get("name")) for c in (resp.get("tool_calls") or [])],
            "finish_reason": resp.get("finish_reason"),
            "prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens"),
            "has_token_ids": bool(resp.get("prompt_token_ids")) or bool(resp.get("token_ids")),
            "reasoning_chars": len(resp.get("reasoning") or ""),
            "content_chars": len(resp.get("content") or ""),
            "session_hint": self._active_session(agent),
        }
        agent.requests.append(item)
        agent.requests_total += 1
        agent.last_request_ts = ts
        if usage.get("completion_tokens"):
            agent.completion_tokens_total += int(usage["completion_tokens"])
        if rid:
            _put(agent.request_bodies, rid, rec, REQUEST_BODIES_MAX)
        if event == "budget_exhausted":
            agent.blocked = rec.get("dimension") or "budget"
        elif event == "upstream_error":
            agent.last_error = rec.get("error")

    # -- reads ----------------------------------------------------------------------------

    def agent_status(self, agent: AgentState) -> str:
        if agent.blocked:
            return BLOCKED
        nodes = [self.sessions[s] for s in self._agent_sessions(agent.agent_id)]
        if any(n.status == ERROR for n in nodes):
            return ERROR
        busy = [n for n in nodes if n.status == BUSY]
        if busy:
            latest = max(busy, key=lambda n: n.updated_ts or 0)
            return latest.to_dict()["status"]
        if agent.rewards and not busy:
            return DONE
        return IDLE

    def agent_summary(self, agent: AgentState) -> str:
        if agent.blocked:
            return f"Stopped: budget used up ({agent.blocked})"
        nodes = [self.sessions[s] for s in self._agent_sessions(agent.agent_id)]
        if not nodes:
            return "Starting"
        busy = [n for n in nodes if n.status == BUSY]
        pick = max(busy or nodes, key=lambda n: n.updated_ts or 0)
        line = pick.summary()
        if not busy and agent.rewards and not line.lower().startswith(("done", "stopped", "finished")):
            head = line.split(" ", 1)[0]
            body = line if head == "I" or head.isupper() else line[0].lower() + line[1:]
            return _clip("Done: " + body, 90)
        return _clip(("Subagent: " if pick.parent_id else "") + line, 90)

    def agent_summary_source(self, agent: AgentState) -> str:
        nodes = [self.sessions[s] for s in self._agent_sessions(agent.agent_id)]
        if agent.blocked or not nodes:
            return "rules"
        busy = [n for n in nodes if n.status == BUSY]
        return max(busy or nodes, key=lambda n: n.updated_ts or 0).summary_source()

    def snapshot(self) -> dict[str, Any]:
        agents = []
        for agent in self.agents.values():
            agents.append({
                "agent_id": agent.agent_id, "task": agent.task, "status": self.agent_status(agent),
                "summary": self.agent_summary(agent), "summary_source": self.agent_summary_source(agent),
                "budget": agent.budget,
                "requests": agent.requests_total, "completion_tokens": agent.completion_tokens_total,
                "rewards": agent.rewards, "root_sessions": list(agent.root_sessions),
                "last_request_ts": agent.last_request_ts, "last_error": agent.last_error,
            })
        return {
            "run_id": self.run_id, "status": self.status, "started_ts": self.started_ts,
            "last_ts": self.last_ts, "ingested": self.ingested, "ignored_records": self.ignored_records,
            "service_records": self.service_records, "interactions": list(self.interactions),
            "run_conditions": self.run_conditions,
            "agents": agents,
            "sessions": {sid: n.to_dict() for sid, n in self.sessions.items()},
        }

    def session_details(self, session_id: str) -> dict[str, Any] | None:
        node = self.sessions.get(session_id)
        if node is None:
            return None
        items = sorted(list(node.items) + list(node.tools.values()), key=lambda i: i.get("ts") or 0)
        agent = self.agents.get(node.agent_id or "")
        requests = [r for r in agent.requests if r.get("session_hint") == session_id] if agent else []
        return {**node.to_dict(), "live_text": node.live_text, "items": items, "requests": requests,
                "error": node.error}

    def agent_requests(self, agent_id: str) -> list[dict[str, Any]]:
        agent = self.agents.get(agent_id)
        return list(agent.requests) if agent else []

    def request_record(self, agent_id: str, request_id: str) -> dict[str, Any] | None:
        agent = self.agents.get(agent_id)
        return agent.request_bodies.get(request_id) if agent else None

    # -- internals ------------------------------------------------------------------------

    def _before_run(self, ts: float | None) -> bool:
        """Shared gateway/services logs carry earlier runs; drop what predates this bundle's first event."""
        if self.run_started is not None and ts is not None and ts < self.run_started:
            self.ignored_records += 1
            return True
        return False

    def _tick(self, ts: float | None) -> None:
        if ts is None:
            return
        if self.started_ts is None or ts < self.started_ts:
            self.started_ts = ts
        if self.last_ts is None or ts > self.last_ts:
            self.last_ts = ts

    def _agent(self, agent_id: str | None) -> AgentState:
        key = agent_id or "unknown"
        if key not in self.agents:
            self.agents[key] = AgentState(agent_id=key)
        return self.agents[key]

    def _session(self, session_id: str | None, ts: float | None) -> SessionNode:
        key = session_id or "unknown"
        node = self.sessions.get(key)
        if node is None:
            node = SessionNode(session_id=key, created_ts=ts)
            self.sessions[key] = node
            self._evict_sessions()
        if ts is not None:
            node.updated_ts = ts
        return node

    def _evict_sessions(self) -> None:
        if len(self.sessions) <= SESSIONS_MAX:
            return
        finished = sorted((n for n in self.sessions.values() if n.status != BUSY and not n.children),
                          key=lambda n: n.updated_ts or 0)
        for node in finished[: len(self.sessions) - SESSIONS_MAX]:
            parent = self.sessions.get(node.parent_id or "")
            if parent and node.session_id in parent.children:
                parent.children.remove(node.session_id)
            agent = self.agents.get(node.agent_id or "")
            if agent and node.session_id in agent.root_sessions:
                agent.root_sessions.remove(node.session_id)
            del self.sessions[node.session_id]

    def _attach_root(self, node: SessionNode) -> None:
        """A session that names an agent but has no parent is one of that agent's root sessions,
        even if its `harness.session` record has not been seen (replay order, gateway-first)."""
        if node.agent_id and not node.parent_id:
            agent = self._agent(node.agent_id)
            if node.session_id not in agent.root_sessions:
                agent.root_sessions.append(node.session_id)

    def _agent_sessions(self, agent_id: str) -> list[str]:
        return [sid for sid, n in self.sessions.items() if n.agent_id == agent_id]

    def _active_session(self, agent: AgentState) -> str | None:
        busy = [self.sessions[s] for s in self._agent_sessions(agent.agent_id) if self.sessions[s].status == BUSY]
        if not busy:
            return None
        return max(busy, key=lambda n: n.updated_ts or 0).session_id

    def _apply_opencode(self, etype: str, data: dict[str, Any], agent_id: str | None, ts: float | None) -> None:
        raw = data.get("raw") or {}
        props = raw.get("properties") or {}
        sid = data.get("session_id")
        if etype == "session.created":
            info = props.get("info") or {}
            node = self._session(sid, ts)
            node.title = info.get("title") or node.title
            parent_id = info.get("parentID")
            if parent_id:
                node.parent_id = parent_id
                parent = self._session(parent_id, ts)
                if sid not in parent.children:
                    parent.children.append(sid)
                node.agent_id = node.agent_id or parent.agent_id
                agent = self.agents.get(node.agent_id or "")
                if agent and sid in agent.root_sessions:      # seen before its parent was known
                    agent.root_sessions.remove(sid)
            if agent_id:
                node.agent_id = agent_id
            self._attach_root(node)
            return
        if not sid:
            return
        node = self._session(sid, ts)
        if agent_id and not node.agent_id:
            node.agent_id = agent_id
        self._attach_root(node)
        if etype == "session.status":
            status = (props.get("status") or {}).get("type")
            if status == "busy":
                node.status = BUSY
            elif status == "idle":
                node.status = IDLE
            elif status == "retry":
                node.status = BUSY
                node.items.append({"ts": ts, "kind": "status", "text": "retry"})
        elif etype == "session.error":
            node.status = ERROR
            err = props.get("error") or {}
            node.error = _clip(str((err.get("data") or {}).get("message") or err.get("name") or err), 200)
            node.items.append({"ts": ts, "kind": "error", "text": node.error})
        elif etype == "message.part.updated":
            self._apply_part(node, props.get("part") or {}, ts)
        elif etype == "message.part.delta":
            delta = props.get("delta") or props.get("text") or ""
            if isinstance(delta, str) and delta:
                node.live_text = (node.live_text + delta)[-LIVE_TEXT_MAX:]
        elif etype == "message.updated":
            info = props.get("info") or {}
            if info.get("tokens"):
                node.tokens = info["tokens"]
            if info.get("role") == "user":
                node.live_text = ""
        elif etype == "file.edited":
            path = props.get("file") or props.get("path") or ""
            item = {"ts": ts, "kind": "file", "path": pathlib.PurePosixPath(str(path)).name or str(path)}
            node.items.append(item)
            node.last_activity = item
        elif etype == "session.updated":
            info = props.get("info") or {}
            node.title = info.get("title") or node.title
            if info.get("tokens"):
                node.tokens = info["tokens"]

    def _apply_part(self, node: SessionNode, part: dict[str, Any], ts: float | None) -> None:
        ptype = part.get("type")
        if ptype == "tool":
            state = part.get("state") or {}
            call_id = part.get("callID") or part.get("id")
            status = state.get("status") or "pending"
            prev = node.tools.get(call_id)
            item = {
                "ts": prev["ts"] if prev else ts, "updated_ts": ts, "kind": "tool", "call_id": call_id,
                "tool": part.get("tool"), "status": status,
                "input_summary": tool_input_summary(part.get("tool"), state.get("input")),
                "title": state.get("title"),
                "output": _clip(state.get("output") or "", 400) if status == "completed" else None,
                "error": state.get("error") if status == "error" else None,
            }
            if prev is None:
                node.tool_calls_total += 1
            _put(node.tools, call_id, item, TOOLS_MAX)
            node.last_activity = item
            if status == "error":
                node.items.append({"ts": ts, "kind": "error", "text": _clip(str(state.get("error")), 200)})
        elif ptype == "text":
            text = part.get("text") or ""
            if not text:
                return
            item = {"ts": ts, "kind": "text", "part_id": part.get("id"), "text": _clip(text, 1000)}
            if node.items and node.items[-1].get("part_id") == part.get("id"):
                node.items[-1] = item
            else:
                node.items.append(item)
                node.text_parts_total += 1
            node.last_activity = item
            node.live_text = ""
        elif ptype == "reasoning":
            text = part.get("text") or ""
            if text:
                item = {"ts": ts, "kind": "reasoning", "part_id": part.get("id"), "text": _clip(text, 1000)}
                if node.items and node.items[-1].get("part_id") == part.get("id"):
                    node.items[-1] = item
                else:
                    node.items.append(item)
                node.last_activity = item
        elif ptype in ("step-start", "step-finish"):
            node.items.append({"ts": ts, "kind": "status", "text": ptype})
