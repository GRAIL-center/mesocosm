"""Structural diagnosis fixtures are not real-agent acceptance evidence."""

from __future__ import annotations

import json

from mesocosm.core.diagnostics import diagnose_bundle
from mesocosm.core.records import RunBundle


def bundle_with_agents(tmp_path, n=3):
    bundle = RunBundle.create(tmp_path, "run_diagnostics")
    bundle.write_manifest({"agents": [{"id": f"agent-{i}"} for i in range(1, n + 1)]}, "test")
    for i in range(1, n + 1):
        bundle.record("harness.session", agent_id=f"agent-{i}", task="original-task", session_id=f"session-{i}")
    return bundle


def completion(agent, *, tools=(), finish="stop", **kwargs):
    return {"agent": agent, "event": "chat_completion", "status": 200, "stream": True,
            "request": {"tools": [{"type": "function"}], "api_key": "SECRET", "messages": [{"content": "PROMPT"}]},
            "response": {"tool_calls": list(tools), "finish_reason": finish, "stream_complete": True,
                         "content": "I completed the task. SECRET", "reasoning": "PRIVATE"}, **kwargs}


def message(*, agent="agent-1", state=None):
    return {"info": {"id": "message-1", "sessionID": f"session-{agent}", "role": "assistant", "finish": "stop"},
            "parts": [{"type": "text", "text": "I completed the task. SECRET"}] +
            ([{"type": "tool", "id": "part-1", "callID": "call-1", "tool": "bash",
               "state": {"status": state, "input": {"command": "SECRET"}, "output": "PRIVATE"}}] if state else [])}


def write_gateway(bundle, records):
    bundle.write_native("gateway/records.jsonl", "".join(json.dumps(record) + "\n" for record in records))


def test_three_agents_distinguishes_no_tool_output_execution_and_actual_zero_scores(tmp_path):
    bundle = bundle_with_agents(tmp_path)
    records = []
    for index in range(1, 4):
        agent = f"agent-{index}"
        tools = [{"id": "call-1", "function": {"name": "bash", "arguments": "SECRET"}}] if index == 2 else []
        records.append(completion(agent, tools=tools, finish="tool_calls" if tools else "stop"))
        bundle.write_native(f"{agent}/original-task/messages.json",
                            json.dumps([message(agent=agent, state="completed" if tools else None)]))
        bundle.record("verifier.reward", agent_id=agent, task="original-task", reward=0)
        bundle.record("gateway.chat_completion", agent_id=agent)
    write_gateway(bundle, records)
    report = diagnose_bundle(bundle.root)
    first, second, third = report["agents"]
    assert [agent["model"]["reported_tool_calls"] for agent in report["agents"]] == [0, 1, 0]
    assert [agent["harness"]["completed_tools"] for agent in report["agents"]] == [0, 1, 0]
    assert first["findings"] == third["findings"] == ["retained_model_completions_contain_no_tool_calls"]
    assert second["findings"] == []
    assert all(agent["verifier"][0] == {"task": "original-task", "status": "measured", "original_score": 0}
               for agent in report["agents"])
    assert report["causal_root_cause"] is None
    assert report["task_success_inferred_from_prose"] is False
    assert not any(value in json.dumps(report) for value in ("SECRET", "PRIVATE", "PROMPT", "I completed"))


def test_missing_native_records_are_unknown_not_zero_and_normalized_calls_not_double_counted(tmp_path):
    bundle = bundle_with_agents(tmp_path, n=5)
    bundle.record("gateway.chat_completion", agent_id="agent-1")
    bundle.record("gateway.budget_exhausted", agent_id="agent-1")
    report = diagnose_bundle(bundle.root)
    assert len(report["agents"]) == 5
    first = report["agents"][0]
    assert first["model"]["normalized_completion_records"] == 1
    assert first["normalized_gateway_events"] == {"budget_exhausted": 1}
    assert first["model"]["reported_tool_calls"] is None
    assert first["harness"]["completed_tools"] is None
    assert first["verifier"][0]["status"] == "unmeasured"
    assert report["unknown_evidence"]["missing_or_unsafe_files"] == 1


