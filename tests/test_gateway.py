import json

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from mesocosm.gateway.app import Settings, create_app, parse_sse
from mesocosm.gateway.client import GatewayAdmin

# ---------------------------------------------------------------------------------------
# parse_sse: pure


def test_parse_sse_collapses_content_tool_calls_usage_and_token_ids():
    raw = "\n".join([
        'data: {"id":"c1","model":"m","prompt_token_ids":[1,2,3],"choices":[{"index":0,"delta":{"role":"assistant","content":"Hel"},"token_ids":[10]}]}',
        'data: {"id":"c1","choices":[{"index":0,"delta":{"content":"lo"},"token_ids":[11]}]}',
        'data: {"id":"c1","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"bash","arguments":"{\\"cmd\\": \\"ls"}}]}}]}',
        'data: {"id":"c1","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\"}"}}]},"finish_reason":"tool_calls"}]}',
        'data: {"id":"c1","choices":[],"usage":{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}}',
        "data: [DONE]",
        "",
    ])
    out = parse_sse(raw)
    assert out["content"] == "Hello"
    assert out["tool_calls"] == [{"id": "call_1", "type": "function",
                                  "function": {"name": "bash", "arguments": '{"cmd": "ls"}'}}]
    assert out["finish_reason"] == "tool_calls"
    assert out["usage"]["completion_tokens"] == 2
    assert out["prompt_token_ids"] == [1, 2, 3]
    assert out["token_ids"] == [10, 11]
    assert out["chunks"] == 5 and out["unparsed_lines"] == 0


def test_parse_sse_tolerates_garbage_and_missing_fields():
    out = parse_sse("data: not json\n\nevent: ping\ndata: {\"choices\":[{\"delta\":{}}]}\n")
    assert out["unparsed_lines"] == 1
    assert out["content"] == "" and out["tool_calls"] == [] and out["token_ids"] is None


# ---------------------------------------------------------------------------------------
# proxy against a fake upstream (vLLM stand-in), both served in-process via ASGITransport


def fake_upstream() -> FastAPI:
    up = FastAPI()
    up.state.requests = []

    @up.get("/v1/models")
    async def models():
        return {"data": [{"id": "mesocosm-3b"}]}

    @up.post("/v1/chat/completions")
    async def chat(request: Request):
        body = await request.json()
        up.state.requests.append({"headers": dict(request.headers), "body": body})
        if body.get("max_tokens", 0) > 1000:
            return JSONResponse(status_code=400, content={"error": {"message": "This model's maximum context length "
                                                                    "is 16384 tokens.", "type": "BadRequestError"}})
        if body.get("stream"):
            async def gen():
                yield b'data: {"id":"s1","choices":[{"index":0,"delta":{"content":"ok"},"token_ids":[7]}],"prompt_token_ids":[1,2]}\n\n'
                yield b'data: {"id":"s1","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
                yield b'data: {"id":"s1","choices":[],"usage":{"prompt_tokens":2,"completion_tokens":1,"total_tokens":3}}\n\n'
                yield b"data: [DONE]\n\n"
            return StreamingResponse(gen(), media_type="text/event-stream")
        return JSONResponse({"id": "n1", "model": "mesocosm-3b",
                             "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"},
                                          "finish_reason": "stop"}],
                             "usage": {"prompt_tokens": 5, "completion_tokens": 4, "total_tokens": 9}})

    return up


@pytest.fixture
def gw(tmp_path):
    up = fake_upstream()
    settings = Settings(upstream="http://upstream", log_dir=tmp_path,
                        budgets={"agent-1": {"completion_tokens": 6}}, return_token_ids=True)
    app = create_app(settings, transport=httpx.ASGITransport(app=up))
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway")
    return app, up, client, tmp_path


def _records(tmp_path):
    return [json.loads(line) for line in (tmp_path / "gateway.jsonl").read_text().splitlines() if line]


