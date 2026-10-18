"""The simulated clock, the ledger and the world-state export a verifier reads (ADR-0028)."""

import subprocess
import time

from test_services import client, log_lines, settings

from mesocosm.env.services.app import create_app


def world_settings(tmp_path):
    st = settings(tmp_path, channels=("pastebin",))
    st.personas = {"agent-1": "jkim", "agent-2": "mlee"}
    st.chat = {"channels": ["general"], "max_chars": None, "max_messages": None}
    st.clock = {"origin": "2026-01-05T09:00:00", "speed": 60.0}
    st.ledger = {"unit": "credits", "initial": {"agent-1": 100, "agent-2": 20}}
    return st


async def test_clock_counts_from_the_run_start_at_its_speed(tmp_path):
    st = world_settings(tmp_path)
    app = create_app(st)
    async with client(app, "172.30.0.11", "clock") as c, client(app, "172.30.0.4", "172.30.0.3") as control:
        await control.post("/admin/clock", json={"started": time.time() - 60})      # the run began a minute ago
        t = (await c.get("/")).json()
        assert t["now"].startswith("2026-01-05T10:0") and t["speed"] == 60.0 and 3590 < t["elapsed_seconds"] < 3700
        assert (await c.get("/api/time")).json()["origin"] == "2026-01-05T09:00:00"
        assert (await control.get("/admin/state")).json()["clock"]["speed"] == 60.0
    assert [ln["op"] for ln in log_lines(st.log_path) if ln["service"] == "clock"] == ["read", "read"]
    plain = create_app(settings(tmp_path))
    async with client(plain, "172.30.0.11", "clock") as c, client(plain, "172.30.0.11", "ledger") as led:
        assert (await c.get("/")).status_code == 404 and (await led.get("/api/balance")).status_code == 404


async def test_ledger_refuses_overdrafts_logs_transfers_and_replays(tmp_path):
    st = world_settings(tmp_path)
    app = create_app(st)
    async with client(app, "172.30.0.11", "ledger") as a1, client(app, "172.30.0.12", "ledger") as a2, \
            client(app, "172.30.0.4", "172.30.0.3") as control:
        assert "ledger (credits)" in (await a1.get("/")).text
        assert (await a1.get("/api/balance")).json() == {"account": "jkim", "balance": 100.0, "unit": "credits"}
        assert (await a2.get("/api/balances")).json()["balances"] == {"jkim": 100.0, "mlee": 20.0}
        r = await a2.post("/api/transfer", json={"to": "jkim", "amount": 25, "memo": "too much"})
        assert r.status_code == 402 and r.json() == {"error": "insufficient funds", "balance": 20.0}
        assert (await a2.post("/api/transfer", json={"to": "mlee", "amount": 1})).status_code == 400
        assert (await a2.post("/api/transfer", json={"to": "jkim", "amount": -5})).status_code == 400
        assert (await a2.post("/api/transfer", json={"to": "jkim", "amount": "lots"})).status_code == 400
        r = await a2.post("/api/transfer", json={"to": "jkim", "amount": 15.5, "memo": "for the review"})
        assert r.status_code == 201 and (r.json()["from"], r.json()["to"], r.json()["amount"]) == ("mlee", "jkim", 15.5)
        assert (await a1.get("/api/balance")).json()["balance"] == 115.5
        credit = await control.post("/admin/ledger", json={"to": "agent-2", "amount": 50, "memo": "task bonus"})
        assert credit.status_code == 201 and (await a2.get("/api/balance")).json()["balance"] == 54.5
        hist = (await a2.get("/api/history")).json()["history"]
        assert [(h["from"], h["to"], h["amount"], h["memo"]) for h in hist] == [("mlee", "jkim", 15.5, "for the review"),
                                                                                 ("bank", "mlee", 50.0, "task bonus")]
        assert (await a1.post("/admin/ledger", json={"to": "agent-1", "amount": 1})).status_code == 404
    ops = [(ln["op"], ln["agent"], ln.get("reason"), ln.get("amount"), ln.get("balance")) for ln in log_lines(st.log_path)
           if ln["service"] == "ledger"]
    assert ("transfer_rejected", "agent-2", "insufficient", 25.0, 20.0) in ops
    assert ("write", "agent-2", None, 15.5, 4.5) in ops
    again = create_app(st)
    async with client(again, "172.30.0.11", "ledger") as a1:
        assert (await a1.get("/api/balances")).json()["balances"] == {"jkim": 115.5, "mlee": 54.5}, \
            "the journal replays; the opening balances are applied once"


def _commit_and_push(work, bare):
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"}
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    (work / "a.txt").write_text("a\n")
    subprocess.run(["git", "-C", str(work), "add", "."], check=True)
    subprocess.run(["git", "-C", str(work), "commit", "-q", "-m", "a"], check=True, env=env)
    subprocess.run(["git", "-C", str(work), "push", "-q", str(bare), "main"], check=True)
    return subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()


async def test_world_state_export_is_the_control_planes_and_names_the_viewer(tmp_path):
    st = world_settings(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "intro.md").write_text("# Intro\n")
    app = create_app(st)
    sha = _commit_and_push(tmp_path / "work", st.data / "git" / "project.git")
    async with client(app, "172.30.0.11", "board") as b, client(app, "172.30.0.11", "chat") as ch, \
            client(app, "172.30.0.11", "pastebin") as pb, client(app, "172.30.0.4", "172.30.0.3") as control:
        await b.post("/api/posts", json={"title": "done", "body": "notes are in"})
        await ch.post("/api/messages", json={"to": "#general", "text": "hi all"})
        await pb.post("/api/paste", json={"text": "secret"})
        assert (await b.get("/admin/state/world")).status_code == 404, "agents get the usual 404"
        world = (await control.get("/admin/state/world", params={"agent": "agent-1"})).json()
        bare = (await control.get("/admin/state/world")).json()
    assert world["viewer"] == {"agent_id": "agent-1", "username": "jkim"} and "viewer" not in bare
    assert world["personas"] == {"agent-1": "jkim", "agent-2": "mlee"}
    assert world["board"]["posts"][0]["author"] == "jkim" and world["board"]["posts"][0]["body"] == "notes are in"
    assert world["chat"]["messages"][0]["channel"] == "general" and world["chat"]["epoch"] is None
    assert world["pastes"][0]["text"] == "secret"
    assert world["wiki"]["pages"][0]["slug"] == "intro" and world["wiki"]["pages"][0]["visible"] is True
    assert world["git"] == {"project": {"main": sha}}
    assert world["ledger"]["balances"] == {"agent-1": 100.0, "agent-2": 20.0} and world["clock"]["speed"] == 60.0
    assert [ln["op"] for ln in log_lines(st.log_path) if ln["service"] == "services"] == ["admin_denied"]
