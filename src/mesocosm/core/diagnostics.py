"""Read-only, structural run diagnostics; agent prose never establishes task success.

Counts describe retained records, not capture completeness or causal root causes. Raw
prompts, generated text, tool arguments, error strings and credentials are never emitted.
"""

from __future__ import annotations

import json
import math
import pathlib
import re
from collections import Counter
from collections.abc import Iterator
from typing import Any

from mesocosm.core.records import RunBundle

MAX_RECORD_BYTES = 16 * 1024 * 1024
MAX_SNAPSHOT_BYTES = 64 * 1024 * 1024
FINISH_REASONS = {"stop", "length", "tool_calls", "tool-calls", "content_filter", "function_call", "error", "unknown"}
GATEWAY_EVENTS = {"budget_exhausted", "resource_denied", "auth_denied", "endpoint_denied", "invalid_request",
                  "rollout_denied", "run_stopped", "upstream_error", "hold_expired", "resource_close_unknown"}
STOP_EVENTS = {"harness.task_timeout", "harness.round_timeout", "harness.failed", "harness.aborted",
               "harness.abort_failed", "workload.termination"}


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _identifier(value: Any) -> str | None:
    return (value if isinstance(value, str) and value not in {".", ".."}
            and re.fullmatch(r"[\w.-]{1,128}", value) else None)


def _label(value: Any, allowed: set[str]) -> str:
    return value if isinstance(value, str) and value in allowed else "unknown"


def _safe_file(root: pathlib.Path, path: pathlib.Path) -> bool:
    return path.is_file() and not any(p.is_symlink() for p in (path, *path.parents) if p != root.parent) and (
        path.resolve().is_relative_to(root))


def _json_file(root: pathlib.Path, path: pathlib.Path, unknown: Counter[str]) -> Any:
    if not _safe_file(root, path):
        unknown["missing_or_unsafe_files"] += 1
        return None
    if path.stat().st_size > MAX_SNAPSHOT_BYTES:
        unknown["oversized_files"] += 1
        return None
    try:
        return json.loads(path.read_bytes())
    except (OSError, ValueError):
        unknown["unreadable_or_malformed_files"] += 1
        return None


def _json_lines(root: pathlib.Path, path: pathlib.Path, unknown: Counter[str]) -> Iterator[dict[str, Any]]:
    if not _safe_file(root, path):
        unknown["missing_or_unsafe_files"] += 1
        return
    with path.open("rb") as stream:
        while line := stream.readline(MAX_RECORD_BYTES + 1):
            if len(line) > MAX_RECORD_BYTES:
                while line and not line.endswith(b"\n"):
                    line = stream.readline(MAX_RECORD_BYTES + 1)
                unknown["oversized_records"] += 1
                continue
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                unknown["malformed_records"] += 1
                continue
            if not isinstance(record, dict):
                unknown["malformed_records"] += 1
                continue
            yield record


def _agent(agent_id: str) -> dict[str, Any]:
    return {"agent_id": agent_id, "model": Counter(), "model_finish_reasons": Counter(),
            "gateway_events": Counter(), "normalized_gateway_events": Counter(), "harness": Counter(),
            "harness_finish_reasons": Counter(),
            "harness_tool_states": Counter(), "stop_events": Counter(), "monitor": Counter(),
            "verifier": {}, "findings": [], "next_checks": []}


