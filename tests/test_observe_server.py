"""The observe server tails a bundle directory, serves snapshots, SSE and drill-down endpoints,
and rebuilds the state at a point in time without keeping anything per client."""

import asyncio
import json

import httpx
import pytest
from test_observe_state import ev, gateway_record, oc, tool_part

from mesocosm.observe.server import Follower, Source, create_app, make_sources, sse_events, state_at


def write_lines(path, records):
    with path.open("a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / "run_x"
    (root / "native").mkdir(parents=True)
    (root / "manifest.json").write_text(json.dumps({"digest": "d", "manifest": {"agents": [{"id": "agent-1"}]}}))
    write_lines(root / "events.jsonl", [
        ev("harness.session", 10.0, "agent-1", session_id="s1", task="hello"),
        oc("session.status", 11.0, "s1", "agent-1", status={"type": "busy"}),
        oc("message.part.updated", 12.0, "s1", "agent-1", **tool_part("s1", "c1", "bash", "running", {"command": "ls"})),
    ])
    write_lines(root / "receipts.jsonl", [{"requested": {"agent_id": "agent-1", "requests": 200}, "result": "applied",
                                           "enforced_at": {"component": "gateway", "wallclock": 9.5}}])
    gw = tmp_path / "gateway.jsonl"
    write_lines(gw, [gateway_record(0.5, "agent-1", "req-old", 1),                 # an earlier run under the same label
                     gateway_record(11.5, "agent-1", "req-1", 2, ("bash",)),
                     gateway_record(11.6, "curl", "req-curl", 1)])        # another caller on the same gateway
    return root, gw


async def test_state_sse_and_drilldown(bundle):
    root, gw = bundle
    app = create_app(root, gw)
    follower = app.state.follower
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://obs") as c:
        r = await c.get("/state")
        await asyncio.sleep(0.4)                      # the tail task starts on first request
        r = await c.get("/state")
        snap = r.json()["state"]
        assert r.json()["meta"]["mode"] == "live"
        assert [a["agent_id"] for a in snap["agents"]] == ["agent-1"] and snap["ignored_records"] == 2
        agent = snap["agents"][0]
        assert agent["agent_id"] == "agent-1" and agent["status"] == "tool" and agent["requests"] == 1
        assert agent["budget"] == {"requests": 200, "receipt": "applied"}
        assert snap["sessions"]["s1"]["summary"] == "Running ls"

        # appended lines and the end-of-run summary are picked up without restarting anything
        write_lines(root / "events.jsonl", [oc("session.status", 13.0, "s1", "agent-1", status={"type": "idle"})])
        (root / "summary.json").write_text(json.dumps({"status": "ok"}))
        await asyncio.sleep(0.6)
        after = (await c.get("/state")).json()["state"]
        assert after["agents"][0]["status"] == "idle" and after["status"] == "ok"

        d = (await c.get("/session/s1")).json()
        assert d["items"][0]["tool"] == "bash" and d["requests"][0]["request_id"] == "req-1"
        assert (await c.get("/session/nope")).status_code == 404
        rec = (await c.get("/request/agent-1/req-1")).json()
        assert rec["response"]["prompt_token_ids"] == [1, 2, 3]
        assert (await c.get("/request/agent-1/missing")).status_code == 404
        assert (await c.get("/agent/agent-1/requests")).json()["requests"][0]["n_messages"] == 2

        # point-in-time rebuild: before the tool call the session was waiting for the model
        past = (await c.get("/state", params={"at": 11.2})).json()
        assert past["meta"]["at"] == 11.2
        assert past["state"]["sessions"]["s1"]["summary"] == "Waiting for the model"
        assert past["state"]["agents"][0]["requests"] == 0

        assert (await c.get("/")).text.startswith("<!doctype html>")
    await follower.stop()


async def test_sse_sends_a_snapshot_and_stops_when_the_client_leaves(bundle):
    root, gw = bundle
    follower = Follower(sources=make_sources(root, gw))
    follower.ensure_started()
    await asyncio.sleep(0.4)
    polls = {"n": 0}

    async def gone():
        polls["n"] += 1
        return polls["n"] > 1

    chunks = [c async for c in sse_events(follower, lambda: {"mode": "live"}, gone)]
    assert len(chunks) == 1 and chunks[0].startswith(b"event: state\ndata: ")
    payload = json.loads(chunks[0].split(b"data: ", 1)[1])
    assert payload["meta"] == {"mode": "live"} and payload["state"]["agents"][0]["agent_id"] == "agent-1"
    await follower.stop()


async def test_replay_feeds_records_in_time_order(bundle):
    root, gw = bundle
    app = create_app(root, gw, replay_speed=1e6)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://obs") as c:
        await c.get("/state")
        for _ in range(20):
            await asyncio.sleep(0.05)
            r = (await c.get("/state")).json()
            if r["meta"]["replay_done"]:
                break
        assert r["meta"]["mode"] == "replay" and r["meta"]["replay_done"]
        assert r["state"]["ingested"] == 7 and r["state"]["agents"][0]["requests"] == 1
        assert r["state"]["status"] == "running" and r["state"]["ignored_records"] == 2   # the old record and curl
    await app.state.follower.stop()


def test_source_handles_partial_lines_and_truncation(tmp_path):
    p = tmp_path / "f.jsonl"
    src = Source(p, "apply_bundle_event")
    assert src.read_new_lines() == []
    p.write_bytes(b'{"a":1}\n{"b":')
    assert src.read_new_lines() == [b'{"a":1}']
    with p.open("ab") as f:
        f.write(b'2}\n')
    assert src.read_new_lines() == [b'{"b":2}']
    p.write_bytes(b'{"c":3}\n')          # shorter than before: treated as a new file
    assert src.read_new_lines() == [b'{"c":3}']
    assert src.read_new_lines() == []


def test_state_at_ignores_later_records(bundle):
    root, gw = bundle
    early = state_at(root, gw, 9.0)["agents"]
    assert [a["agent_id"] for a in early] == ["agent-1"] and early[0]["status"] == "idle" and early[0]["requests"] == 0
    assert state_at(root, gw, 10.5)["agents"][0]["status"] == "idle"
    assert state_at(root, gw, 99.0)["agents"][0]["status"] == "tool"


async def test_the_page_its_sprites_font_and_the_constraint_board_are_served(bundle):
    root, gw = bundle
    app = create_app(root, gw, gpu_hour_cost=0.8)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://observe") as c:
        page = (await c.get("/")).text
        assert '<script src="/static/sprites.js"></script>' in page and "Silkscreen" in page
        assert '<g id="map"></g><g id="live"></g>' in page, "the forest map and the live layer above it"
        sprites = await c.get("/static/sprites.js")
        assert sprites.status_code == 200 and "window.MesoSprites" in sprites.text
        assert "floor:" in sprites.text and "meadow:" in sprites.text, "the ground tiles of the map"
        font = await c.get("/static/fonts/silkscreen-latin-400-normal.woff2")
        assert font.status_code == 200 and font.content[:4] == b"wOF2"
        assert (await c.get("/static/fonts/OFL.txt")).status_code == 200
        assert (await c.get("/static/../server.py")).status_code == 404
        state = (await c.get("/state")).json()["state"]
        keys = [x["key"] for x in state["constraints"]]
        assert keys[:3] == ["sandbox", "internet", "channels"] and "cost" in keys
        cost = next(x for x in state["constraints"] if x["key"] == "cost")
        assert cost["rows"][2] == ["GPU-hour rate", "0.8"]
        past = (await c.get("/state", params={"at": 11.2})).json()["state"]
        assert next(x for x in past["constraints"] if x["key"] == "cost")["rows"][2] == ["GPU-hour rate", "0.8"]
