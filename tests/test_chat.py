"""The chat service on its own (ADR-0027): public channels and direct messages between the members, a
per-run message cap when no peer review opens episodes, every read and write logged by identity."""

import json

from test_services import client, log_lines, settings

from mesocosm.env.services.app import create_app


def chat_settings(tmp_path, **cfg):
    st = settings(tmp_path)
    st.chat = {"channels": ["general", "ops"], "max_chars": None, "max_messages": None, **cfg}
    return st


async def test_channels_are_public_and_direct_messages_are_private(tmp_path):
    st = chat_settings(tmp_path)
    st.personas = {"agent-1": "jkim", "agent-2": "mlee"}
    app = create_app(st)
    async with client(app, "172.30.0.11", "chat") as a1, client(app, "172.30.0.12", "chat") as a2, \
            client(app, "10.9.9.9", "chat") as stranger:
        usage = (await a1.get("/")).text
        assert "members: jkim, mlee" in usage and "channels: #general, #ops" in usage and "verdicts" not in usage
        assert (await a1.post("/api/messages", json={"to": "#general", "text": "deploy at 5"})).status_code == 201
        assert (await a1.post("/api/messages", json={"to": "mlee", "text": "psst"})).status_code == 201
        assert (await a1.post("/api/messages", json={"to": "#nope", "text": "x"})).status_code == 400
        assert (await a1.post("/api/messages", json={"to": "nobody", "text": "x"})).status_code == 400
        assert (await a1.post("/api/messages", json={"to": "jkim", "text": "me"})).status_code == 400
        assert (await a1.post("/api/messages", json={"to": "#ops", "text": "  "})).status_code == 400
        assert (await a2.post("/api/messages", json={"to": "#ops", "text": "ack"})).status_code == 201
        everything = (await a2.get("/api/messages")).json()["messages"]
        assert [(m["from"], m["to"], m.get("channel"), m["text"]) for m in everything] == [
            ("jkim", None, "general", "deploy at 5"), ("jkim", "mlee", None, "psst"), ("mlee", None, "ops", "ack")]
        assert [m["text"] for m in (await a2.get("/api/messages", params={"channel": "#ops"})).json()["messages"]] == ["ack"]
        assert (await a2.get("/api/messages", params={"channel": "nope"})).status_code == 404
        ts = everything[0]["ts"]
        assert [m["text"] for m in (await a1.get("/api/messages", params={"since": ts})).json()["messages"]] == ["psst", "ack"]
        # a stranger (another address) sees the channels, never the direct messages
        assert [m["text"] for m in (await stranger.get("/api/messages")).json()["messages"]] == ["deploy at 5", "ack"]
        assert (await a1.get("/api/verdicts")).status_code == 404, "verdicts belong to peer review"
    chat = [ln for ln in log_lines(st.log_path) if ln["service"] == "chat"]
    writes = [(ln["agent"], ln.get("to"), ln.get("channel"), ln["chars"], ln["message_id"]) for ln in chat if ln["op"] == "write"]
    assert writes == [("agent-1", None, "general", 11, 1), ("agent-1", "agent-2", None, 4, 2), ("agent-2", None, "ops", 3, 3)]
    read = next(ln for ln in chat if ln["op"] == "read_list" and ln["agent"] == "agent-2")
    assert read["count"] == 3 and read["chars_received"] == 15 and read["channel"] is None
    journal = [json.loads(ln) for ln in (st.data / "chat.jsonl").read_text().splitlines()]
    assert [r["kind"] for r in journal] == ["message"] * 3 and journal[0]["channel"] == "general" and journal[0]["epoch"] is None


async def test_limits_are_per_run_without_peer_review_and_refusals_are_logged(tmp_path):
    st = chat_settings(tmp_path, max_chars=10, max_messages=2)
    app = create_app(st)
    async with client(app, "172.30.0.11", "chat") as a1, client(app, "172.30.0.4", "172.30.0.3") as control:
        assert "max 10 chars, 2 per run" in (await a1.get("/")).text
        long = await a1.post("/api/messages", json={"to": "#general", "text": "x" * 11})
        assert long.status_code == 400 and long.json()["error"] == "MessageError: exceeds 10 char limit"
        assert (await a1.post("/api/messages", json={"to": "#general", "text": "one"})).status_code == 201
        assert (await a1.post("/api/messages", json={"to": "agent-2", "text": "two"})).status_code == 201
        third = await a1.post("/api/messages", json={"to": "#general", "text": "three"})
        assert third.status_code == 400 and third.json()["error"] == "MessageError: message limit (2) reached for this run"
        state = (await control.get("/admin/state")).json()
        assert state["chat"] == {"channels": ["general", "ops"], "max_chars": 10, "max_messages": 2}
        assert state["peer_review"] is None
    rejected = [(ln.get("reason"), ln.get("channel"), ln.get("chars")) for ln in log_lines(st.log_path)
                if ln.get("op") == "message_rejected"]
    assert rejected == [("too_long", "general", 11), ("limit", "general", 5)]
