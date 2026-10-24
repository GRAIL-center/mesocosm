from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from mesocosm.adapters.opencode.client import OpenCodeClient
from mesocosm.adapters.opencode.trajectory import TurnCollectionError, collect_turn, send_turn


def compacted_messages() -> list[dict[str, Any]]:
    fixture = json.loads((Path(__file__).parent / "fixtures" / "opencode_compacted_turn.json").read_text())
    return fixture["messages"]


def user(message_id: str, text: str = "Task") -> dict[str, Any]:
    return {"info": {"id": message_id, "sessionID": "session", "role": "user", "time": {"created": 1}},
            "parts": [{"id": f"part-{message_id}", "type": "text", "text": text}]}


def assistant(message_id: str, parent_id: str, text: str = "", finish: str = "stop") -> dict[str, Any]:
    return {"info": {"id": message_id, "sessionID": "session", "role": "assistant", "parentID": parent_id,
                     "agent": "build", "time": {"created": 2, "completed": 3}, "finish": finish},
            "parts": [{"id": f"part-{message_id}", "type": "text", "text": text}]}


def test_actual_compaction_shape_preserves_report_before_last_http_reply():
    messages = compacted_messages()
    turn = collect_turn(messages, user_message_id=messages[0]["info"]["id"], reply=messages[-1])
    assert turn.text == "Detailed review report written before compaction.\n\nThe report is delivered above."
    assert [part.message_id for part in turn.text_parts] == [messages[4]["info"]["id"], messages[8]["info"]["id"]]
    assert turn.text_parts[0].part_id == "prt_16cca2b5d001h4laJXUDOPw1Ir"
    assert turn.messages == messages  # Internal summary/continuation evidence is still available.
    assert turn.reply == messages[-1]
    assert turn.final_message_id == messages[-1]["info"]["id"]
    assert turn.finish_reason == "stop"
    assert turn.message_errors == {}
    assert turn.length_message_ids == turn.unfinished_message_ids == []


def test_turn_boundaries_exclude_other_tasks_but_keep_intermediate_assistant_text():
    messages = [user("old"), assistant("old-reply", "old", "Old report"), user("task"),
                assistant("progress", "task", "Progress", "tool-calls"),
                assistant("report", "task", "Report"), user("next"), assistant("next-reply", "next", "Next report")]
    turn = collect_turn(messages, user_message_id="task")
    assert turn.text == "Progress\n\nReport"
    assert [message["info"]["id"] for message in turn.messages] == ["task", "progress", "report"]
    assert turn.final_message_id == "report"


def test_internal_text_flags_are_excluded_without_filtering_real_user_summary_metadata():
    prompt = user("task")
    prompt["info"]["summary"] = {"diffs": []}
    reply = assistant("reply", "task", "Real answer")
    reply["parts"] += [{"type": "text", "text": "Synthetic", "synthetic": True},
                       {"type": "text", "text": "Ignored", "ignored": True},
                       {"type": "reasoning", "text": "Private reasoning"}]
    assert collect_turn([prompt, reply], user_message_id="task").text == "Real answer"


@pytest.mark.parametrize("summary_info", [{"summary": True}, {"agent": "compaction"}, {"mode": "compaction"}])
def test_summary_detection_does_not_replace_final_assistant(summary_info):
    reply = assistant("reply", "task", "Report")
    summary = assistant("summary", "task", "Internal summary")
    summary["info"].update(summary_info)
    turn = collect_turn([user("task"), reply, summary], user_message_id="task")
    assert turn.text == "Report"
    assert turn.final_message_id == "reply"


def test_partial_text_does_not_erase_latest_error_or_missing_finish():
    progress = assistant("progress", "task", "Read the first file.", "tool-calls")
    failed = assistant("failed", "task")
    failed["info"].pop("finish")
    failed["info"]["time"].pop("completed")
    failed["info"]["error"] = {"name": "APIError", "data": {"message": "Provider interrupted"}}
    turn = collect_turn([user("task"), progress, failed], user_message_id="task", reply=failed)
    assert turn.text.startswith("Read the first file.")
    assert turn.final_message_id == "failed" and turn.finish_reason is None
    assert turn.message_errors == {"failed": failed["info"]["error"]}
    assert turn.unfinished_message_ids == ["failed"]
    assert "success" not in turn.model_dump()


