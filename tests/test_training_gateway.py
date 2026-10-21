"""Training gateway contracts against an explicit in-process upstream; no GPU acceptance claims."""

import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from mesocosm.gateway.app import Settings, create_app, parse_sse
from mesocosm.gateway.client import GatewayAdmin
from mesocosm.gateway.rollout import SAMPLING
from mesocosm.learning.config import PolicyIdentity, policy_id


def policy():
    identity = {"base_model": "test/model", "base_revision": "a" * 40, "base_sha256": "b" * 64,
                "adapter_sha256": None}
    return PolicyIdentity(id=policy_id(**identity), model="policy-0", **identity)


def upstream_response():
    return {"id": "completion", "model": "policy-0", "prompt_token_ids": [1, 2],
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "answer"},
                         "token_ids": [3, 4], "logprobs": {"content": [{"logprob": -0.3}, {"logprob": -0.7}]},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4}}


def training_gateway(tmp_path, *, entered=None, release=None):
    upstream = FastAPI()
    upstream.state.requests = []

    @upstream.post("/v1/chat/completions")
    async def chat(request: Request):
        body = await request.json()
        upstream.state.requests.append(body)
        if entered is not None:
            entered.set()
        if release is not None:
            await release.wait()
        response = upstream_response()
        if not body.get("stream"):
            return response

        async def chunks():
            for index, token in enumerate([3, 4]):
                chunk = {"model": "policy-0", "choices": [{"index": 0, "delta": {"content": "a"},
                         "token_ids": [token], "logprobs": {"content": [{"logprob": [-0.3, -0.7][index]}]}}]}
                if index == 0:
                    chunk["prompt_token_ids"] = [1, 2]
                yield f"data: {json.dumps(chunk)}\n\n"
            yield 'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
            yield f'data: {json.dumps({"choices": [], "usage": response["usage"]})}\n\n'
            yield "data: [DONE]\n\n"

        return StreamingResponse(chunks(), media_type="text/event-stream")

    settings = Settings(upstream="http://upstream", log_dir=tmp_path, require_rollout_binding=True,
                        agent_keys={f"key-{i}": {"agent": f"agent-{i}", "run": "world"} for i in range(1, 4)})
    app = create_app(settings, transport=httpx.ASGITransport(app=upstream))
    transport = httpx.ASGITransport(app=app)
    return app, upstream, transport, GatewayAdmin("http://gateway", transport=transport)


def records(path):
    return [json.loads(line) for line in (path / "gateway.jsonl").read_text().splitlines()]


def test_stream_error_is_retained_even_when_the_http_status_can_remain_200():
    response = parse_sse('data: {"error":{"message":"generation failed"}}\n\ndata: [DONE]\n')
    assert response["stream_complete"] is True
    assert response["stream_errors"] == [{"message": "generation failed"}]


async def bind(admin, agent="agent-1", **kwargs):
    return await admin.bind_rollout(agent, "world", "episode-1", "task", 1, f"session-{agent}", policy(),
                                    run_id="run-source", **kwargs)


async def test_binding_requires_control_identity_and_exact_registered_agent_run(tmp_path):
    app, up, transport, admin = training_gateway(tmp_path)
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway",
                                headers={"Authorization": "Bearer key-1"}) as agent:
        refused = await agent.post("/admin/rollout/bind", json={})
        assert refused.status_code == 404
        missing = await agent.post("/v1/chat/completions", headers={"X-Mesocosm-Agent": "agent-2"},
                                   json={"model": "policy-0", "messages": []})
        assert missing.status_code == 400 and not up.state.requests
    with pytest.raises(httpx.HTTPStatusError) as error:
        await admin.bind_rollout("agent-1", "wrong", "episode-1", "task", 1, "s", policy())
    assert error.value.response.status_code == 422
    receipt = await bind(admin)
    assert receipt.verification.detail["binding_id"] == receipt.enforced_at.event_id
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway",
                                headers={"Authorization": "Bearer mesocosm-local"}) as control:
        monitor = await control.post("/v1/chat/completions", headers={"X-Mesocosm-Agent": "monitor",
                                      "X-Mesocosm-Run": "world-monitor"}, json={"model": "policy-0", "messages": []})
        assert monitor.status_code == 200
    await admin.aclose()
    await app.state.gateway.client.aclose()


