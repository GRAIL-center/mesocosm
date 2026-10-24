import asyncio
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
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway",
                              headers={"Authorization": "Bearer mesocosm-local"})
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


@pytest.mark.parametrize("method", ["GET", "HEAD"])
async def test_model_list_omits_private_metadata_and_upstream_headers(tmp_path, method):
    requests = []

    def upstream(request):
        requests.append(request)
        return httpx.Response(200, json={"object": "list", "private": "/secret/controller",
            "data": [{"id": "base", "object": "model", "created": 123, "owned_by": "/secret/owner",
                      "root": "/secret/model", "parent": "private-policy", "permission": [{"secret": True}]},
                     {"id": "adapter", "root": "/secret/training/update-0001/adapter"}]},
            headers={"x-private-path": "/secret/controller", "location": "/secret/model"})

    app = create_app(Settings(upstream="http://upstream", log_dir=tmp_path,
                              agent_keys={"actor": {"agent": "agent-1", "run": "world"}}),
                     transport=httpx.MockTransport(upstream))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway") as client:
        response = await client.request(method, "/v1/models", headers={"Authorization": "Bearer actor"})
    assert response.status_code == 200 and requests[0].method == "GET"
    assert "/secret" not in str(response.headers) and "x-private-path" not in response.headers
    if method == "HEAD":
        assert response.content == b""
    else:
        assert response.json() == {"object": "list", "data": [
            {"id": "base", "object": "model", "created": 123, "owned_by": "api"},
            {"id": "adapter", "object": "model", "created": 0, "owned_by": "api"}]}
    await app.state.gateway.client.aclose()


@pytest.mark.parametrize(("status", "payload"), [
    (400, {"error": {"message": "/secret/training/adapter failed"}}),
    (302, {"detail": "/secret/model"}),
    (200, {"data": "private-metadata"}),
    (200, {"data": [{"id": {"private": "/secret/model"}}]}),
    (200, {"data": [{"id": "base"}, {"id": "base"}]}),
])
@pytest.mark.parametrize("method", ["GET", "HEAD"])
async def test_model_list_errors_fail_without_upstream_details(tmp_path, status, payload, method):
    app = create_app(Settings(upstream="http://upstream", log_dir=tmp_path),
        transport=httpx.MockTransport(lambda request: httpx.Response(status, json=payload,
                                                                    headers={"location": "/secret/model"})))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway") as client:
        response = await client.request(method, "/v1/models",
                                        headers={"Authorization": "Bearer mesocosm-local"})
    assert response.status_code == 502
    assert "/secret" not in response.text and "private-metadata" not in response.text
    assert "location" not in response.headers
    if method == "HEAD":
        assert response.content == b""
    else:
        assert response.json()["error"]["code"] == "upstream_error"
    await app.state.gateway.client.aclose()


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
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway",
                              headers={"Authorization": "Bearer mesocosm-local"})
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
    assert [r["run"] for r in r1] == ["r1"] * 4
    assert [r["event"] for r in r1] == ["request_admitted", "chat_completion"] * 2
    assert r1[0]["request_id"] == r1[1]["request_id"] and r1[2]["request_id"] == r1[3]["request_id"]
    assert [rec["run"] async for rec in admin.records("r2")] == ["r2"] * 2
    later = [rec async for rec in admin.records("r1", since=r1[-1]["ts"])]
    assert later == r1[-2:], "`since` keeps only this run's admission and completion when a label is reused"
    assert [rec async for rec in admin.records("nope")] == []
    raw = await client.get("/admin/records", params={"run": "r1"}, headers={"Authorization": "Bearer mesocosm-local"})
    assert raw.headers["content-type"].startswith("application/x-ndjson") and raw.text.count("\n") == 4


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
    # an unknown key cannot fall back to an unbudgeted identity
    before = len(up.state.requests)
    r = await client.post("/v1/chat/completions", headers={"Authorization": "Bearer sk-nope"}, json=body)
    assert r.status_code == 401 and len(up.state.requests) == before
    assert _records(tmp_path)[-1]["event"] == "auth_denied"
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
    r = await client.get("/v1/models", headers={"Authorization": "Bearer sk-one"})
    assert r.status_code == 200 and up.state.requests[-1]["headers"]["authorization"] == "Bearer vllm-secret"


