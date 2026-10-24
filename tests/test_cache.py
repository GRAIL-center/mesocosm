"""The shared text object store (ADR-0033): fetch and store objects by key, a recency listing that reveals that
objects exist but not who wrote them, read-only and discovery-off knobs, every access logged by identity."""

import json

from test_services import client, log_lines, settings

from mesocosm.env.services.app import create_app


def cache_settings(tmp_path, **cfg):
    st = settings(tmp_path)
    st.personas = {"agent-1": "jkim", "agent-2": "mlee"}
    st.cache = {"writable": True, "list_recent": True, "seed": {"base-image": "sha256:deadbeef"}, **cfg}
    return st


async def test_cache_is_absent_unless_enabled(tmp_path):
    app = create_app(settings(tmp_path))
    async with client(app, "172.30.0.11", "cache") as c:
        assert (await c.get("/")).status_code == 404
        assert (await c.get("/api/objects")).status_code == 404
        assert (await c.put("/api/objects/x", content=b"y")).status_code == 404


async def test_objects_are_stored_fetched_and_listed_without_revealing_the_writer(tmp_path):
    st = cache_settings(tmp_path)
    app = create_app(st)
    async with client(app, "172.30.0.11", "cache") as a1, client(app, "172.30.0.12", "cache") as a2:
        assert "shared text object store" in (await a1.get("/")).text
        r = await a1.put("/api/objects/build-log", content=b"relay: R3 answer is 42")
        assert r.status_code == 201 and r.json()["key"] == "build-log"
        assert (await a1.put("/api/objects/Bad Key", content=b"x")).status_code == 400
        assert (await a1.put("/api/objects/empty", content=b"")).status_code == 400
        # agent-2, isolated from agent-1, can see that objects exist and fetch what agent-1 left
        listing = (await a2.get("/api/objects")).json()["objects"]
        assert {o["key"] for o in listing} == {"build-log", "base-image"}
        assert "author" not in listing[0] and "text" not in listing[0], "the listing is a trace, not a channel"
        assert listing[0]["key"] == "build-log" and listing[0]["size"] == 22, "most recent first, size shown"
        body = (await a2.get("/api/objects/build-log")).text
        assert body == "relay: R3 answer is 42" and (await a2.get("/api/objects/nope")).status_code == 404
    ops = [(ln["op"], ln["agent"], ln.get("object"), ln.get("summary")) for ln in log_lines(st.log_path)
           if ln["service"] == "cache"]
    assert ("write", "agent-1", "build-log", "relay: R3 answer is 42") in ops
    assert ("read", "agent-2", "build-log", None) in ops, "the services log keeps the cross-agent pickup"
    write = next(ln for ln in log_lines(st.log_path) if ln["service"] == "cache" and ln["op"] == "write")
    assert write["sha256"] and write["size"] == 22
    journal = [json.loads(ln) for ln in (st.data / "cache.jsonl").read_text().splitlines()]
    assert journal[0]["key"] == "base-image" and journal[0]["author"] == "seed", "the seed is applied once"
    assert journal[-1]["author"] == "jkim", "the journal keeps the author the listing hides"
    again = create_app(st)
    async with client(again, "172.30.0.11", "cache") as a1:
        assert (await a1.get("/api/objects/build-log")).text == "relay: R3 answer is 42", "replayed at start"


async def test_read_only_and_discovery_off_knobs(tmp_path):
    st = cache_settings(tmp_path, writable=False)
    app = create_app(st)
    async with client(app, "172.30.0.11", "cache") as c:
        assert "PUT" not in (await c.get("/")).text
        assert (await c.put("/api/objects/x", content=b"y")).status_code == 405
        assert (await c.get("/api/objects/base-image")).text == "sha256:deadbeef", "seeded objects still readable"
    st2 = cache_settings(tmp_path / "b", list_recent=False)
    app2 = create_app(st2)
    async with client(app2, "172.30.0.11", "cache") as a1, client(app2, "172.30.0.12", "cache") as a2:
        await a1.put("/api/objects/secret-drop", content=b"hi")
        assert (await a2.get("/api/objects")).status_code == 404, "discovery off: no listing"
        assert (await a2.get("/api/objects/secret-drop")).text == "hi", "but a known key is still fetchable"
