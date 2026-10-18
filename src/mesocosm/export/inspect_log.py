"""A run bundle as an Inspect eval log (ADR-0025): one sample per agent and task, the agent's final model
request as the sample's messages, the verifier's reward as its score, the gateway's token usage as model
usage, the run's conditions, receipts and flags as metadata.

Written by hand against the schema of `inspect_ai.log.EvalLog` (version 2, the JSON form that `inspect
view` and `read_eval_log` open) so the export has no dependency on `inspect_ai`; the fields used are the
ones that library marks required plus the ones the viewer shows. The bundle remains the record of
truth: an export carries what Inspect's model can hold, and names in `metadata` what it cannot.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
from typing import Any

from mesocosm.core.records import RunBundle

STATUS = {"ok": "success", "stopped": "cancelled"}        # anything else is "error"
NOT_EXPORTED = ["token ids and activations (native/gateway/records.jsonl, activations/)",
                "services and web logs (native/services, native/web)", "receipts beyond their summary",
                "harness event stream (opencode.* events)", "raw tool logs (workspace raw_log.jsonl)"]


def _iso(ts: float | None) -> str:
    return dt.datetime.fromtimestamp(ts or 0, dt.UTC).isoformat().replace("+00:00", "Z")


def _tool_call(tc: dict[str, Any]) -> dict[str, Any]:
    fn = tc.get("function") or {}
    raw = fn.get("arguments")
    try:
        args = json.loads(raw) if isinstance(raw, str) else (raw or {})
        if not isinstance(args, dict):
            args = {"value": args}
    except json.JSONDecodeError:
        args = {"raw": raw}
    return {"id": str(tc.get("id") or ""), "function": str(fn.get("name") or ""), "arguments": args, "type": "function"}


def _message(m: dict[str, Any]) -> dict[str, Any]:
    """An OpenAI chat message as an Inspect ChatMessage."""
    role = m.get("role") or "user"
    content = m.get("content")
    if isinstance(content, list):                                 # content parts -> text
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    out: dict[str, Any] = {"role": role, "content": content or ""}
    if role == "assistant" and m.get("tool_calls"):
        out["tool_calls"] = [_tool_call(tc) for tc in m["tool_calls"]]
    if role == "tool":
        out["tool_call_id"] = m.get("tool_call_id")
        out["function"] = m.get("name")
    return out


def _usage(u: dict[str, Any] | None) -> dict[str, int]:
    u = u or {}
    prompt, completion = int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)
    return {"input_tokens": prompt, "output_tokens": completion,
            "total_tokens": int(u.get("total_tokens") or prompt + completion)}


def _add_usage(total: dict[str, int], u: dict[str, int]) -> None:
    for k, v in u.items():
        total[k] = total.get(k, 0) + v


def gateway_records(bundle: RunBundle) -> list[dict[str, Any]]:
    path = bundle.root / "native" / "gateway" / "records.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def inspect_log(bundle: RunBundle) -> dict[str, Any]:
    """The bundle as an Inspect `EvalLog` (version 2) dict."""
    manifest = bundle.manifest()
    spec = manifest.get("manifest") or manifest
    summary = json.loads((bundle.root / "summary.json").read_text(encoding="utf-8")) \
        if (bundle.root / "summary.json").exists() else {}
    events = bundle.events()
    records = [r for r in gateway_records(bundle) if r.get("event") == "chat_completion"]
    provider = (spec.get("gateway") or {}).get("provider_id", "internal")
    model = f"{provider}/{(spec.get('model') or {}).get('id', 'model')}"
    flags = bundle.flags()
    conditions = [e.data for e in events if e.kind == "conditions.set"]

    # one sample per (agent, task): its window is harness.session -> verifier.reward
    windows: list[dict[str, Any]] = []
    for e in events:
        if e.kind == "harness.session":
            windows.append({"agent": e.agent_id, "task": e.data.get("task"), "start": e.ts, "end": None,
                            "reward": None})
        elif e.kind == "verifier.reward":
            w = next((w for w in reversed(windows) if w["agent"] == e.agent_id and w["end"] is None), None)
            if w is not None:
                w["end"], w["reward"], w["source"] = e.ts, e.data.get("reward"), e.data.get("source")
    samples = []
    run_usage: dict[str, int] = {}
    for w in windows:
        mine = [r for r in records if r.get("agent") == w["agent"] and w["start"] - 1 <= float(r.get("ts") or 0)
                and (w["end"] is None or float(r.get("ts") or 0) <= w["end"] + 1)]
        usage: dict[str, int] = {}
        for r in mine:
            _add_usage(usage, _usage((r.get("response") or {}).get("usage")))
        _add_usage(run_usage, usage)
        last = mine[-1] if mine else None
        messages = [_message(m) for m in ((last or {}).get("request") or {}).get("messages") or []]
        final: dict[str, Any] = {"role": "assistant", "content": ""}
        if last is not None:
            resp = last.get("response") or {}
            final = {"role": "assistant", "content": resp.get("content") or "", "model": resp.get("model")}
            if resp.get("tool_calls"):
                final["tool_calls"] = [_tool_call(tc) for tc in resp["tool_calls"]]
            messages.append(final)
        instruction = next((m["content"] for m in messages if m["role"] == "user"), "")
        agent_flags = [f for f in flags if f.get("agent_id") == w["agent"]]
        reward = w["reward"]
        samples.append({
            "id": f"{w['agent']}/{w['task']}", "epoch": 1, "input": instruction, "target": "",
            "messages": messages,
            "output": {"model": model, "choices": [{"message": final, "stop_reason": "stop"}],
                       "usage": usage or None},
            "scores": {"verifier": {"value": reward if reward is not None else "", "answer": None,
                                    "explanation": w.get("source"), "metadata": {"task": w["task"]}}},
            "metadata": {"agent_id": w["agent"], "task": w["task"], "run_id": bundle.run_id,
                         "model_calls": len(mine), "flags": [{"kind": f["kind"], "summary": f["summary"]}
                                                             for f in agent_flags]},
            "model_usage": {model: usage} if usage else {},
            "started_at": _iso(w["start"]), "completed_at": _iso(w["end"] or w["start"]),
            "total_time": round((w["end"] or w["start"]) - w["start"], 3),
        })

    rewards = [s["scores"]["verifier"]["value"] for s in samples
               if isinstance(s["scores"]["verifier"]["value"], (int, float))]
    accuracy = sum(rewards) / len(rewards) if rewards else 0.0
    started = events[0].ts if events else None
    completed = events[-1].ts if events else None
    return {
        "version": 2,
        "status": STATUS.get(str(summary.get("status")), "error"),
        "eval": {
            "run_id": bundle.run_id, "eval_id": bundle.run_id, "created": _iso(started),
            "task": spec.get("name", "world"), "task_id": str(manifest.get("digest", ""))[:16], "task_version": 0,
            "task_display_name": spec.get("name", "world"),
            "dataset": {"name": spec.get("name", "world"), "samples": len(samples),
                        "sample_ids": [s["id"] for s in samples], "shuffled": False},
            "model": model, "config": {}, "tags": ["mesocosm"],
            "metadata": {"mesocosm": {"bundle": str(bundle.root), "run_label": summary.get("run_label"),
                                      "mode": spec.get("mode"), "conditions": conditions,
                                      "receipts": [{"op": r.get("op"), "requested": r.get("requested"),
                                                    "result": r.get("result")} for r in bundle.receipts()],
                                      "flags": summary.get("flags"), "not_exported": NOT_EXPORTED}},
        },
        "plan": {"name": "mesocosm", "steps": [{"solver": "opencode", "params": {}}]},
        "results": {"total_samples": len(samples), "completed_samples": len(samples),
                    "scores": [{"name": "verifier", "scorer": "harbor",
                                "metrics": {"accuracy": {"name": "accuracy", "value": accuracy}}}]},
        "stats": {"started_at": _iso(started), "completed_at": _iso(completed),
                  "model_usage": {model: run_usage} if run_usage else {}},
        "samples": samples,
    }


def write_inspect_log(bundle_root: str | pathlib.Path, out: str | pathlib.Path | None = None) -> pathlib.Path:
    bundle = RunBundle.open(bundle_root)
    target = pathlib.Path(out) if out else bundle.root / "exports" / f"{bundle.run_id}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(inspect_log(bundle), ensure_ascii=False, indent=1), encoding="utf-8")
    return target
