from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

import pytest
from pydantic import ValidationError

from mesocosm.core.history import EpisodeHistory, HistoryCapacityError, HistoryRecordError, HistorySpec


def messages(session: str, *, prompt: str = "Original task", output: str = "Actual result") -> list[dict[str, Any]]:
    return [
        {"info": {"id": f"{session}-u", "sessionID": session, "role": "user"},
         "parts": [{"id": f"{session}-up", "type": "text", "text": prompt}]},
        {"info": {"id": f"{session}-a", "sessionID": session, "role": "assistant",
                  "parentID": f"{session}-u", "finish": "stop", "time": {"completed": 2}},
         "parts": [
             {"id": f"{session}-r", "type": "reasoning", "text": "HIDDEN_REASONING"},
             {"id": f"{session}-t", "type": "text", "text": "Visible report"},
             {"id": f"{session}-p", "type": "tool", "tool": "bash", "callID": f"{session}-call",
              "state": {"status": "completed", "input": {"command": "pytest -q"}, "output": output,
                        "metadata": {"private": "UNSELECTED_METADATA"}, "time": {"start": 1, "end": 2}}},
         ]},
    ]


def payload(history: EpisodeHistory, agent: str = "agent-1") -> dict[str, Any]:
    context = history.context(agent)
    assert context is not None
    return json.loads(context.text.split("\n", 1)[1])


def test_history_default_disabled_and_schema_rejects_unknown_or_invalid_conditions():
    history = EpisodeHistory(HistorySpec())
    assert history.record("agent-1", "task", "session", messages("session")) is None
    assert history.context("agent-1") is None
    for invalid in ({"episodes": -1}, {"max_chars": 99}, {"scope": "summary"}, {"summarize": True}):
        with pytest.raises(ValidationError):
            HistorySpec.model_validate(invalid)


def test_actions_are_exact_tool_inputs_and_results_without_text_or_metadata():
    history = EpisodeHistory(HistorySpec(episodes=1))
    source = messages("s1")
    evidence = history.record("agent-1", "original-task", "s1", source, task_sha256="a" * 64)
    selected = payload(history)
    assert selected["episodes"] == [{"task": "original-task", "records": [
        {"message_id": "s1-a", "part_id": "s1-p", "type": "tool", "tool": "bash", "call_id": "s1-call",
         "status": "completed", "input": {"command": "pytest -q"}, "output": "Actual result"},
    ]}]
    context = history.context("agent-1")
    assert context is not None and evidence is not None
    assert "Visible report" not in context.text
    assert "HIDDEN_REASONING" not in context.text
    assert "UNSELECTED_METADATA" not in context.text
    assert evidence["task_sha256"] == "a" * 64
    native = json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    assert evidence["native_messages_sha256"] == hashlib.sha256(native.encode()).hexdigest()
    assert evidence["message_ids"] == ["s1-a"]
    assert evidence["part_ids"] == ["s1-p"]
    assert context.evidence["payload_sha256"] == hashlib.sha256(context.text.encode()).hexdigest()


def test_transcript_excludes_prompt_replay_hidden_reasoning_and_synthetic_text():
    history = EpisodeHistory(HistorySpec(episodes=2, scope="transcript"))
    source = messages("s1", prompt="FIRST_PROMPT_NOT_A_RESPONSE")
    source[1]["parts"].extend([
        {"id": "synthetic", "type": "text", "text": "SYNTHETIC_TEXT", "synthetic": True},
        {"id": "ignored", "type": "text", "text": "IGNORED_TEXT", "ignored": True},
    ])
    history.record("agent-1", "first", "s1", source)
    first = history.context("agent-1")
    assert first is not None
    history.record("agent-1", "second", "s2", messages("s2", prompt=first.text))
    second = history.context("agent-1")
    assert second is not None
    assert second.text.count("Previous completed task records:") == 1
    assert second.text.count("Visible report") == 2
    for excluded in ("FIRST_PROMPT_NOT_A_RESPONSE", "SYNTHETIC_TEXT", "IGNORED_TEXT", "HIDDEN_REASONING"):
        assert excluded not in second.text
    assert payload(history)["episodes"][0]["records"][0]["type"] == "text"


def test_last_k_episodes_remain_in_order_and_agent_histories_stay_separate():
    history = EpisodeHistory(HistorySpec(episodes=2))
    for index in range(3):
        history.record("agent-1", f"task-{index}", f"s{index}", messages(f"s{index}"))
    history.record("agent-2", "private-task", "private", messages("private", output="PRIVATE"))
    assert [episode["task"] for episode in payload(history)["episodes"]] == ["task-1", "task-2"]
    assert [episode["task"] for episode in payload(history, "agent-2")["episodes"]] == ["private-task"]
    assert history.context("agent-3") is None
    assert "PRIVATE" not in history.context("agent-1").text


