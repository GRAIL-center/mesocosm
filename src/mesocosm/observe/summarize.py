"""Model-written status lines for the live view (ADR-0008: derived, labeled, kept apart from records).

A `Summarizer` asks an OpenAI-compatible chat endpoint for one short plain line per session node
whenever the node's activity signature changes. Results are cached by signature and appended to a
derived file next to the bundle, so a replay shows the same lines without calling the model again.
Rule-based summaries (state.SessionNode.summary) remain the fallback and the model's input.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import pathlib
import time
from collections import OrderedDict
from typing import Any

import httpx

from mesocosm.observe import state as S

CACHE_MAX = 4000
MAX_WORDS = 8
MIN_INTERVAL = 2.0          # seconds between model calls for the same node
CONCURRENCY = 2

SYSTEM = (
    "You write the status line for one AI coding agent on a monitoring dashboard.\n"
    "Reply with ONE short line: at most {max_words} words, present tense, plain words that a non-engineer "
    "understands, no quotes, no trailing period, no agent or file-system jargon unless it is the point.\n"
    "Say what the agent is doing right now and, when it is clear from the actions, what for.\n"
    "Good lines: 'Searching git history for the lost commits' / 'Writing the notes file' / "
    "'Waiting for the model' / 'Stopped: token budget used up' / 'Done: answer written to a file'.\n"
    "{language}"
)
LANGUAGES = {"en": "Write in English.", "ko": "한국어로, 20자 안팎의 짧은 한 줄로 쓰세요. 마침표는 쓰지 마세요."}


def signature(node: S.SessionNode) -> str:
    act = node.last_activity or {}
    key = json.dumps([node.status, node.error, act.get("kind"), act.get("tool"), act.get("input_summary"),
                      act.get("status"), (act.get("text") or "")[:80], act.get("path")], ensure_ascii=False)
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def context_for(node: S.SessionNode, task: str | None) -> str:
    """What the model gets: status, the last few actions oldest first, the latest text."""
    names = {S.BUSY: "waiting for the model", S.IDLE: "idle", S.ERROR: "error", S.DONE: "done"}
    status = names.get(node.status, node.status)
    running = [t for t in node.tools.values() if t["status"] in ("pending", "running")]
    if running:
        status = "running a tool"
    lines = [f"Task given to the agent: {task or 'unknown'}", f"Status: {status}"]
    said = [i for i in node.items if i.get("kind") in ("text", "file", "error")][-3:]
    recent = sorted(list(node.tools.values())[-6:] + said, key=lambda i: i.get("ts") or 0)[-8:]
    if recent:
        lines.append("Recent actions, oldest first:")
    for it in recent:
        if it["kind"] == "tool":
            out = f" -> {it['output'][:120]}" if it.get("output") else ""
            lines.append(f"- {it['tool']} {it.get('input_summary') or ''} [{it['status']}]{out}")
        elif it["kind"] == "text":
            lines.append(f"- agent said: {it['text'][:200]}")
        elif it["kind"] == "file":
            lines.append(f"- edited file {it['path']}")
        elif it["kind"] == "error":
            lines.append(f"- error: {it['text'][:160]}")
    if node.parent_id:
        lines.append("This is a subagent working on a delegated part of the task.")
    return "\n".join(lines)


def clean(text: str) -> str:
    line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    line = line.strip().strip('"“”\'`').rstrip(".。").strip()
    words = line.split()
    if len(words) > MAX_WORDS + 4:
        line = " ".join(words[: MAX_WORDS + 4]) + "…"
    return S._clip(line, 70)


class Summarizer:
    def __init__(self, url: str, model: str, api_key: str = "", lang: str = "en",
                 derived_path: pathlib.Path | None = None, client: httpx.AsyncClient | None = None):
        self.url = url.rstrip("/")
        self.model = model
        self.lang = lang if lang in LANGUAGES else "en"
        self.derived_path = derived_path
        self.client = client or httpx.AsyncClient(timeout=30.0)
        if api_key:
            self.client.headers["Authorization"] = f"Bearer {api_key}"
        self.cache: OrderedDict[str, str] = OrderedDict()      # signature -> text
        self.last_call: dict[str, float] = {}                  # session id -> ts
        self.pending: set[str] = set()
        self.sem = asyncio.Semaphore(CONCURRENCY)
        self.calls = 0
        self.failures = 0
        self.last_error: str | None = None
        if derived_path and derived_path.exists():
            for line in derived_path.read_text(encoding="utf-8").splitlines():
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("signature") and rec.get("text"):
                    S._put(self.cache, rec["signature"], rec["text"], CACHE_MAX)

    def apply_cached(self, observer: S.Observer) -> None:
        """Attach cached lines to nodes whose current signature we already summarized."""
        for node in observer.sessions.values():
            sig = signature(node)
            text = self.cache.get(sig)
            if text:
                node.model_summary = (sig, text)

    def due(self, observer: S.Observer, now: float | None = None) -> list[S.SessionNode]:
        now = now or time.time()
        out = []
        for node in observer.sessions.values():
            sig = signature(node)
            if node.model_summary and node.model_summary[0] == sig:
                continue
            if sig in self.cache:
                node.model_summary = (sig, self.cache[sig])
                continue
            if node.session_id in self.pending or now - self.last_call.get(node.session_id, 0) < MIN_INTERVAL:
                continue
            if node.last_activity is None and node.status != S.ERROR:
                continue                       # nothing to say beyond the rule text
            out.append(node)
        return out

    async def summarize(self, node: S.SessionNode, task: str | None) -> str | None:
        sig = signature(node)
        self.pending.add(node.session_id)
        try:
            async with self.sem:
                self.last_call[node.session_id] = time.time()
                system = SYSTEM.format(max_words=MAX_WORDS, language=LANGUAGES[self.lang])
                body = {"model": self.model, "temperature": 0.2, "max_tokens": 40,
                        "messages": [{"role": "system", "content": system},
                                     {"role": "user", "content": context_for(node, task)}]}
                r = await self.client.post(f"{self.url}/chat/completions", json=body)
                r.raise_for_status()
                text = clean(((r.json().get("choices") or [{}])[0].get("message") or {}).get("content") or "")
                self.calls += 1
        except Exception as e:  # noqa: BLE001 - a summarizer failure must never break the view
            self.failures += 1
            self.last_error = repr(e)[:200]
            return None
        finally:
            self.pending.discard(node.session_id)
        if not text:
            return None
        S._put(self.cache, sig, text, CACHE_MAX)
        if signature(node) == sig:
            node.model_summary = (sig, text)
        if self.derived_path:
            self.derived_path.parent.mkdir(parents=True, exist_ok=True)
            with self.derived_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": time.time(), "session_id": node.session_id, "signature": sig, "text": text,
                                    "model": self.model, "lang": self.lang}, ensure_ascii=False) + "\n")
        return text

    def stats(self) -> dict[str, Any]:
        return {"model": self.model, "lang": self.lang, "calls": self.calls, "failures": self.failures,
                "cached": len(self.cache), "last_error": self.last_error}

    async def aclose(self) -> None:
        await self.client.aclose()