def _completion(agent: dict[str, Any], record: dict[str, Any]) -> None:
    model = agent["model"]
    model["completion_records"] += 1
    status = record.get("status")
    if type(status) is int and status >= 400:
        model["http_error_records"] += 1
    response = _mapping(record.get("response"))
    calls = response.get("tool_calls")
    valid = isinstance(calls, list) and all(isinstance(c, dict) and isinstance(c.get("function"), dict)
                                          for c in calls)
    errors = response.get("error") is not None or bool(response.get("stream_errors"))
    complete = (type(status) is int and 200 <= status < 300 and not errors
                and response.get("finish_reason") is not None
                and (record.get("stream") is False or response.get("stream_complete") is True))
    if valid and isinstance(calls, list):
        model["parsed_response_records"] += 1
        model["reported_tool_calls"] += len(calls)
        model["responses_with_tool_calls" if calls else "responses_without_tool_calls"] += 1
    else:
        model["unparsed_response_records"] += 1
    model["complete_response_records" if complete and valid else "incomplete_or_unknown_response_records"] += 1
    if errors:
        model["response_error_records"] += 1
    agent["model_finish_reasons"][_label(response.get("finish_reason"), FINISH_REASONS)] += 1
    n_tools = _mapping(record.get("request_summary")).get("n_tools")
    request = _mapping(record.get("request"))
    if isinstance(request.get("tools"), list):
        n_tools = len(request["tools"])
    model["requests_with_tools" if type(n_tools) is int and n_tools > 0 else
          "requests_without_tools" if n_tools == 0 and type(n_tools) is int else
          "request_tool_availability_unknown"] += 1


def _messages(agent: dict[str, Any], messages: Any, seen: dict[tuple[str, str], dict[str, Any] | None]) -> None:
    counts = agent["harness"]
    if not isinstance(messages, list):
        counts["malformed_snapshots"] += 1
        return
    counts["snapshots_read"] += 1
    for message in messages:
        if not isinstance(message, dict) or not isinstance(message.get("parts"), list):
            counts["malformed_messages"] += 1
            continue
        info = _mapping(message.get("info"))
        if info.get("role") not in {"user", "assistant"}:
            counts["malformed_messages"] += 1
            continue
        if info.get("role") != "assistant":
            continue
        session, message_id = _identifier(info.get("sessionID")), _identifier(info.get("id"))
        if not session or not message_id:
            counts["messages_missing_identity"] += 1
            continue
        key = (session, message_id)
        # Carried sessions appear in several snapshots; a later partial snapshot must
        # not erase an earlier terminal state. Conflicting terminal data stays unknown.
        if key in seen and seen[key] != message:
            counts["conflicting_message_snapshots"] += 1
            seen[key] = None
            continue
        seen[key] = message


