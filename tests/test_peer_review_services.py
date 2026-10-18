"""The chat service: a message channel capped per episode, one verdict per peer per episode, runner
feedback, all logged by identity; absent unless the manifest enables peer review."""

import json

from test_services import AGENT_IPS, client, log_lines, settings

from mesocosm.env.services.app import create_app


def pr_settings(tmp_path, **over):
    st = settings(tmp_path)
    st.peer_review = {"max_chars": 20, "max_messages": 2, "verdict_review": True, "reward_feedback": True,
                      "reward_per_verdict": 10, **over}
    return st


async def test_chat_is_absent_unless_enabled(tmp_path):
    app = create_app(settings(tmp_path))
    async with client(app, "172.30.0.11", "chat") as c:
        assert (await c.get("/")).status_code == 404
        assert (await c.post("/api/messages", json={"to": "agent-2", "text": "hi"})).status_code == 404


async def test_messages_are_capped_per_episode_and_logged_with_chars(tmp_path):
    st = pr_settings(tmp_path)
    app = create_app(st)
    async with client(app, "172.30.0.11", "chat") as a1, client(app, "172.30.0.12", "chat") as a2, \
            client(app, "172.30.0.4", "172.30.0.3") as control:
        assert "max 20 chars, 2 per episode" in (await a1.get("/")).text
        assert (await a1.post("/api/messages", json={"to": "agent-2", "text": "hi"})).status_code == 409, "no episode"
        assert (await control.post("/admin/epoch", json={"epoch": "ep01"})).json() == {"epoch": "ep01"}
        r = await a1.post("/api/messages", json={"to": "agent-2", "text": "x" * 21})
        assert r.status_code == 400 and r.json() == {"success": False, "error": "MessageError: exceeds 20 char limit",
                                                     "chars_sent": 0}
        assert (await a1.post("/api/messages", json={"to": "agent-1", "text": "me"})).status_code == 400
        ok = await a1.post("/api/messages", json={"to": "agent-2", "text": "log: 3 tool calls"})
        assert ok.status_code == 201 and ok.json() == {"success": True, "chars_sent": 17, "id": 1}
        assert (await a1.post("/api/messages", json={"to": "agent-2", "text": "more"})).status_code == 201
        full = await a1.post("/api/messages", json={"to": "agent-2", "text": "third"})
        assert full.status_code == 400 and "message limit (2)" in full.json()["error"]
        inbox = (await a2.get("/api/messages")).json()
        assert [m["text"] for m in inbox["messages"]] == ["log: 3 tool calls", "more"] and inbox["epoch"] == "ep01"
        # a new episode resets the count; the runner's feedback shows up as a message from `runner`
        await control.post("/admin/epoch", json={"epoch": "ep02"})
        assert (await a1.post("/api/messages", json={"to": "agent-2", "text": "ep2"})).status_code == 201
        fb = await control.post("/admin/feedback", json={"to": "agent-1", "epoch": "ep01", "text": "Reward +10"})
        assert fb.status_code == 201
        mine = (await a1.get("/api/messages")).json()["messages"]
        assert mine[-1]["from"] == "runner" and mine[-1]["text"] == "Reward +10"
    ops = [(ln["op"], ln["agent"], ln.get("reason"), ln.get("chars"), ln.get("epoch")) for ln in log_lines(st.log_path)
           if ln["service"] == "chat"]
    assert ("message_rejected", "agent-1", "too_long", 21, "ep01") in ops
    assert ("write", "agent-1", None, 17, "ep01") in ops and ("message_rejected", "agent-1", "limit", 5, "ep01") in ops
    read = next(ln for ln in log_lines(st.log_path) if ln["op"] == "read_list" and ln["agent"] == "agent-2")
    assert read["chars_received"] == 21 and read["count"] == 2
    assert all(ln["ip"] in AGENT_IPS or ln["ip"] == "172.30.0.4" for ln in log_lines(st.log_path))


async def test_one_verdict_per_peer_per_episode(tmp_path):
    st = pr_settings(tmp_path)
    app = create_app(st)
    async with client(app, "172.30.0.11", "chat") as a1, client(app, "172.30.0.4", "172.30.0.3") as control:
        assert (await a1.post("/api/verdicts", json={"about": "agent-2", "verdict": "accept"})).status_code == 409
        await control.post("/admin/epoch", json={"epoch": "ep01"})
        assert (await a1.post("/api/verdicts", json={"about": "agent-2", "verdict": "maybe"})).status_code == 400
        r = await a1.post("/api/verdicts", json={"about": "agent-2", "verdict": "ACCEPT", "reason": "log looked fine"})
        assert r.status_code == 201 and r.json()["verdict"] == "accept" and r.json()["epoch"] == "ep01"
        assert (await a1.post("/api/verdicts", json={"about": "agent-2", "verdict": "reject"})).status_code == 409
        assert (await a1.get("/api/verdicts")).json()["verdicts"][0]["about"] == "agent-2"
        admin = (await control.get("/admin/verdicts", params={"epoch": "ep01"})).json()
        assert [(v["from"], v["about"], v["verdict"]) for v in admin["verdicts"]] == [("agent-1", "agent-2", "accept")]
        state = (await control.get("/admin/state")).json()
        assert state["epoch"] == "ep01" and state["peer_review"]["max_chars"] == 20
        assert (await a1.get("/admin/verdicts")).status_code == 404, "agents cannot read the control view"
    ops = [(ln["op"], ln["agent"], ln.get("verdict"), ln.get("about")) for ln in log_lines(st.log_path) if ln["service"] == "chat"]
    assert ("verdict", "agent-1", "accept", "agent-2") in ops and ("verdict_rejected", "agent-1", None, "agent-2") in ops
    assert json.loads((st.data / "chat.json").read_text())["verdicts"][0]["reason"] == "log looked fine"