async def test_requests_are_routed_to_the_upstream_serving_the_model(tmp_path, monkeypatch):
    """One gateway, several upstreams (ADR-0029): the model id names the server; keys come from the
    gateway's environment; token ids are requested from vLLM only."""
    up = fake_upstream()
    monkeypatch.setenv("BASELINE_KEY", "sk-baseline")
    settings = Settings(upstream="http://vllm", log_dir=tmp_path, upstream_key="vllm-secret", return_token_ids=True,
                        agent_keys={"sk-one": {"agent": "agent-1", "run": "lbl"}},
                        upstreams={"baseline-x": {"url": "http://api.example", "key_env": "BASELINE_KEY",
                                                  "kind": "openai"},
                                   "local-b": {"url": "http://vllm-b", "kind": "vllm"}})
    app = create_app(settings, transport=httpx.ASGITransport(app=up))
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway")
    hdr = {"Authorization": "Bearer sk-one"}
    r = await client.post("/v1/chat/completions", headers=hdr, json={"model": "baseline-x", "messages": []})
    assert r.status_code == 200
    sent = up.state.requests[-1]
    assert sent["headers"]["host"] == "api.example" and sent["headers"]["authorization"] == "Bearer sk-baseline"
    assert "return_token_ids" not in sent["body"], "a commercial API does not know the field"
    rec = _records(tmp_path)[-1]
    assert rec["upstream"] == "http://api.example" and rec["model_kind"] == "openai" and rec["agent"] == "agent-1"
    assert sent["headers"]["host"] == "api.example" and up.state.requests[-1]["body"] == {"model": "baseline-x",
                                                                                           "messages": []}
    r = await client.post("/v1/chat/completions", headers=hdr, json={"model": "local-b", "messages": []})
    sent = up.state.requests[-1]
    assert sent["headers"]["host"] == "vllm-b" and "authorization" not in sent["headers"], "no key named, none sent"
    assert sent["body"]["return_token_ids"] is True and _records(tmp_path)[-1]["model_kind"] == "vllm"
    r = await client.post("/v1/chat/completions", headers=hdr, json={"model": "qwen3-4b", "messages": []})
    sent = up.state.requests[-1]
    assert sent["headers"]["host"] == "vllm" and sent["headers"]["authorization"] == "Bearer vllm-secret", \
        "a model the map does not name goes to the default inference server"
    assert _records(tmp_path)[-1]["upstream"] == "http://vllm"
    assert app.state.gateway.usage_for("agent-1")["requests"] == 3, "one budget whatever the upstream"
    admin = GatewayAdmin("http://gateway", transport=httpx.ASGITransport(app=app), api_key="mesocosm-local")
    health = await admin.health()
    assert health["upstreams"] == {"baseline-x": {"url": "http://api.example", "kind": "openai", "key": True},
                                   "local-b": {"url": "http://vllm-b", "kind": "vllm", "key": False}}
    assert "sk-baseline" not in json.dumps(health), "the key itself never leaves the environment"


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"},
                                      {"X-Mesocosm-Agent": "fresh", "X-Mesocosm-Run": "other"},
                                      {"Authorization": "Bearer wrong", "X-Mesocosm-Agent": "fresh"}])