def diagnose_bundle(bundle_path: str | pathlib.Path) -> dict[str, Any]:
    """Summarize raw model output, harness observations and verifier outcomes separately."""
    bundle = RunBundle.open(bundle_path)
    root = bundle.root.resolve()
    unknown: Counter[str] = Counter()
    manifest = _mapping(_json_file(root, root / "manifest.json", unknown)).get("manifest")
    agents: dict[str, dict[str, Any]] = {}
    for spec in _mapping(manifest).get("agents") or []:
        agent_id = _identifier(_mapping(spec).get("id"))
        if agent_id:
            agents[agent_id] = _agent(agent_id)
    for event in _json_lines(root, root / "events.jsonl", unknown):
        agent_id = _identifier(event.get("agent_id"))
        if not agent_id:
            continue
        agent = agents.setdefault(agent_id, _agent(agent_id))
        kind, data = event.get("kind"), _mapping(event.get("data"))
        if not isinstance(kind, str):
            unknown["malformed_event_kinds"] += 1
            continue
        if kind == "gateway.chat_completion":
            agent["model"]["normalized_completion_records"] += 1
        if kind.startswith("gateway.") and kind.removeprefix("gateway.") in GATEWAY_EVENTS:
            agent["normalized_gateway_events"][kind.removeprefix("gateway.")] += 1
        if kind in {"monitor.error", "monitor.skipped"}:
            agent["monitor"][kind] += 1
        if isinstance(kind, str) and kind in STOP_EVENTS:
            agent["stop_events"][kind] += 1
        if kind == "monitor.intervention":
            agent["monitor"][_label(data.get("outcome"), {"allowed", "blocked", "unknown"})] += 1
            if data.get("decision") == "deny":
                agent["monitor"]["deny_decisions"] += 1
        if kind in {"harness.session", "verifier.reward", "verifier.failed"}:
            task = _identifier(data.get("task"))
            if not task:
                unknown["unattributed_task_records"] += 1
                continue
            outcome = agent["verifier"].setdefault(task, {"task": task, "status": "unmeasured", "original_score": None})
            if kind == "verifier.reward":
                score = data.get("reward")
                valid = isinstance(score, int | float) and not isinstance(score, bool) and math.isfinite(score)
                outcome.update(status="measured" if valid else "unknown", original_score=score if valid else None)
            elif kind == "verifier.failed":
                outcome.update(status="failed", original_score=None)
    for record in _json_lines(root, root / "native/gateway/records.jsonl", unknown):
        agent_id = _identifier(record.get("agent"))
        if not agent_id or agent_id not in agents:
            unknown["non_agent_or_unattributed_gateway_records"] += 1
            continue
        agent = agents[agent_id]
        if record.get("event") == "chat_completion":
            _completion(agent, record)
        elif isinstance(record.get("event"), str) and record["event"] in GATEWAY_EVENTS:
            agent["gateway_events"][record["event"]] += 1
    for agent_id, agent in agents.items():
        seen: dict[tuple[str, str], dict[str, Any] | None] = {}
        for path in sorted((root / "native" / agent_id).glob("*/messages.json")):
            messages = _json_file(root, path, unknown)
            if messages is not None:
                _messages(agent, messages, seen)
        tools_seen: set[tuple[str, str]] = set()
        for message in seen.values():
            if message is None:
                continue
            info = _mapping(message.get("info"))
            agent["harness"]["assistant_messages"] += 1
            agent["harness_finish_reasons"][_label(info.get("finish"), FINISH_REASONS)] += 1
            for part in message["parts"]:
                if not isinstance(part, dict):
                    agent["harness"]["malformed_parts"] += 1
                    continue
                if part.get("type") != "tool":
                    continue
                call_id = _identifier(part.get("callID") or part.get("id"))
                tool_key = (str(info.get("sessionID")), call_id or "")
                if not call_id or tool_key in tools_seen:
                    agent["harness"]["missing_or_duplicate_tool_identity"] += 1
                    continue
                tools_seen.add(tool_key)
                state = _label(_mapping(part.get("state")).get("status"), {"pending", "running", "completed", "error"})
                agent["harness_tool_states"][state] += 1
        model, harness = agent["model"], agent["harness"]
        # Null signals absent evidence; zero is only an observed count within parsed records.
        model["reported_tool_calls"] = model["reported_tool_calls"] if model["parsed_response_records"] else None
        harness["completed_tools"] = (agent["harness_tool_states"]["completed"]
                                      if any(value is not None for value in seen.values()) else None)
        if not harness["snapshots_read"]:
            agent["next_checks"].append("collect_harness_messages")
        if not model["completion_records"]:
            agent["next_checks"].append("collect_native_gateway_records")
        elif not model["incomplete_or_unknown_response_records"] and model["reported_tool_calls"] == 0:
            agent["findings"].append("retained_model_completions_contain_no_tool_calls")
            agent["next_checks"].append("compare_model_harness_and_sampling_on_the_same_original_task")
        if model["requests_without_tools"]:
            agent["next_checks"].append("inspect_harness_tool_configuration")
        if model["reported_tool_calls"] and harness["completed_tools"] == 0:
            agent["findings"].append("model_tool_calls_without_recorded_completed_harness_tools")
            agent["next_checks"].append("inspect_tool_admission_and_harness_states")
        if any(outcome["original_score"] == 0 for outcome in agent["verifier"].values()):
            agent["next_checks"].append("calibrate_original_verifier_with_unchanged_reference_solution")
        agent["verifier"] = list(agent["verifier"].values())
    return {"schema_version": "mesocosm.diagnostics.v1", "run_id": bundle.run_id,
            "agents": list(agents.values()), "unknown_evidence": dict(unknown),
            "coverage": "retained records only; missing events cannot establish that an action did not occur",
            "tool_execution_semantics": "completed is the harness-reported terminal state; "
                                        "error can include pre-tool denial",
            "causal_root_cause": None, "task_success_inferred_from_prose": False}