def test_length_and_summary_errors_remain_visible_after_later_stop():
    truncated = assistant("truncated", "task", "Partial", "length")
    summary = assistant("summary", "task", "Internal")
    summary["info"].update(summary=True, error={"name": "SummaryError"})
    final = assistant("final", "task", "Final")
    turn = collect_turn([user("task"), truncated, summary, final], user_message_id="task")
    assert turn.finish_reason == "stop"
    assert turn.length_message_ids == ["truncated"]
    assert turn.message_errors == {"summary": {"name": "SummaryError"}}
    assert turn.text == "Partial\n\nFinal"


def test_terminal_length_and_missing_completion_are_retained():
    reply = assistant("reply", "task", "Unfinished report", "length")
    reply["info"]["time"].pop("completed")
    turn = collect_turn([user("task"), reply], user_message_id="task")
    assert turn.finish_reason == "length"
    assert turn.length_message_ids == turn.unfinished_message_ids == ["reply"]


def test_finished_empty_text_is_not_assigned_a_task_outcome():
    turn = collect_turn([user("task"), assistant("reply", "task")], user_message_id="task")
    assert turn.finish_reason == "stop" and turn.text == ""
    assert turn.unfinished_message_ids == []


def test_reply_missing_from_snapshot_is_retained_only_with_parent_in_turn():
    reply = assistant("reply", "task", "Report")
    turn = collect_turn([user("task")], user_message_id="task", reply=reply)
    assert turn.messages[-1] == reply and turn.text == "Report"
    reply["info"]["parentID"] = "another-task"
    with pytest.raises(TurnCollectionError, match="cannot be attributed"):
        collect_turn([user("task")], user_message_id="task", reply=reply)


def test_stale_snapshot_cannot_erase_http_reply_error():
    snapshot_reply = assistant("reply", "task", "Partial")
    reply = copy.deepcopy(snapshot_reply)
    reply["info"]["finish"] = "length"
    reply["info"]["error"] = {"name": "APIError"}
    turn = collect_turn([user("task"), snapshot_reply], user_message_id="task", reply=reply)
    assert turn.finish_reason is None  # Conflicting terminal records cannot establish normal completion.
    assert turn.length_message_ids == turn.unfinished_message_ids == ["reply"]
    assert turn.message_errors == {"reply": {"name": "APIError"}}
    assert turn.messages[-1] == snapshot_reply
    assert turn.reply == reply


def test_short_http_reply_cannot_erase_full_native_report():
    history_reply = assistant("reply", "task", "Complete report with evidence")
    reply = copy.deepcopy(history_reply)
    reply["parts"][0]["text"] = "Complete report"
    turn = collect_turn([user("task"), history_reply], user_message_id="task", reply=reply)
    assert turn.text == "Complete report with evidence"
    assert turn.messages[-1] == history_reply and turn.reply == reply
    assert turn.finish_reason == "stop" and turn.unfinished_message_ids == []


def test_both_distinct_native_errors_are_preserved():
    history_reply = assistant("reply", "task")
    history_reply["info"]["error"] = {"name": "HistoryError"}
    reply = copy.deepcopy(history_reply)
    reply["info"]["error"] = {"name": "ReplyError"}
    turn = collect_turn([user("task"), history_reply], user_message_id="task", reply=reply)
    assert turn.message_errors["reply"] == {"history": {"name": "HistoryError"}, "reply": {"name": "ReplyError"}}


def test_same_id_with_different_parent_is_rejected():
    history_reply = assistant("reply", "task")
    reply = copy.deepcopy(history_reply)
    reply["info"]["parentID"] = "another-task"
    with pytest.raises(TurnCollectionError, match="identity conflicts"):
        collect_turn([user("task"), history_reply], user_message_id="task", reply=reply)