async def test_non_streaming_request_is_logged_tagged_and_counted(gw):
    app, up, client, tmp_path = gw
    r = await client.post("/v1/chat/completions", headers={"X-Mesocosm-Agent": "agent-1", "X-Mesocosm-Run": "r1"},
                          json={"model": "mesocosm-3b", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200 and r.json()["choices"][0]["message"]["content"] == "hi"
    sent = up.state.requests[-1]
    assert sent["headers"]["x-mesocosm-request-id"]
    assert sent["body"]["return_token_ids"] is True
    rec = _records(tmp_path)[-1]
    assert rec["event"] == "chat_completion" and rec["agent"] == "agent-1" and rec["run"] == "r1"
    assert rec["request"]["messages"][0]["content"] == "hi"
    assert rec["response"]["usage"]["completion_tokens"] == 4
    assert rec["request_id"] == sent["headers"]["x-mesocosm-request-id"]
    assert app.state.gateway.usage_for("agent-1") == {"requests": 1, "prompt_tokens": 5, "completion_tokens": 4}


async def test_streaming_passes_bytes_through_and_collapses_the_log(gw):
    app, up, client, tmp_path = gw
    async with client.stream("POST", "/v1/chat/completions", headers={"X-Mesocosm-Agent": "agent-2"},
                             json={"model": "m", "stream": True, "messages": []}) as r:
        body = b"".join([chunk async for chunk in r.aiter_raw()])
    assert r.status_code == 200 and b"[DONE]" in body
    assert up.state.requests[-1]["body"]["stream_options"] == {"include_usage": True}
    rec = _records(tmp_path)[-1]
    assert rec["stream"] is True
    assert rec["response"]["content"] == "ok" and rec["response"]["token_ids"] == [7]
    assert rec["response"]["prompt_token_ids"] == [1, 2]
    assert rec["usage_total"]["completion_tokens"] == 1


async def test_upstream_error_body_is_kept_for_streaming_requests(gw):
    app, up, client, tmp_path = gw
    r = await client.post("/v1/chat/completions", headers={"X-Mesocosm-Agent": "agent-3"},
                          json={"model": "m", "stream": True, "max_tokens": 8192, "messages": []})
    assert r.status_code == 400
    rec = _records(tmp_path)[-1]
    assert rec["status"] == 400 and "maximum context length" in rec["response"]["raw"]


async def test_budget_exhaustion_is_explicit_and_recorded(gw):
    app, up, client, tmp_path = gw
    hdr = {"X-Mesocosm-Agent": "agent-1"}
    body = {"model": "m", "messages": [{"role": "user", "content": "x"}]}
    r1 = await client.post("/v1/chat/completions", headers=hdr, json=body)   # uses 4 of 6
    r2 = await client.post("/v1/chat/completions", headers=hdr, json=body)   # 8 >= 6 -> next call refused
    r3 = await client.post("/v1/chat/completions", headers=hdr, json=body)
    assert (r1.status_code, r2.status_code, r3.status_code) == (200, 200, 429)
    err = r3.json()["error"]
    assert err == {"message": "You exceeded your current quota, please check your plan and billing details.",
                   "type": "insufficient_quota", "code": "insufficient_quota", "param": "completion_tokens"}, \
        "the refusal looks like any OpenAI-compatible quota error; the log says what it was"
    assert len(up.state.requests) == 2, "the refused request never reached the upstream"
    last = _records(tmp_path)[-1]
    assert last["event"] == "budget_exhausted" and last["dimension"] == "completion_tokens"


async def test_budget_receipt_via_admin_endpoint(gw):
    app, up, client, tmp_path = gw
    admin = GatewayAdmin("http://gateway", transport=httpx.ASGITransport(app=app))
    receipt = await admin.set_budget("agent-9", {"completion_tokens": 100, "ignored": 5}, run_id="run_x")
    assert receipt.op == "conditions.set" and receipt.result == "applied"
    assert receipt.requested == {"condition": "budget", "agent_id": "agent-9", "value": {"completion_tokens": 100}}
    assert receipt.previous is None and receipt.enforced_at.component == "gateway"
    assert receipt.verification.ok
    second = await admin.set_budget("agent-9", {"completion_tokens": 50})
    assert second.previous == {"value": {"completion_tokens": 100}}
    assert app.state.gateway.budgets["agent-9"] == {"completion_tokens": 50}
    assert any(r.get("event") == "receipt" for r in _records(tmp_path))
    health = await admin.health()
    assert health["budget"]["agent-9"] == {"completion_tokens": 50}


async def test_passthrough_for_non_chat_paths(gw):
    app, up, client, tmp_path = gw
    r = await client.get("/v1/models")
    assert r.status_code == 200 and r.json()["data"][0]["id"] == "mesocosm-3b"
    assert _records(tmp_path)[-1]["event"] == "passthrough"


async def test_pause_holds_requests_until_resume_and_stop_refuses_them(gw):
    import asyncio

    app, up, client, tmp_path = gw
    admin = GatewayAdmin("http://gateway", transport=httpx.ASGITransport(app=app))
    hdr = {"X-Mesocosm-Agent": "agent-2", "X-Mesocosm-Run": "lbl"}
    body = {"model": "m", "messages": [{"role": "user", "content": "x"}]}
    paused = await admin.pause("lbl", reason="operator", run_id="run_p")
    assert paused.op == "run.pause" and paused.result == "applied" and paused.previous == {"paused": False}
    pending = asyncio.create_task(client.post("/v1/chat/completions", headers=hdr, json=body))
    await asyncio.sleep(0.15)
    assert not pending.done(), "the request waits at the gateway while the run is paused"
    health = await admin.health()
    assert health["paused"] == [{"run": "lbl", "agent_id": None, "since": health["paused"][0]["since"], "held": 1,
                                 "reason": "operator"}]
    other = await client.post("/v1/chat/completions", headers={"X-Mesocosm-Agent": "a", "X-Mesocosm-Run": "other"},
                              json=body)
    assert other.status_code == 200, "other runs are not affected"
    resumed = await admin.resume("lbl", run_id="run_p")
    assert resumed.op == "run.resume" and resumed.verification.detail["released"] == 1
    assert (await pending).status_code == 200
    held = [r for r in _records(tmp_path) if r["event"] == "held"]
    assert len(held) == 1 and held[0]["held_ms"] >= 100 and held[0]["run"] == "lbl"
    stopped = await admin.stop("lbl", run_id="run_p")
    assert stopped.op == "run.stop" and stopped.result == "applied"
    refused = await client.post("/v1/chat/completions", headers=hdr, json=body)
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "account_deactivated"
    assert "mesocosm" not in refused.text.lower()
    assert _records(tmp_path)[-1]["event"] == "run_stopped" and (await admin.health())["stopped"] == ["lbl"]
    assert [r["op"] for r in _records(tmp_path) if r["event"] == "receipt"] == ["run.pause", "run.resume", "run.stop"]


async def test_a_hold_that_outlives_the_limit_is_released_with_an_explicit_error(tmp_path):
    up = fake_upstream()
    app = create_app(Settings(upstream="http://upstream", log_dir=tmp_path, max_hold_seconds=0.2),
                     transport=httpx.ASGITransport(app=up))
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway")
    admin = GatewayAdmin("http://gateway", transport=httpx.ASGITransport(app=app))
    await admin.pause("lbl", agent_id="agent-1")
    hdr = {"X-Mesocosm-Agent": "agent-1", "X-Mesocosm-Run": "lbl"}
    body = {"model": "m", "messages": [{"role": "user", "content": "x"}]}
    r = await client.post("/v1/chat/completions", headers=hdr, json=body)
    assert r.status_code == 503 and r.json()["error"] == {"message": "The server is temporarily unable to handle "
                                                          "the request. Please try again later.",
                                                          "type": "server_error", "code": "service_unavailable"}
    assert _records(tmp_path)[-1]["event"] == "hold_expired"
    peer = await client.post("/v1/chat/completions", headers={"X-Mesocosm-Agent": "agent-2", "X-Mesocosm-Run": "lbl"},
                             json=body)
    assert peer.status_code == 200, "pausing one agent leaves the other running"
    stop = await admin.stop("lbl")
    assert stop.verification.detail == {"released_held_requests": 0} and (await admin.health())["paused"] == []


async def test_records_are_streamed_per_run_and_since(gw):
    app, up, client, tmp_path = gw
    admin = GatewayAdmin("http://gateway", transport=httpx.ASGITransport(app=app))
    for run, agent in (("r1", "agent-2"), ("r2", "agent-2"), ("r1", "agent-2")):
        await client.post("/v1/chat/completions", headers={"X-Mesocosm-Agent": agent, "X-Mesocosm-Run": run},
                          json={"model": "mesocosm-3b", "messages": [{"role": "user", "content": run}]})
    r1 = [rec async for rec in admin.records("r1")]
    assert [r["run"] for r in r1] == ["r1", "r1"] and all(r["event"] == "chat_completion" for r in r1)
    assert [rec["run"] async for rec in admin.records("r2")] == ["r2"]
    later = [rec async for rec in admin.records("r1", since=r1[-1]["ts"])]
    assert later == [r1[-1]], "`since` keeps only this run's records when a label is reused"
    assert [rec async for rec in admin.records("nope")] == []
    raw = await client.get("/admin/records", params={"run": "r1"}, headers={"Authorization": "Bearer mesocosm-local"})
    assert raw.headers["content-type"].startswith("application/x-ndjson") and raw.text.count("\n") == 2


async def test_agents_are_identified_by_their_own_key_and_the_admin_api_does_not_exist_for_them(tmp_path):
    up = fake_upstream()
    keys = {"sk-one": {"agent": "agent-1", "run": "lbl"}, "sk-two": {"agent": "agent-2", "run": "lbl"}}
    app = create_app(Settings(upstream="http://upstream", log_dir=tmp_path, control_key="ctl", agent_keys=keys,
                              budgets={"agent-2": {"requests": 1}}),
                     transport=httpx.ASGITransport(app=up))
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway")
    body = {"model": "m", "messages": [{"role": "user", "content": "x"}]}
    r = await client.post("/v1/chat/completions", headers={"Authorization": "Bearer sk-one"}, json=body)
    assert r.status_code == 200
    rec = _records(tmp_path)[-1]
    assert rec["agent"] == "agent-1" and rec["run"] == "lbl" and rec["identified_by"] == "key"
    sent = up.state.requests[-1]["headers"]
    assert "authorization" not in sent and "x-mesocosm-agent" not in sent, "the agent's key stays at the gateway"
    # a key cannot be overridden by headers, and the budget follows the key's agent
    r = await client.post("/v1/chat/completions", headers={"Authorization": "Bearer sk-two", "X-Mesocosm-Agent": "agent-1"},
                          json=body)
    assert r.status_code == 200 and _records(tmp_path)[-1]["agent"] == "agent-2"
    r = await client.post("/v1/chat/completions", headers={"Authorization": "Bearer sk-two"}, json=body)
    assert r.status_code == 429 and _records(tmp_path)[-1]["agent"] == "agent-2"
    # an unknown key is still proxied (the upstream decides) but recorded as nobody
    r = await client.post("/v1/chat/completions", headers={"Authorization": "Bearer sk-nope"}, json=body)
    assert _records(tmp_path)[-1]["agent"] == "unknown" and _records(tmp_path)[-1]["identified_by"] == "none"
    # the control surface: liveness says nothing, /admin needs the control key, probes are logged by identity
    assert (await client.get("/health")).json() == {"ok": True}
    for path in ("/admin/health", "/admin/usage", "/admin/records?run=lbl"):
        r = await client.get(path, headers={"Authorization": "Bearer sk-one"})
        assert r.status_code == 404 and r.json() == {"detail": "Not Found"}
    r = await client.post("/admin/budget", headers={"Authorization": "Bearer sk-one"},
                          json={"agent_id": "agent-1", "budget": {"requests": 10**6}})
    assert r.status_code == 404 and app.state.gateway.budgets.get("agent-1") is None, "an agent cannot raise its budget"
    denied = [r for r in _records(tmp_path) if r["event"] == "admin_denied"]
    assert len(denied) == 4 and all(d["agent"] == "agent-1" and d["identified_by"] == "key" for d in denied)
    assert denied[-1]["path"] == "/admin/budget"
    admin = GatewayAdmin("http://gateway", transport=httpx.ASGITransport(app=app), api_key="ctl")
    assert (await admin.health())["agents"] == ["agent-1", "agent-2"]
    wrong = GatewayAdmin("http://gateway", transport=httpx.ASGITransport(app=app), api_key="mesocosm-local")
    with pytest.raises(httpx.HTTPStatusError):
        await wrong.health()


async def test_the_gateway_authenticates_to_the_upstream_itself(tmp_path):
    up = fake_upstream()
    app = create_app(Settings(upstream="http://upstream", log_dir=tmp_path, upstream_key="vllm-secret",
                              agent_keys={"sk-one": {"agent": "agent-1", "run": "lbl"}}),
                     transport=httpx.ASGITransport(app=up))
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway")
    body = {"model": "m", "messages": [{"role": "user", "content": "x"}]}
    r = await client.post("/v1/chat/completions", headers={"Authorization": "Bearer sk-one"}, json=body)
    assert r.status_code == 200
    assert up.state.requests[-1]["headers"]["authorization"] == "Bearer vllm-secret", "the agent's key is replaced"
    r = await client.get("/v1/models")
    assert r.status_code == 200 and up.state.requests[-1]["headers"]["authorization"] == "Bearer vllm-secret"