async def test_training_model_list_exposes_only_the_bound_policy_and_requires_identity(tmp_path):
    requests = []
    listing = {"data": [{"id": "old-policy", "root": "/secret/old-adapter"},
                        {"id": "policy-0", "root": "/secret/training/update-0001/adapter",
                         "parent": "base-policy", "permission": [{"id": "/secret/permission"}]}]}

    def upstream(request):
        requests.append(request)
        return httpx.Response(200, json=listing)

    app = create_app(Settings(upstream="http://upstream", log_dir=tmp_path, require_rollout_binding=True,
        agent_keys={f"key-{i}": {"agent": f"agent-{i}", "run": "world"} for i in range(1, 4)}),
        transport=httpx.MockTransport(upstream))
    transport = httpx.ASGITransport(app=app)
    admin = GatewayAdmin("http://gateway", transport=transport)
    await bind(admin)
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway") as client:
        for key, status in (("unknown", 401), ("key-2", 400)):
            response = await client.get("/v1/models", headers={"Authorization": f"Bearer {key}"})
            assert response.status_code == status
        assert not requests, "unknown or unbound training actors cannot discover upstream models"
        response = await client.get("/v1/models", headers={"Authorization": "Bearer key-1",
                                                           "X-Mesocosm-Agent": "agent-2"})
        assert response.status_code == 200
        assert response.json() == {"object": "list", "data": [
            {"id": "policy-0", "object": "model", "created": 0, "owned_by": "api"}]}
        assert "/secret" not in response.text and "old-policy" not in response.text
        listing["data"] = listing["data"][:1]
        absent = await client.get("/v1/models", headers={"Authorization": "Bearer key-1"})
        assert absent.status_code == 502 and "old-policy" not in absent.text and "/secret" not in absent.text
    await admin.aclose()
    await app.state.gateway.client.aclose()


async def test_model_listing_holds_its_binding_and_uses_the_bound_policy_upstream(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    requests = []

    async def upstream(request):
        requests.append(request)
        entered.set()
        await release.wait()
        return httpx.Response(200, json={"data": [{"id": "policy-0", "root": "/secret/adapter"}]})

    app = create_app(Settings(upstream="http://default", log_dir=tmp_path, require_rollout_binding=True,
        upstreams={"policy-0": {"url": "http://policy-server", "kind": "vllm"}},
        agent_keys={"key-1": {"agent": "agent-1", "run": "world"}}),
        transport=httpx.MockTransport(upstream))
    transport = httpx.ASGITransport(app=app)
    admin = GatewayAdmin("http://gateway", transport=transport)
    await bind(admin)
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway") as client:
        listing = asyncio.create_task(client.get("/v1/models", headers={"Authorization": "Bearer key-1"}))
        await asyncio.wait_for(entered.wait(), timeout=1)
        try:
            with pytest.raises(httpx.HTTPStatusError) as error:
                await admin.unbind_rollout("agent-1", "world", "episode-1", "session-agent-1")
            assert error.value.response.status_code == 409
        finally:
            release.set()
        response = await asyncio.wait_for(listing, timeout=1)
        assert response.status_code == 200 and requests[0].url.host == "policy-server"
        receipt = await admin.unbind_rollout("agent-1", "world", "episode-1", "session-agent-1")
        assert receipt.result == "applied"
    await admin.aclose()
    for upstream_client in app.state.gateway.clients.values():
        await upstream_client.aclose()


@pytest.mark.parametrize("stream", [False, True])
async def test_bound_request_records_actual_sampling_tokens_and_unspoofable_context(tmp_path, stream):
    app, up, transport, admin = training_gateway(tmp_path)
    bound = await bind(admin)
    app.state.gateway.budgets["agent-1"] = {"completion_tokens": 3}
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway",
                                headers={"Authorization": "Bearer key-1"}) as agent:
        result = await agent.post("/v1/chat/completions", headers={"X-Mesocosm-Agent": "agent-2",
                                  "X-Mesocosm-Run": "spoof"}, json={"model": "policy-0", "messages": [],
                                  "temperature": 0.2, "top_p": 0.5, "n": 9, "max_tokens": 100, "stream": stream})
        assert result.status_code == 200
    body = up.state.requests[-1]
    assert all(body[key] == value for key, value in SAMPLING.items()) and body["max_tokens"] == 3
    completed = next(record for record in records(tmp_path) if record["event"] == "chat_completion")
    assert completed["agent"] == "agent-1" and completed["run"] == "world"
    assert completed["rollout"] == bound.verification.detail
    assert completed["request"] == body
    assert completed["response"]["token_ids"] == [3, 4]
    assert completed["response"]["token_logprobs"] == [-0.3, -0.7]
    if stream:
        assert completed["response"]["stream_complete"] is True
    await admin.unbind_rollout("agent-1", "world", "episode-1", "session-agent-1", run_id="run-source")
    assert not app.state.gateway.rollout_bindings
    await admin.aclose()
    await app.state.gateway.client.aclose()