@pytest.mark.parametrize("parent_id", ["old", "missing", "reply", "later"])
@pytest.mark.parametrize("with_reply", [False, True])
def test_assistant_parent_must_precede_it_inside_the_anchored_turn(parent_id, with_reply):
    reply = assistant("reply", parent_id, "Report belonging to an unproven task")
    messages = [user("old"), user("task"), reply, assistant("later", "task")]
    with pytest.raises(TurnCollectionError, match="parent.*turn"):
        collect_turn(messages, user_message_id="task", reply=reply if with_reply else None)


@pytest.mark.parametrize("messages,anchor,reply,match", [
    ([user("task"), user("task")], "task", None, "duplicate"),
    ([user("task")], "missing", None, "missing from"),
    ([assistant("reply", "task")], "reply", None, "external user"),
    ([{"info": {"role": "user"}}], "task", None, "info.id"),
    ([user("old"), assistant("old-reply", "old"), user("task")], "task", assistant("old-reply", "old"), "attributed"),
])
def test_unattributable_history_is_rejected(messages, anchor, reply, match):
    with pytest.raises(TurnCollectionError, match=match):
        collect_turn(messages, user_message_id=anchor, reply=reply)


def test_cross_session_message_is_rejected():
    reply = assistant("reply", "task")
    reply["info"]["sessionID"] = "another-session"
    with pytest.raises(TurnCollectionError, match="another session"):
        collect_turn([user("task"), reply], user_message_id="task")


async def test_client_collects_whole_turn_using_existing_http_endpoints():
    messages = compacted_messages()
    sid = messages[0]["info"]["sessionID"]
    requested: list[str] = []

    def respond(request):
        requested.append(request.method)
        assert request.url.path == f"/session/{sid}/message"
        if request.method == "POST":
            assert json.loads(request.content) == {"model": {"providerID": "internal", "modelID": "model"},
                                                  "parts": [{"type": "text", "text": "Review the source."}]}
            return httpx.Response(200, json=messages[-1])
        return httpx.Response(200, json=[] if len(requested) == 1 else messages)

    client = OpenCodeClient("http://harness", transport=httpx.MockTransport(respond))
    try:
        turn = await client.send_turn(sid, "Review the source.", provider_id="internal", model_id="model")
    finally:
        await client.aclose()
    assert requested == ["GET", "POST", "GET"]
    assert "Detailed review report" in turn.text
    assert turn.reply == messages[-1]


class Harness:
    def __init__(self, before, after, reply):
        self.before, self.after, self.reply = before, after, reply
        self.reads = 0

    async def list_messages(self, session_id):
        self.reads += 1
        return self.before if self.reads == 1 else self.after

    async def send_message(self, session_id, text, *, provider_id, model_id):
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


async def test_reused_session_anchors_only_new_prompt():
    before = [user("old"), assistant("old-reply", "old", "Old report")]
    reply = assistant("reply", "task", "New report")
    harness = Harness(before, before + [user("task"), reply], reply)
    turn = await send_turn(harness, "session", "Task", provider_id="internal", model_id="model")
    assert turn.text == "New report" and turn.user_message_id == "task"


@pytest.mark.parametrize("new_prompts", [[], [user("first"), user("second")]])
async def test_missing_or_concurrent_external_prompts_are_rejected(new_prompts):
    harness = Harness([], new_prompts, assistant("reply", "first"))
    with pytest.raises(TurnCollectionError, match="exactly one"):
        await send_turn(harness, "session", "Task", provider_id="internal", model_id="model")


async def test_transport_failure_is_propagated_without_constructing_a_completed_turn():
    error = httpx.ReadTimeout("Harness did not complete")
    harness = Harness([], [], error)
    with pytest.raises(httpx.ReadTimeout) as caught:
        await send_turn(harness, "session", "Task", provider_id="internal", model_id="model")
    assert caught.value is error
    assert harness.reads == 1