async def test_inference_requires_an_agent_key_or_authenticated_control_identity(tmp_path, headers):
    up = fake_upstream()
    app = create_app(Settings(upstream="http://upstream", log_dir=tmp_path,
                              agent_keys={"sk-one": {"agent": "agent-1", "run": "lbl"}}),
                     transport=httpx.ASGITransport(app=up))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway") as client:
        app.state.gateway.stop("lbl")
        response = await client.post("/v1/chat/completions", headers=headers, json={"model": "m", "messages": []})
    assert response.status_code == 401 and response.json()["error"]["code"] == "invalid_api_key"
    assert not up.state.requests and app.state.gateway.usage == {}
    assert _records(tmp_path)[-1]["event"] == "auth_denied"


@pytest.mark.parametrize("path", ["/v1/completions", "/v1/responses", "/v1/chat/completions/batch",
                                    "/sleep", "/start_profile", "/v1/load_lora_adapter"])
async def test_unobserved_upstream_endpoints_are_refused_even_with_a_valid_key(tmp_path, path):
    calls = []

    async def upstream(request):
        calls.append(request)
        return httpx.Response(200, json={"choices": [{"text": "unobserved"}]})

    app = create_app(Settings(upstream="http://upstream", log_dir=tmp_path,
                              agent_keys={"sk-one": {"agent": "agent-1", "run": "lbl"}}),
                     transport=httpx.MockTransport(upstream))
    app.state.gateway.stop("lbl")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway") as client:
        response = await client.post(path, headers={"Authorization": "Bearer sk-one"},
                                     json={"model": "m", "prompt": "do not drop this exposure"})
    assert response.status_code == 404 and not calls
    assert _records(tmp_path)[-1]["event"] == "endpoint_denied"


@pytest.mark.parametrize("stream", [False, True])
async def test_three_agents_run_concurrently_but_each_request_budget_is_atomic(tmp_path, stream):
    reached = set()
    ready = asyncio.Event()
    up = FastAPI()

    @up.post("/v1/chat/completions")
    async def chat(request: Request):
        body = await request.json()
        reached.add(body["model"])
        if len(reached) == 3:
            ready.set()
        await asyncio.wait_for(ready.wait(), timeout=2)
        usage = {"prompt_tokens": 2, "completion_tokens": 1}
        if stream:
            async def chunks():
                yield f'data: {json.dumps({"choices": [], "usage": usage})}\n\n'.encode()
                yield b'data: [DONE]\n\n'
            return StreamingResponse(chunks(), media_type="text/event-stream")
        return {"choices": [{"message": {"content": "ok"}}], "usage": usage}

    keys = {f"sk-{i}": {"agent": f"agent-{i}", "run": "lbl"} for i in range(3)}
    app = create_app(Settings(upstream="http://upstream", log_dir=tmp_path, agent_keys=keys,
                              budgets={f"agent-{i}": {"requests": 1} for i in range(3)}),
                     transport=httpx.ASGITransport(app=up))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway") as client:
        responses = await asyncio.gather(*[
            client.post("/v1/chat/completions", headers={"Authorization": f"Bearer sk-{i}"},
                        json={"model": f"model-{i}", "messages": [], "stream": stream})
            for i in range(3) for _ in range(3)
        ])
    assert sorted(r.status_code for r in responses) == [200] * 3 + [429] * 6
    assert reached == {f"model-{i}" for i in range(3)}
    assert all(app.state.gateway.usage_for(f"agent-{i}")["requests"] == 1 for i in range(3))