@pytest.mark.parametrize("extra", [{"logit_bias": {}}, {"allowed_token_ids": [1]}, {"bad_words": ["x"]},
                                  {"structured_outputs": {}}, {"guided_json": {}}, {"min_tokens": 1},
                                  {"logits_processors": []}, {"future_sampler": 1}, {"tool_choice": "required"},
                                  {"tool_choice": "none"}, {"rollout": {}}, {"model": "another-policy"}])
async def test_training_rejects_distribution_overrides_and_boundary_spoofs(tmp_path, extra):
    app, up, transport, admin = training_gateway(tmp_path)
    await bind(admin)
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway",
                                headers={"Authorization": "Bearer key-1"}) as agent:
        result = await agent.post("/v1/chat/completions", json={"model": "policy-0", "messages": [], **extra})
    assert result.status_code == 400 and not up.state.requests
    await admin.aclose()
    await app.state.gateway.client.aclose()


async def test_active_request_prevents_boundary_change_and_other_agents_keep_independent_leases(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    app, up, transport, admin = training_gateway(tmp_path, entered=entered, release=release)
    await bind(admin)
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway",
                                headers={"Authorization": "Bearer key-1"}) as agent:
        request = asyncio.create_task(agent.post("/v1/chat/completions", json={"model": "policy-0", "messages": []}))
        await asyncio.wait_for(entered.wait(), 1)
        with pytest.raises(httpx.HTTPStatusError) as error:
            await admin.unbind_rollout("agent-1", "world", "episode-1", "session-agent-1")
        assert error.value.response.status_code == 409
        await bind(admin, "agent-2")
        assert len(app.state.gateway.rollout_bindings) == 2
        release.set()
        assert (await request).status_code == 200
    await admin.aclose()
    await app.state.gateway.client.aclose()


async def test_non_vllm_binding_and_replayed_boundary_are_rejected(tmp_path):
    app, up, transport, admin = training_gateway(tmp_path)
    app.state.gateway.settings.upstreams["policy-0"] = {"url": "http://upstream", "kind": "openai"}
    with pytest.raises(httpx.HTTPStatusError) as error:
        await bind(admin)
    assert error.value.response.status_code == 422
    app.state.gateway.settings.upstreams.clear()
    await bind(admin)
    with pytest.raises(httpx.HTTPStatusError) as error:
        await bind(admin)
    assert error.value.response.status_code == 409
    await admin.aclose()
    await app.state.gateway.client.aclose()