def test_stream_failure_gateway_refusals_and_denied_harness_tools_remain_separate(tmp_path):
    bundle = bundle_with_agents(tmp_path, n=1)
    tools = [{"id": "call-1", "function": {"name": "bash"}}]
    write_gateway(bundle, [completion("agent-1", tools=tools, response={"tool_calls": tools, "finish_reason": "tool_calls",
                                                                     "stream_complete": False}),
                           {"event": "resource_denied", "agent": "agent-1", "reason": "SECRET"},
                           {"event": "upstream_error", "agent": "agent-1", "error": "SECRET"}])
    bundle.write_native("agent-1/original-task/messages.json", json.dumps([message(state="error")]))
    bundle.record("monitor.intervention", agent_id="agent-1", outcome="blocked", decision="deny", reason="SECRET")
    bundle.record("verifier.failed", agent_id="agent-1", task="original-task", error="SECRET")
    report = diagnose_bundle(bundle.root)
    agent = report["agents"][0]
    assert agent["model"]["incomplete_or_unknown_response_records"] == 1
    assert agent["gateway_events"] == {"resource_denied": 1, "upstream_error": 1}
    assert agent["monitor"] == {"blocked": 1, "deny_decisions": 1}
    assert agent["harness_tool_states"] == {"error": 1}
    assert agent["harness"]["completed_tools"] == 0
    assert agent["verifier"][0]["original_score"] is None
    assert "SECRET" not in json.dumps(report)


def test_carried_transcripts_are_deduplicated_and_malformed_evidence_is_counted(tmp_path):
    bundle = bundle_with_agents(tmp_path, n=1)
    for task in ("one", "two"):
        bundle.write_native(f"agent-1/{task}/messages.json", json.dumps([message(state="completed")]))
    bundle.write_native("agent-1/three/messages.json", json.dumps([None, {"parts": []}]))
    bundle.write_native("gateway/records.jsonl", "not json\n[]\n" + json.dumps(
        completion("agent-1", response={"tool_calls": None, "finish_reason": "stop"})) + "\n")
    agent = diagnose_bundle(bundle.root)["agents"][0]
    assert agent["harness"]["assistant_messages"] == 1
    assert agent["harness"]["completed_tools"] == 1
    assert agent["harness"]["malformed_messages"] == 2
    assert agent["model"]["reported_tool_calls"] is None
    assert agent["findings"] == []


def test_diagnostic_file_reference_cannot_escape_bundle(tmp_path):
    bundle = bundle_with_agents(tmp_path, n=1)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "messages.json").write_text(json.dumps([message(state="completed")]))
    (bundle.root / "native/agent-1").mkdir()
    (bundle.root / "native/agent-1/task").symlink_to(outside, target_is_directory=True)
    write_gateway(bundle, [])
    report = diagnose_bundle(bundle.root)
    assert report["agents"][0]["harness"]["completed_tools"] is None
    assert report["unknown_evidence"]["missing_or_unsafe_files"] == 1


def test_conflicting_snapshots_and_unknown_finish_values_do_not_fabricate_execution(tmp_path):
    bundle = bundle_with_agents(tmp_path, n=1)
    bundle.write_native("agent-1/one/messages.json", json.dumps([message(state="pending")]))
    bundle.write_native("agent-1/two/messages.json", json.dumps([message(state="completed")]))
    write_gateway(bundle, [completion("agent-1", finish="SECRET finish reason")])
    report = diagnose_bundle(bundle.root)
    agent = report["agents"][0]
    assert agent["harness"]["conflicting_message_snapshots"] == 1
    assert agent["harness"]["completed_tools"] is None
    assert agent["model_finish_reasons"] == {"unknown": 1}
    assert "SECRET" not in json.dumps(report)


def test_truncated_oversized_records_are_unknown_and_reader_continues(tmp_path, monkeypatch):
    import mesocosm.core.diagnostics as diagnostics

    bundle = bundle_with_agents(tmp_path, n=1)
    monkeypatch.setattr(diagnostics, "MAX_RECORD_BYTES", 1024)
    bundle.write_native("gateway/records.jsonl", " " * 1025 + "\n" + json.dumps(completion("agent-1")) + "\n")
    report = diagnose_bundle(bundle.root)
    assert report["unknown_evidence"]["oversized_records"] == 1
    assert report["agents"][0]["model"]["completion_records"] == 1


def test_gateway_failure_response_is_not_a_completed_no_tool_model_turn(tmp_path):
    bundle = bundle_with_agents(tmp_path, n=1)
    write_gateway(bundle, [completion("agent-1", status=400, response={"raw": "SECRET"})])
    agent = diagnose_bundle(bundle.root)["agents"][0]
    assert agent["model"]["http_error_records"] == 1
    assert agent["model"]["reported_tool_calls"] is None
    assert agent["findings"] == []