def test_recorded_source_and_returned_evidence_cannot_mutate_stored_history():
    history = EpisodeHistory(HistorySpec(episodes=1))
    source = messages("s1")
    original = copy.deepcopy(source)
    receipt = history.record("agent-1", "task", "s1", source)
    initial = history.context("agent-1")
    assert initial is not None and receipt is not None
    assert source == original
    source[1]["parts"][2]["state"]["input"]["command"] = "MUTATED"
    receipt["message_ids"].append("MUTATED")
    initial.evidence["episodes"][0]["part_ids"].append("MUTATED")
    assert "MUTATED" not in history.context("agent-1").text
    assert history.context("agent-1").evidence["episodes"][0]["part_ids"] == ["s1-p"]


def test_failed_tool_and_attachment_results_are_preserved():
    history = EpisodeHistory(HistorySpec(episodes=1))
    source = messages("s1")
    state = source[1]["parts"][2]["state"]
    state.update(status="error", error="command failed", attachments=[{"type": "file", "url": "file:///tmp/a"}])
    del state["output"]
    history.record("agent-1", "task", "s1", source)
    record = payload(history)["episodes"][0]["records"][0]
    assert record["error"] == "command failed"
    assert record["attachments"] == state["attachments"]
    assert "output" not in record


def test_capacity_limit_rejects_entire_delivery_without_truncating_or_losing_episodes():
    unlimited = EpisodeHistory(HistorySpec(episodes=2))
    unlimited.record("agent-1", "task", "s1", messages("s1", output="결과"))
    expected = unlimited.context("agent-1")
    assert expected is not None
    exact = EpisodeHistory(HistorySpec(episodes=2, max_chars=len(expected.text)))
    exact.record("agent-1", "task", "s1", messages("s1", output="결과"))
    assert exact.context("agent-1").text == expected.text
    bounded = EpisodeHistory(HistorySpec(episodes=2, max_chars=len(expected.text) - 1))
    bounded.record("agent-1", "task", "s1", messages("s1", output="결과"))
    for _ in range(2):
        with pytest.raises(HistoryCapacityError, match="not truncated or delivered") as error:
            bounded.context("agent-1")
        assert error.value.chars == len(expected.text)
        assert error.value.max_chars == len(expected.text) - 1


@pytest.mark.parametrize("malformation", [
    "foreign_session", "duplicate_message", "duplicate_part", "missing_id", "bad_parts", "unknown_role",
    "pending_tool", "missing_input", "missing_output", "missing_call_id", "pruned_tool", "compaction", "nan",
])
def test_malformed_or_incomplete_history_is_rejected_without_overwriting_previous_episode(malformation: str):
    history = EpisodeHistory(HistorySpec(episodes=1, scope="transcript"))
    history.record("agent-1", "good", "s1", messages("s1"))
    source = messages("s2")
    assistant = source[1]
    tool = assistant["parts"][2]
    if malformation == "foreign_session":
        assistant["info"]["sessionID"] = "other"
    elif malformation == "duplicate_message":
        source.append(copy.deepcopy(assistant))
    elif malformation == "duplicate_part":
        assistant["parts"].append(copy.deepcopy(tool))
    elif malformation == "missing_id":
        del assistant["info"]["id"]
    elif malformation == "bad_parts":
        assistant["parts"] = "not a list"
    elif malformation == "unknown_role":
        assistant["info"]["role"] = "unknown"
    elif malformation == "pending_tool":
        tool["state"]["status"] = "running"
    elif malformation == "missing_input":
        del tool["state"]["input"]
    elif malformation == "missing_output":
        del tool["state"]["output"]
    elif malformation == "missing_call_id":
        del tool["callID"]
    elif malformation == "pruned_tool":
        tool["state"]["time"]["compacted"] = 3
    elif malformation == "compaction":
        assistant["info"]["summary"] = True
    else:
        tool["state"]["input"]["not_finite"] = float("nan")
    with pytest.raises(HistoryRecordError):
        history.record("agent-1", "bad", "s2", source)
    assert [episode["task"] for episode in payload(history)["episodes"]] == ["good"]


def test_reusing_a_retained_session_is_not_a_new_episode():
    history = EpisodeHistory(HistorySpec(episodes=1))
    history.record("agent-1", "first", "s1", messages("s1"))
    history.record("agent-1", "second", "s2", messages("s2"))
    with pytest.raises(HistoryRecordError, match="fresh session"):
        history.record("agent-1", "third", "s1", messages("s1"))


def test_missing_native_history_cannot_become_an_empty_completed_episode():
    history = EpisodeHistory(HistorySpec(episodes=1))
    with pytest.raises(HistoryRecordError, match="nonempty"):
        history.record("agent-1", "task", "s1", [])


def test_actionless_completed_episode_is_preserved_as_empty_records():
    history = EpisodeHistory(HistorySpec(episodes=1))
    source = messages("s1")
    source[1]["parts"] = source[1]["parts"][:2]
    history.record("agent-1", "no-tools", "s1", source)
    assert payload(history)["episodes"] == [{"task": "no-tools", "records": []}]
