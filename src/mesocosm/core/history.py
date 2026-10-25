"""Bounded observable episode history, reconstructed from original native records."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class HistorySpec(BaseModel):
    """Explicit context replay; no retained session, generated summary or weight update.

    ``transcript`` includes assistant visible responses and tool inputs/results only.
    User/system prompts are excluded because they can contain earlier replayed history.
    ``actions`` includes tool inputs/results only. Neither includes hidden reasoning.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    episodes: int = Field(default=0, ge=0)
    scope: Literal["transcript", "actions"] = "actions"
    max_chars: int = Field(default=60000, ge=100)


class HistoryRecordError(ValueError):
    """Native evidence cannot support the configured history selection."""


class HistoryCapacityError(ValueError):
    """The complete selected history exceeds the declared delivery limit."""

    def __init__(self, chars: int, max_chars: int):
        self.chars = chars
        self.max_chars = max_chars
        super().__init__(f"rejected_budget: episode history has {chars} characters; max_chars={max_chars}; "
                         "history was not truncated or delivered")


def _json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise HistoryRecordError("native history must contain finite JSON values") from exc


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _identifier(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise HistoryRecordError(f"native history requires a nonempty {name}")
    return value


def _select(messages: list[dict[str, Any]], session_id: str, scope: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    message_ids: set[str] = set()
    part_ids: set[str] = set()
    for message in messages:
        if not isinstance(message, dict) or not isinstance(message.get("info"), dict):
            raise HistoryRecordError("native history requires message info objects")
        info = message["info"]
        message_id = _identifier(info.get("id"), "message ID")
        if info.get("sessionID") != session_id:
            raise HistoryRecordError("native history contains another session")
        if message_id in message_ids:
            raise HistoryRecordError("native history contains duplicate message IDs")
        message_ids.add(message_id)
        if info.get("role") not in {"user", "assistant", "system"}:
            raise HistoryRecordError("native history contains an unknown message role")
        parts = message.get("parts")
        if not isinstance(parts, list) or not all(isinstance(part, dict) for part in parts):
            raise HistoryRecordError("native message parts must be objects")
        if info["role"] != "assistant":
            continue
        if info.get("summary") is True or info.get("agent") == "compaction" or info.get("mode") == "compaction":
            raise HistoryRecordError("compacted native history cannot provide exact episode records")
        for part in parts:
            kind = part.get("type")
            if kind not in {"text", "tool"} or (kind == "text" and scope == "actions"):
                continue
            if part.get("synthetic") is True or part.get("ignored") is True:
                continue
            part_id = _identifier(part.get("id"), "part ID")
            if part_id in part_ids:
                raise HistoryRecordError("native history contains duplicate selected part IDs")
            part_ids.add(part_id)
            record: dict[str, Any] = {"message_id": message_id, "part_id": part_id, "type": kind}
            if kind == "text":
                if not isinstance(part.get("text"), str):
                    raise HistoryRecordError("native visible text must be a string")
                record["text"] = part["text"]
            else:
                state = part.get("state")
                if not isinstance(state, dict) or state.get("status") not in {"completed", "error"}:
                    raise HistoryRecordError("episode history requires completed or failed native tools")
                if not isinstance(state.get("input"), dict):
                    raise HistoryRecordError("native tool history requires recorded input arguments")
                timing = state.get("time")
                if isinstance(timing, dict) and timing.get("compacted") is not None:
                    raise HistoryRecordError("pruned tool results cannot provide exact episode records")
                result = "output" if state["status"] == "completed" else "error"
                if not isinstance(state.get(result), str):
                    raise HistoryRecordError(f"native tool history requires its recorded {result}")
                record.update(tool=_identifier(part.get("tool"), "tool name"),
                              call_id=_identifier(part.get("callID"), "tool call ID"),
                              status=state["status"], input=state["input"], **{result: state[result]})
                if "attachments" in state:
                    if not isinstance(state["attachments"], list):
                        raise HistoryRecordError("native tool attachments must be a list")
                    record["attachments"] = state["attachments"]
            records.append(record)
    return records


@dataclass(frozen=True)
class HistoryContext:
    text: str
    evidence: dict[str, Any]


@dataclass(frozen=True)
class _Episode:
    payload: str
    evidence: str


class EpisodeHistory:
    """Per-agent last-k task records; callers record only completed task episodes.

    Each episode must have its own fresh native session. Context is built before the
    next prompt; overflow fails without silently dropping episodes or truncating data.
    Native input hashes use canonical finite JSON, not the native file's whitespace.
    """

    def __init__(self, spec: HistorySpec):
        self.spec = spec
        self._episodes: dict[str, deque[_Episode]] = defaultdict(lambda: deque(maxlen=spec.episodes))
        self._sessions: dict[str, set[str]] = defaultdict(set)

    def record(self, agent_id: str, task: str, session_id: str, messages: list[dict[str, Any]], *,
               task_sha256: str | None = None) -> dict[str, Any] | None:
        if self.spec.episodes == 0:
            return None
        _identifier(agent_id, "agent ID")
        _identifier(task, "task name")
        _identifier(session_id, "session ID")
        if not isinstance(messages, list) or not messages:
            raise HistoryRecordError("native history must be a nonempty list of messages")
        if task_sha256 is not None and re.fullmatch(r"[0-9a-f]{64}", task_sha256) is None:
            raise HistoryRecordError("task_sha256 must be a lowercase SHA256 digest")
        if session_id in self._sessions[agent_id]:
            raise HistoryRecordError("episode history requires a fresh session for every task")
        native_json = _json(messages)
        records = _select(messages, session_id, self.spec.scope)
        payload = _json({"task": task, "records": records})
        evidence = {"task": task, "task_sha256": task_sha256, "session_id": session_id,
                    "native_messages_sha256": _sha256(native_json), "selection_sha256": _sha256(payload),
                    "hash_format": "canonical-json-utf8-v1", "records": len(records),
                    "native_message_ids": [message["info"]["id"] for message in messages],
                    "message_ids": list(dict.fromkeys(record["message_id"] for record in records)),
                    "part_ids": [record["part_id"] for record in records]}
        self._episodes[agent_id].append(_Episode(payload, _json(evidence)))
        self._sessions[agent_id].add(session_id)
        return evidence

    def context(self, agent_id: str) -> HistoryContext | None:
        episodes = self._episodes.get(agent_id)
        if not episodes:
            return None
        description = ("Assistant visible responses and tool inputs/results; original prompts and hidden reasoning "
                       "are excluded." if self.spec.scope == "transcript" else "Tool inputs/results only.")
        payload = {"scope": self.spec.scope, "scope_description": description,
                   "episodes": [json.loads(episode.payload) for episode in episodes]}
        text = "Previous completed task records:\n" + _json(payload)
        if len(text) > self.spec.max_chars:
            raise HistoryCapacityError(len(text), self.spec.max_chars)
        evidence = {"scope": self.spec.scope, "episodes": [json.loads(episode.evidence) for episode in episodes],
                    "chars": len(text), "max_chars": self.spec.max_chars,
                    "payload_sha256": _sha256(text), "truncated": False}
        return HistoryContext(text=text, evidence=evidence)
