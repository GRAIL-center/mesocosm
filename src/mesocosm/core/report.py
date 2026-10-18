"""Counters from a run bundle: rewards, model calls, tokens, agent time, flags, and cost per
agent-hour when a GPU-hour price is given (M4 exit criterion)."""

from __future__ import annotations

import json
import pathlib
from collections import Counter
from typing import Any

from mesocosm.core.records import Event, RunBundle

ACTIVE_KINDS_PREFIX = ("harness.", "opencode.", "verifier.", "gateway.chat_completion")


def agent_seconds(events: list[Event]) -> dict[str, float]:
    """Per agent: from its first session to its last recorded activity."""
    first: dict[str, float] = {}
    last: dict[str, float] = {}
    for ev in events:
        if not ev.agent_id or not ev.kind.startswith(ACTIVE_KINDS_PREFIX):
            continue
        if ev.kind == "harness.session":
            first.setdefault(ev.agent_id, ev.ts)
        last[ev.agent_id] = max(last.get(ev.agent_id, ev.ts), ev.ts)
    return {a: round(max(0.0, last[a] - first[a]), 3) for a in first}


def bundle_report(root: str | pathlib.Path, gpu_hour_cost: float | None = None) -> dict[str, Any]:
    bundle = RunBundle.open(root)
    events = bundle.events()
    summary_path = bundle.root / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    agents: dict[str, dict[str, Any]] = {}
    for ev in events:
        if ev.agent_id and ev.kind in ("harness.session", "gateway.chat_completion", "verifier.reward"):
            agents.setdefault(ev.agent_id,
                              {"model_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "rewards": {}})
        if ev.kind == "gateway.chat_completion" and ev.agent_id:
            usage = ev.data.get("usage") or {}
            a = agents[ev.agent_id]
            a["model_calls"] += 1
            a["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
            a["completion_tokens"] += int(usage.get("completion_tokens") or 0)
        elif ev.kind == "verifier.reward" and ev.agent_id:
            agents[ev.agent_id]["rewards"][ev.data.get("task")] = ev.data.get("reward")
    seconds = agent_seconds(events)
    for agent_id, a in agents.items():
        a["seconds"] = seconds.get(agent_id, 0.0)
    wallclock = round(max(e.ts for e in events) - min(e.ts for e in events), 3) if events else 0.0
    agent_hours = round(sum(seconds.values()) / 3600, 6)
    flags = bundle.flags()
    latest_verdict: dict[str, str] = {}
    for v in bundle.verdicts():
        latest_verdict[v["flag_id"]] = v["verdict"]
    report: dict[str, Any] = {
        "run_id": bundle.run_id, "status": summary.get("status", "running"), "rewards": summary.get("rewards", {}),
        "wallclock_seconds": wallclock, "agent_hours": agent_hours, "agents": agents,
        "model_calls": sum(a["model_calls"] for a in agents.values()),
        "prompt_tokens": sum(a["prompt_tokens"] for a in agents.values()),
        "completion_tokens": sum(a["completion_tokens"] for a in agents.values()),
        "flags": dict(sorted(Counter(f["kind"] for f in flags).items())),
        "verdicts": dict(sorted(Counter(latest_verdict.values()).items())),
        "unreviewed_flags": sum(1 for f in flags if f["flag_id"] not in latest_verdict),
    }
    from mesocosm.judge.flags import peer_review_metrics

    metrics = peer_review_metrics(events)
    if metrics:
        report["peer_review"] = metrics
    if gpu_hour_cost is not None:
        cost = round(gpu_hour_cost * wallclock / 3600, 4)
        report["cost"] = {"gpu_hour_cost": gpu_hour_cost, "run_cost": cost,
                          "per_agent_hour": round(cost / agent_hours, 4) if agent_hours else None}
    return report
