"""Identity-only gateway contracts; no live serving or behavioral acceptance claims."""

from __future__ import annotations

import json

import httpx
import pytest

from mesocosm.gateway.app import Settings, create_app, parse_sse

IDENTITY = {
    "deployment_id": "deployment-" + "a" * 64,
    "policy_id": "b" * 64,
    "base_sha256": "c" * 64,
    "adapter_sha256": "d" * 64,
}


@pytest.mark.parametrize("response_model,expected", [("bound", "matched"), ("wrong", "mismatched"), (None, "missing")])
async def test_bound_gateway_preserves_sampling_and_records_actual_response(tmp_path, response_model, expected):
    sent = []

    def upstream(request):
        sent.append(request)
        return httpx.Response(
            200,
            json={
                "id": "upstream-response",
                "model": response_model,
                "choices": [{"message": {"content": "reply"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    app = create_app(
        Settings(
            upstream="http://upstream",
            log_dir=tmp_path,
            upstream_key="server-secret",
            agent_keys={"agent-key": {"agent": "a", "run": "r"}},
            agent_models={"a": "bound"},
            upstreams={"bound": {"serving_identity": IDENTITY}},
        ),
        transport=httpx.MockTransport(upstream),
    )
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://gateway") as client:
            body = {"model": "bound", "messages": [], "temperature": 0.37, "seed": 19, "stream": False}
            response = await client.post(
                "/v1/chat/completions", json=body, headers={"Authorization": "Bearer agent-key"}
            )
            assert response.status_code == (200 if expected == "matched" else 502)
            assert json.loads(sent[0].content) == body
            assert sent[0].headers["authorization"] == "Bearer server-secret"
        records = [json.loads(line) for line in (tmp_path / "gateway.jsonl").read_text().splitlines()]
        admitted = next(row for row in records if row["event"] == "request_admitted")
        completed = next(row for row in records if row["event"] == "chat_completion")
        assert admitted["serving_identity"] == completed["serving_identity"] == IDENTITY
        assert completed["model_identity_check"] == expected
    finally:
        await app.state.gateway.client.aclose()


@pytest.mark.parametrize("requested", ["other", None])
async def test_agent_cannot_bypass_assignment_via_default_route(tmp_path, requested):
    def upstream(request):
        pytest.fail("wrong alias reached upstream")

    app = create_app(
        Settings(
            log_dir=tmp_path,
            agent_keys={"key": {"agent": "a", "run": "r"}},
            agent_models={"a": "bound"},
            upstreams={"bound": {"serving_identity": IDENTITY}},
        ),
        transport=httpx.MockTransport(upstream),
    )
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://gateway") as client:
            body = {"messages": []}
            if requested:
                body["model"] = requested
            response = await client.post("/v1/chat/completions", json=body, headers={"Authorization": "Bearer key"})
            assert response.status_code == 400
    finally:
        await app.state.gateway.client.aclose()


def test_later_stream_chunk_cannot_hide_conflicting_alias():
    chunks = [{"model": "bound", "choices": []}, {"model": "wrong", "choices": []}]
    response = parse_sse("".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n")
    assert response["model"] == "bound"
    assert response["stream_errors"] == ["conflicting_model_ids"]


def test_binding_requires_operator_agent_key_and_configured_policy():
    with pytest.raises(ValueError, match="configured key"):
        Settings(agent_models={"a": "unconfigured"})


async def test_bound_model_listing_uses_deployment_server_and_hides_other_aliases(tmp_path):
    def upstream(request):
        assert request.url.host == "deployment-server"
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "bound", "root": "/private/adapter", "parent": "base"},
                    {"id": "unassigned", "root": "/private/other"},
                ]
            },
        )

    app = create_app(
        Settings(
            upstream="http://default-server",
            log_dir=tmp_path,
            agent_keys={"key": {"agent": "a", "run": "r"}},
            agent_models={"a": "bound"},
            upstreams={"bound": {"url": "http://deployment-server/", "serving_identity": IDENTITY}},
        ),
        transport=httpx.MockTransport(upstream),
    )
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://gateway") as client:
            response = await client.get("/v1/models", headers={"Authorization": "Bearer key"})
            assert response.status_code == 200
            assert response.json() == {
                "object": "list",
                "data": [{"id": "bound", "object": "model", "created": 0, "owned_by": "api"}],
            }
    finally:
        for client in [app.state.gateway.client, *app.state.gateway.clients.values()]:
            await client.aclose()
