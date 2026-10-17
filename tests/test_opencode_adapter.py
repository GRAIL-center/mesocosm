import json

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from mesocosm.adapters.opencode.client import OpenCodeClient, normalize_event, render_opencode_config


def fake_opencode() -> FastAPI:
    """Stand-in for `opencode serve`, following the server docs' endpoint shapes."""
    app = FastAPI()
    app.state.sessions = {}
    app.state.messages = {}

    @app.get("/global/health")
    async def health():
        return {"healthy": True, "version": "fake"}

    @app.get("/config/providers")
    async def providers():
        return {"providers": [{"id": "mesocosm", "models": {"mesocosm-3b": {}}}], "default": {"mesocosm": "mesocosm-3b"}}

    @app.post("/session")
    async def create(request: Request):
        body = await request.json()
        sid = f"ses_{len(app.state.sessions) + 1}"
        app.state.sessions[sid] = {"id": sid, "title": body.get("title")}
        app.state.messages[sid] = []
        return app.state.sessions[sid]

    @app.post("/session/{sid}/message")
    async def message(sid: str, request: Request):
        body = await request.json()
        assert body["model"] == {"providerID": "mesocosm", "modelID": "mesocosm-3b"}
        user = {"info": {"id": "msg_u", "role": "user", "sessionID": sid}, "parts": body["parts"]}
        reply = {"info": {"id": "msg_a", "role": "assistant", "sessionID": sid},
                 "parts": [{"type": "tool", "tool": "bash", "state": {"status": "completed", "input": {"command": "ls"}}},
                           {"type": "text", "text": "done"}]}
        app.state.messages[sid] += [user, reply]
        return reply

    @app.get("/session/{sid}/message")
    async def list_messages(sid: str):
        return app.state.messages[sid]

    @app.get("/event")
    async def events():
        async def gen():
            yield b'data: {"type":"server.connected","properties":{}}\n\n'
            yield b'data: {"type":"message.part.updated","properties":{"part":{"sessionID":"ses_1","type":"tool","tool":"bash","state":{"status":"running"}}}}\n\n'
            yield b"data: not-json\n\n"
            yield b'data: {"type":"session.status","properties":{"sessionID":"ses_1","status":"idle"}}\n\n'
        return StreamingResponse(gen(), media_type="text/event-stream")

    return app


async def test_client_drives_a_session_and_reads_events():
    app = fake_opencode()
    oc = OpenCodeClient("http://agent-1:4096", transport=httpx.ASGITransport(app=app))
    assert (await oc.health())["healthy"] is True
    assert [p["id"] for p in (await oc.providers())["providers"]] == ["mesocosm"]
    sid = await oc.create_session("t")
    reply = await oc.send_message(sid, "do it", provider_id="mesocosm", model_id="mesocosm-3b")
    assert reply["parts"][0]["tool"] == "bash"
    assert len(await oc.list_messages(sid)) == 2

    raws = [ev async for ev in oc.events()]
    assert [r["type"] for r in raws] == ["server.connected", "message.part.updated", "unparsed", "session.status"]

    normalized = [normalize_event(r, run_id="run_t", session_to_agent={"ses_1": "agent-1"}) for r in raws]
    assert normalized[0].kind == "opencode.server.connected" and normalized[0].agent_id is None
    tool_ev = normalized[1]
    assert tool_ev.kind == "opencode.message.part.updated" and tool_ev.agent_id == "agent-1"
    assert tool_ev.data["summary"] == {"part_type": "tool", "tool": "bash", "tool_status": "running"}
    assert tool_ev.data["raw"] == raws[1]
    assert normalized[3].data["summary"] == {"status": "idle"} and normalized[3].agent_id == "agent-1"
    await oc.aclose()


def test_render_opencode_config_routes_everything_through_the_gateway():
    cfg = render_opencode_config(agent_id="agent-7", run_id="run_1", gateway_url="http://gateway:8080/",
                                 model_id="mesocosm-3b", api_key="k")
    prov = cfg["provider"]["mesocosm"]
    assert prov["npm"] == "@ai-sdk/openai-compatible"
    assert prov["options"]["baseURL"] == "http://gateway:8080/v1"
    assert prov["options"]["headers"] == {"X-Mesocosm-Agent": "agent-7", "X-Mesocosm-Run": "run_1"}
    assert cfg["model"] == "mesocosm/mesocosm-3b" and cfg["permission"] == "allow"
    json.dumps(cfg)  # serializable
