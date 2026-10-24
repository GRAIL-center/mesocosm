"""Collect one external prompt's messages without confusing compaction with a final report."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field


class TurnText(BaseModel):
    message_id: str
    part_id: str | None = None
    text: str


class TaskTurn(BaseModel):
    """Native evidence and ordered assistant text; completion is not task success.

    Finish describes the last non-compaction assistant message. Error, length, and
    unfinished IDs retain evidence from every assistant message, including summaries.
    Callers decide whether the terminal state permits their independent verifier to run.
    """

    session_id: str
    user_message_id: str
    reply: dict[str, Any] | None = None
    messages: list[dict[str, Any]]
    text_parts: list[TurnText] = Field(default_factory=list)
    text: str = ""
    final_message_id: str | None = None
    finish_reason: str | None = None
    message_errors: dict[str, Any] = Field(default_factory=dict)
    length_message_ids: list[str] = Field(default_factory=list)
    unfinished_message_ids: list[str] = Field(default_factory=list)


class TurnCollectionError(ValueError):
    """The native history cannot be attributed to exactly one external prompt."""


class MessageHarness(Protocol):
    async def list_messages(self, session_id: str) -> list[dict[str, Any]]: ...

    async def send_message(
        self, session_id: str, text: str, *, provider_id: str, model_id: str,
    ) -> dict[str, Any]: ...


def _info(message: dict[str, Any]) -> dict[str, Any]:
    info = message.get("info")
    if not isinstance(info, dict) or not isinstance(info.get("id"), str) or not info["id"]:
        raise TurnCollectionError("native message is missing its info.id")
    return info


def _parts(message: dict[str, Any]) -> list[dict[str, Any]]:
    parts = message.get("parts", [])
    if not isinstance(parts, list) or not all(isinstance(part, dict) for part in parts):
        raise TurnCollectionError("native message parts must be objects")
    return parts


def _external_user(message: dict[str, Any]) -> bool:
    if _info(message).get("role") != "user":
        return False
    parts = _parts(message)
    if any(part.get("type") == "compaction" for part in parts):
        return False
    # OpenCode's automatic continuation is a user message containing synthetic text.
    # A normal user's info.summary is an object, not the assistant summary=True flag.
    return not parts or any(part.get("synthetic") is not True and part.get("ignored") is not True for part in parts)


def _summary(info: dict[str, Any]) -> bool:
    return info.get("summary") is True or info.get("agent") == "compaction" or info.get("mode") == "compaction"


def collect_turn(
    messages: list[dict[str, Any]], *, user_message_id: str, reply: dict[str, Any] | None = None,
) -> TaskTurn:
    """Collect native chronological history from a user prompt to the next external prompt.

    Compaction and synthetic continuation stay in raw evidence but not report text.
    The HTTP reply is often only the last of several assistant messages. If a history
    snapshot omits it, its native parent must belong to this turn before it can be added.
    Missing/ambiguous boundaries raise rather than silently returning another task's text.
    """
    ids = [_info(message)["id"] for message in messages]
    if len(ids) != len(set(ids)):
        raise TurnCollectionError("native history contains duplicate message IDs")
    if user_message_id not in ids:
        raise TurnCollectionError("external prompt is missing from native history")
    start = ids.index(user_message_id)
    if not _external_user(messages[start]):
        raise TurnCollectionError("turn must start at an external user prompt")
    session_id = _info(messages[start]).get("sessionID")
    if not isinstance(session_id, str) or not session_id:
        raise TurnCollectionError("external prompt is missing its sessionID")
    end = next((i for i in range(start + 1, len(messages)) if _external_user(messages[i])), len(messages))
    selected = list(messages[start:end])
    preceding_ids: set[str] = set()
    for message in selected:
        info = _info(message)
        if info.get("sessionID") != session_id:
            raise TurnCollectionError("turn contains messages from another session")
        # Chronological proximity alone does not bind a response to this prompt:
        # a delayed response can still belong to an earlier task in the same session.
        if info.get("role") == "assistant" and info.get("parentID") not in preceding_ids:
            raise TurnCollectionError("assistant parent must precede it inside the anchored turn")
        preceding_ids.add(info["id"])
    if reply is not None:
        info = _info(reply)
        if info.get("role") != "assistant" or info.get("sessionID") != session_id:
            raise TurnCollectionError("HTTP reply is not an assistant message in this session")
        selected_ids = [_info(message)["id"] for message in selected]
        if info["id"] in selected_ids:
            history_info = _info(selected[selected_ids.index(info["id"])])
            if (history_info.get("role") != "assistant" or history_info.get("parentID") != info.get("parentID")
                    or _summary(history_info) != _summary(info)):
                raise TurnCollectionError("HTTP reply identity conflicts with native history")
        elif info["id"] not in ids and info.get("parentID") in selected_ids:
            selected.append(reply)
        else:
            raise TurnCollectionError("HTTP reply cannot be attributed to the external prompt")
    turn = TaskTurn(session_id=session_id, user_message_id=user_message_id, reply=reply, messages=selected)
    for message in selected:
        info = _info(message)
        if info.get("role") != "assistant":
            continue
        message_id = info["id"]
        finish = info.get("finish")
        if info.get("error") is not None:
            turn.message_errors[message_id] = info["error"]
        if finish == "length":
            turn.length_message_ids.append(message_id)
        time = info.get("time")
        if not isinstance(time, dict) or time.get("completed") is None or not isinstance(finish, str) or not finish:
            turn.unfinished_message_ids.append(message_id)
        if _summary(info):
            continue
        turn.final_message_id = message_id
        turn.finish_reason = finish if isinstance(finish, str) else None
        for part in _parts(message):
            if (part.get("type") == "text" and isinstance(part.get("text"), str)
                    and part.get("synthetic") is not True and part.get("ignored") is not True):
                turn.text_parts.append(TurnText(message_id=message_id, part_id=part.get("id"), text=part["text"]))
    if reply is not None:
        reply_info = _info(reply)
        reply_id = reply_info["id"]
        history_info = next(_info(message) for message in selected if _info(message)["id"] == reply_id)
        error = reply_info.get("error")
        if error is not None:
            previous_error = turn.message_errors.get(reply_id)
            turn.message_errors[reply_id] = (
                {"history": previous_error, "reply": error}
                if previous_error is not None and previous_error != error else error
            )
        if reply_info.get("finish") == "length" and reply_id not in turn.length_message_ids:
            turn.length_message_ids.append(reply_id)
        # Keep both native records: neither a stale POST nor a stale GET may erase
        # evidence. Contradictory terminal metadata is unknown, not inferred success.
        history_time, reply_time = history_info.get("time"), reply_info.get("time")
        history_completed = history_time.get("completed") if isinstance(history_time, dict) else None
        reply_completed = reply_time.get("completed") if isinstance(reply_time, dict) else None
        if (history_info.get("finish") != reply_info.get("finish") or history_completed != reply_completed):
            if reply_id not in turn.unfinished_message_ids:
                turn.unfinished_message_ids.append(reply_id)
            if turn.final_message_id == reply_id:
                turn.finish_reason = None
    turn.text = "\n\n".join(part.text for part in turn.text_parts)
    return turn


async def send_turn(
    harness: MessageHarness, session_id: str, text: str, *, provider_id: str, model_id: str,
) -> TaskTurn:
    """Send one prompt and collect its whole turn using unmodified harness endpoints.

    A session must have one external writer during this call. Transport failures are
    propagated; callers retain their existing abort and partial-history recovery path.
    """
    before = await harness.list_messages(session_id)
    known_ids = {_info(message)["id"] for message in before}
    reply = await harness.send_message(session_id, text, provider_id=provider_id, model_id=model_id)
    after = await harness.list_messages(session_id)
    new_prompts = [message for message in after if _info(message)["id"] not in known_ids and _external_user(message)]
    if len(new_prompts) != 1:
        raise TurnCollectionError("expected exactly one new external prompt in the session")
    turn = collect_turn(after, user_message_id=_info(new_prompts[0])["id"], reply=reply)
    if turn.session_id != session_id:
        raise TurnCollectionError("native history belongs to another session")
    return turn
