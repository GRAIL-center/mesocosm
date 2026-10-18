"""Counters and cost per agent-hour from a bundle."""

import json

from mesocosm.core.records import RunBundle
from mesocosm.core.report import agent_seconds, bundle_report


def test_bundle_report_counts_time_tokens_flags_and_cost(tmp_path):
    b = RunBundle.create(tmp_path, "run_r")
    t = 1000.0
    b._append("events.jsonl", {"ts": t - 5, "run_id": "run_r", "kind": "gateway.health", "data": {"ok": True}})
    b._append("events.jsonl", {"ts": t, "run_id": "run_r", "kind": "harness.session", "agent_id": "agent-1",
                               "data": {"session_id": "s1", "task": "t"}})
    b._append("events.jsonl", {"ts": t + 30, "run_id": "run_r", "kind": "gateway.chat_completion", "agent_id": "agent-1",
                               "request_id": "r1", "data": {"usage": {"prompt_tokens": 1000, "completion_tokens": 50}}})
    b._append("events.jsonl", {"ts": t + 120, "run_id": "run_r", "kind": "verifier.reward", "agent_id": "agent-1",
                               "data": {"task": "t", "reward": 1.0}})
    b._append("events.jsonl", {"ts": t + 60, "run_id": "run_r", "kind": "harness.session", "agent_id": "agent-2",
                               "data": {"session_id": "s2", "task": "t"}})
    b._append("events.jsonl", {"ts": t + 240, "run_id": "run_r", "kind": "harness.reply", "agent_id": "agent-2",
                               "data": {"session_id": "s2"}})
    b.write_summary({"status": "ok", "rewards": {"agent-1/t": 1.0}})
    b.record_flag({"flag_id": "flag_1", "kind": "trap_credential", "agent_id": "agent-1"})
    b.record_flag({"flag_id": "flag_2", "kind": "trap_credential", "agent_id": "agent-2"})
    b.record_verdict({"flag_id": "flag_1", "verdict": "unclear"})
    b.record_verdict({"flag_id": "flag_1", "verdict": "confirmed"})       # the latest verdict counts
    assert agent_seconds(b.events()) == {"agent-1": 120.0, "agent-2": 180.0}
    r = bundle_report(b.root, gpu_hour_cost=3.0)
    assert r["status"] == "ok" and r["model_calls"] == 1 and r["prompt_tokens"] == 1000 and r["completion_tokens"] == 50
    assert r["agents"]["agent-1"] == {"model_calls": 1, "prompt_tokens": 1000, "completion_tokens": 50,
                                      "rewards": {"t": 1.0}, "seconds": 120.0}
    assert r["agent_hours"] == round(300 / 3600, 6) and r["wallclock_seconds"] > 240
    assert r["flags"] == {"trap_credential": 2} and r["verdicts"] == {"confirmed": 1} and r["unreviewed_flags"] == 1
    assert r["cost"]["gpu_hour_cost"] == 3.0 and r["cost"]["run_cost"] == round(3.0 * r["wallclock_seconds"] / 3600, 4)
    assert r["cost"]["per_agent_hour"] == round(r["cost"]["run_cost"] / r["agent_hours"], 4)
    assert json.dumps(r)
