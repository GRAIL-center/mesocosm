"""A run bundle exported as an Inspect eval log (ADR-0025): one sample per agent and task, the final model
request as messages, the reward as the score, token usage, conditions and flags as metadata. The shape is
the one `inspect_ai.log.read_eval_log` accepts (validated against the library outside the test suite)."""

import json

from mesocosm.core.records import RunBundle
from mesocosm.export.inspect_log import NOT_EXPORTED, inspect_log, write_inspect_log


def _bundle(tmp_path):
    b = RunBundle.create(tmp_path / "runs", "run_x")
    b.write_manifest({"name": "demo", "mode": "comparison", "model": {"id": "qwen3-4b"},
                      "models": [{"id": "baseline-x", "kind": "openai"}],
                      "gateway": {"provider_id": "internal"},
                      "agents": [{"id": "agent-1"}, {"id": "agent-2", "model": "baseline-x"}]}, "d" * 64)
    b.record("conditions.set", condition="budget", result="applied", ts=10.0)
    b.record("harness.session", agent_id="agent-1", step=1, session_id="s1", task="hello", ts=11.0)
    b.record("verifier.reward", agent_id="agent-1", step=2, task="hello", reward=1.0, source="tests/test.sh", ts=20.0)
    b.record("harness.session", agent_id="agent-1", step=3, session_id="s2", task="second", ts=21.0)
    b.record("verifier.reward", agent_id="agent-1", step=4, task="second", reward=0.0, source="tests/test.sh", ts=30.0)
    b.record("harness.session", agent_id="agent-2", step=1, session_id="s3", task="hello", ts=11.0)
    b.record("verifier.reward", agent_id="agent-2", step=2, task="hello", reward=1.0, source="tests/test.sh", ts=20.0)
    b.record_flag({"flag_id": "f1", "run_id": "run_x", "agent_id": "agent-1", "kind": "blocked_egress",
                   "summary": "1 request to 'evil.example' refused", "detector": "web", "ts": 15.0, "evidence": []})
    b.record_receipt({"op": "conditions.set", "requested": {"condition": "budget"}, "result": "applied"})
    tool_call = {"id": "call-1", "type": "function", "function": {"name": "bash", "arguments": "{\"command\": \"ls\"}"}}
    records = [
        {"ts": 12.0, "agent": "agent-1", "event": "chat_completion", "request": {
            "messages": [{"role": "system", "content": "You are opencode"}, {"role": "user", "content": "say hello"}]},
         "response": {"content": "", "tool_calls": [tool_call], "model": "qwen3-4b",
                      "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110}}},
        {"ts": 13.0, "agent": "agent-1", "event": "chat_completion", "request": {
            "messages": [{"role": "system", "content": "You are opencode"}, {"role": "user", "content": "say hello"},
                         {"role": "assistant", "content": None, "tool_calls": [tool_call]},
                         {"role": "tool", "tool_call_id": "call-1", "name": "bash", "content": "a.txt"}]},
         "response": {"content": "done", "tool_calls": [], "model": "qwen3-4b",
                      "usage": {"prompt_tokens": 120, "completion_tokens": 5, "total_tokens": 125}}},
        {"ts": 25.0, "agent": "agent-1", "event": "chat_completion", "request": {
            "messages": [{"role": "user", "content": [{"type": "text", "text": "second task"}]}]},
         "response": {"content": "no", "tool_calls": [], "model": "qwen3-4b", "usage": {"prompt_tokens": 50,
                                                                                         "completion_tokens": 1}}},
        {"ts": 26.0, "agent": "agent-1", "event": "budget_exhausted"},
        {"ts": 14.0, "agent": "agent-2", "event": "chat_completion", "request": {
            "messages": [{"role": "user", "content": "say hello"}]},
         "response": {"content": "hello", "tool_calls": [], "model": "baseline-x",
                      "usage": {"prompt_tokens": 30, "completion_tokens": 2, "total_tokens": 32}}},
    ]
    b.write_native("gateway/records.jsonl", "".join(json.dumps(r) + "\n" for r in records))
    b.write_summary({"status": "ok", "run_label": "demo", "flags": {"blocked_egress": 1}})
    return b


def test_bundle_becomes_a_version_2_eval_log_with_one_sample_per_agent_task(tmp_path):
    log = inspect_log(_bundle(tmp_path))
    assert log["version"] == 2 and log["status"] == "success"
    assert log["eval"]["task"] == "demo" and log["eval"]["model"] == "internal/qwen3-4b"
    assert log["eval"]["dataset"]["sample_ids"] == ["agent-1/hello", "agent-1/second", "agent-2/hello"]
    assert log["eval"]["metadata"]["mesocosm"]["not_exported"] == NOT_EXPORTED
    assert log["eval"]["metadata"]["mesocosm"]["conditions"][0]["condition"] == "budget"
    assert log["eval"]["metadata"]["mesocosm"]["receipts"] == [{"op": "conditions.set", "requested": {"condition": "budget"},
                                                                 "result": "applied"}]
    first, second, other = log["samples"]
    assert other["output"]["model"] == "internal/baseline-x" and other["model_usage"] == {
        "internal/baseline-x": {"input_tokens": 30, "output_tokens": 2, "total_tokens": 32}}, "the agent's own model"
    assert first["input"] == "say hello" and first["scores"]["verifier"]["value"] == 1.0
    roles = [m["role"] for m in first["messages"]]
    assert roles == ["system", "user", "assistant", "tool", "assistant"], "the last request plus its answer"
    assert first["messages"][2]["tool_calls"] == [{"id": "call-1", "function": "bash", "arguments": {"command": "ls"},
                                                   "type": "function"}]
    assert first["messages"][3] == {"role": "tool", "content": "a.txt", "tool_call_id": "call-1", "function": "bash"}
    assert first["messages"][-1]["content"] == "done" and first["output"]["choices"][0]["message"]["content"] == "done"
    assert first["model_usage"] == {"internal/qwen3-4b": {"input_tokens": 220, "output_tokens": 15, "total_tokens": 235}}
    assert first["metadata"]["model_calls"] == 2 and first["metadata"]["flags"][0]["kind"] == "blocked_egress"
    assert first["started_at"].endswith("Z") and first["total_time"] == 9.0
    assert second["input"] == "second task" and second["scores"]["verifier"]["value"] == 0.0
    assert second["model_usage"]["internal/qwen3-4b"]["total_tokens"] == 51, "total is derived when the upstream omits it"
    assert log["results"]["scores"][0]["metrics"]["accuracy"]["value"] == 2 / 3
    assert log["stats"]["model_usage"]["internal/qwen3-4b"]["input_tokens"] == 270
    assert log["stats"]["model_usage"]["internal/baseline-x"]["input_tokens"] == 30


def test_status_follows_the_summary_and_the_file_lands_in_the_bundle(tmp_path):
    b = _bundle(tmp_path)
    path = write_inspect_log(b.root)
    assert path == b.root / "exports" / "run_x.json" and json.loads(path.read_text())["status"] == "success"
    stopped = RunBundle.create(tmp_path / "runs", "run_s")
    stopped.write_manifest({"name": "demo", "agents": []}, "d" * 64)
    stopped.write_summary({"status": "stopped"})
    assert inspect_log(stopped)["status"] == "cancelled" and inspect_log(stopped)["samples"] == []
    failed = RunBundle.create(tmp_path / "runs", "run_f")
    failed.write_manifest({"name": "demo", "agents": []}, "d" * 64)
    failed.write_summary({"status": "failed_isolation"})
    assert inspect_log(failed)["status"] == "error"