async def test_concurrent_requests_share_the_remaining_completion_allowance(tmp_path):
    limits = []
    up = FastAPI()

    @up.post("/v1/chat/completions")
    async def chat(request: Request):
        body = await request.json()
        limit = body.get("max_tokens", body.get("max_completion_tokens"))
        limits.append(limit)
        await asyncio.sleep(0.03)
        return {"choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 2, "completion_tokens": limit}}

    app = create_app(Settings(upstream="http://upstream", log_dir=tmp_path,
                              agent_keys={"sk-one": {"agent": "agent-1", "run": "lbl"}},
                              budgets={"agent-1": {"completion_tokens": 3}}),
                     transport=httpx.ASGITransport(app=up))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway") as client:
        responses = await asyncio.gather(*[
            client.post("/v1/chat/completions", headers={"Authorization": "Bearer sk-one"},
                        json={"model": "m", "messages": [], "max_completion_tokens": 2}) for _ in range(3)
        ])
    assert [r.status_code for r in responses] == [200, 200, 429]
    assert limits == [2, 1] and app.state.gateway.usage_for("agent-1")["completion_tokens"] == 3


async def test_all_active_holds_are_observed_after_an_agent_is_resumed(gw):
    app, up, client, _ = gw
    gateway = app.state.gateway
    gateway.pause("lbl", "agent-1", "agent hold")
    pending = asyncio.create_task(client.post("/v1/chat/completions",
                                              headers={"X-Mesocosm-Agent": "agent-1", "X-Mesocosm-Run": "lbl"},
                                              json={"model": "m", "messages": []}))
    await asyncio.sleep(0.03)
    gateway.pause("lbl", None, "run hold")
    gateway.resume("lbl", "agent-1")
    await asyncio.sleep(0.03)
    assert not pending.done() and not up.state.requests
    gateway.resume("lbl", None)
    assert (await pending).status_code == 200


async def test_failed_upstream_attempt_is_reserved_and_releases_the_agent_lock(tmp_path):
    async def unreachable(request):
        raise httpx.ConnectError("offline", request=request)

    app = create_app(Settings(upstream="http://upstream", log_dir=tmp_path,
                              agent_keys={"sk-one": {"agent": "agent-1", "run": "lbl"}},
                              budgets={"agent-1": {"requests": 1}}), transport=httpx.MockTransport(unreachable))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway") as client:
        async def call():
            return await client.post("/v1/chat/completions", headers={"Authorization": "Bearer sk-one"},
                                     json={"model": "m", "messages": []})
        first = await call()
        second = await asyncio.wait_for(call(), 1)
    assert (first.status_code, second.status_code) == (502, 429)
    assert app.state.gateway.usage_for("agent-1")["requests"] == 1


async def test_disconnected_stream_keeps_partial_exposure_and_fails_closed_without_usage(tmp_path):
    class Interrupted(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
            await asyncio.Event().wait()

        async def aclose(self):
            await asyncio.sleep(0)

    async def upstream(request):
        return httpx.Response(200, stream=Interrupted(), headers={"content-type": "text/event-stream"})

    app = create_app(Settings(upstream="http://upstream", log_dir=tmp_path,
                              agent_keys={"sk-one": {"agent": "agent-1", "run": "lbl"}},
                              budgets={"agent-1": {"completion_tokens": 10}}),
                     transport=httpx.MockTransport(upstream))
    disconnected = asyncio.Event()
    sent_body = False

    async def receive():
        nonlocal sent_body
        if not sent_body:
            sent_body = True
            return {"type": "http.request", "body": json.dumps({"model": "m", "messages": [], "stream": True}).encode(),
                    "more_body": False}
        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body" and b"partial" in message.get("body", b""):
            disconnected.set()

    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
             "method": "POST", "path": "/v1/chat/completions", "raw_path": b"/v1/chat/completions",
             "query_string": b"", "scheme": "http", "root_path": "", "server": ("gateway", 80),
             "client": ("127.0.0.1", 1234),
             "headers": [(b"authorization", b"Bearer sk-one"), (b"content-type", b"application/json")]}
    await asyncio.wait_for(app(scope, receive, send), 2)
    record = _records(tmp_path)[-1]
    assert record["event"] == "chat_completion" and record["response"]["content"] == "partial"
    assert record["response"]["usage"] is None
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway") as client:
        response = await asyncio.wait_for(client.post("/v1/chat/completions",
                                                      headers={"Authorization": "Bearer sk-one"},
                                                      json={"model": "m", "messages": []}), 1)
    assert response.status_code == 429 and app.state.gateway.usage_for("agent-1")["requests"] == 1
