"""Real gateway admission with protocol fixtures; no agent behavior is simulated as acceptance."""
from __future__ import annotations

import asyncio

import httpx
import pytest

from mesocosm.gateway.app import Settings, create_app
from mesocosm.gateway.client import GatewayAdmin


def upstream_response(usage=True):
    body = {"choices": [{"message": {"content": "done"}, "finish_reason": "stop"}]}
    if usage:
        body["usage"] = {"prompt_tokens": 3, "completion_tokens": 2}
    return httpx.Response(200, json=body)


async def test_terminal_receipt_requires_an_actual_refusal_and_is_admin_only(tmp_path):
    settings = Settings(log_dir=tmp_path, budgets={"a": {"requests": 1}},
                        agent_keys={"key": {"agent": "a", "run": "r"}})
    app = create_app(settings, httpx.MockTransport(lambda _: upstream_response()))
    admin = GatewayAdmin("http://gateway", httpx.ASGITransport(app))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://gateway") as client:
        before = await admin.terminal_refusal("r", "a")
        assert before == {"cursor": 0, "refusal": None}
        headers = {"Authorization": "Bearer key"}
        request = {"model": "m", "messages": []}
        assert (await client.post("/v1/chat/completions", headers=headers, json=request)).status_code == 200
        assert await admin.terminal_refusal("r", "a") == before, "last admitted call must finish normally"
        denied = await client.post("/v1/chat/completions", headers=headers, json=request)
        assert denied.status_code == 429 and denied.json()["error"]["code"] == "insufficient_quota"
        snapshot = await admin.terminal_refusal("r", "a", after=before["cursor"])
        receipt = snapshot["refusal"]
        assert receipt["dimension"] == "requests" and receipt["reason"] == "quota_exhausted"
        assert receipt["sequence"] == snapshot["cursor"] == 1
        assert receipt["run"] == "r" and receipt["agent"] == "a" and receipt["event_id"]
        assert "request" not in receipt and "messages" not in receipt
        assert (await admin.terminal_refusal("r", "a", after=snapshot["cursor"]))["refusal"] is None
        assert (await admin.terminal_refusal("other", "a"))["refusal"] is None
        assert (await admin.terminal_refusal("r", "other"))["refusal"] is None
        assert (await client.get("/admin/terminal-refusal", params={"run": "r", "agent_id": "a"},
                                 headers=headers)).status_code == 404
    await admin.aclose()
    await app.state.gateway.client.aclose()

    restarted = create_app(settings, httpx.MockTransport(lambda _: upstream_response()))
    admin = GatewayAdmin("http://gateway", httpx.ASGITransport(restarted))
    assert await admin.terminal_refusal("r", "a") == snapshot
    with pytest.raises(httpx.HTTPStatusError, match="409"):
        await admin.terminal_refusal("r", "a", after=snapshot["cursor"] + 1)
    await admin.aclose()
    await restarted.state.gateway.client.aclose()


@pytest.mark.parametrize("limits,dimension", [({"concurrency": 1}, "team_concurrency"),
                                             ({"completion_tokens": 2}, "team_completion_tokens")])
async def test_temporary_team_reservations_do_not_terminate_an_agent(tmp_path, limits, dimension):
    entered, release = asyncio.Event(), asyncio.Event()

    async def respond(_):
        entered.set()
        await release.wait()
        return upstream_response()

    app = create_app(Settings(log_dir=tmp_path, team_resources=limits,
                             agent_keys={f"key{i}": {"agent": f"a{i}", "run": "r"} for i in range(2)}),
                     httpx.MockTransport(respond))
    admin = GatewayAdmin("http://gateway", httpx.ASGITransport(app))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://gateway") as client:
        async def ask(i):
            return await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer key{i}"},
                                     json={"model": "m", "messages": [], "max_tokens": 2})
        first = asyncio.create_task(ask(0))
        await entered.wait()
        try:
            assert (await ask(1)).json()["error"]["param"] == dimension
            assert (await admin.terminal_refusal("r", "a1"))["refusal"] is None
        finally:
            release.set()
            await first
        if "completion_tokens" in limits:
            assert (await ask(1)).status_code == 429
            receipt = (await admin.terminal_refusal("r", "a1"))["refusal"]
            assert receipt["dimension"] == dimension and receipt["reason"] == "quota_exhausted"
        else:
            assert (await ask(1)).status_code == 200
            assert (await admin.terminal_refusal("r", "a1"))["refusal"] is None
    await admin.aclose()
    await app.state.gateway.client.aclose()


async def test_unknown_usage_is_distinct_from_spent_token_quota(tmp_path):
    app = create_app(Settings(log_dir=tmp_path, budgets={"a": {"completion_tokens": 100}},
                             agent_keys={"key": {"agent": "a", "run": "r"}}),
                     httpx.MockTransport(lambda _: upstream_response(usage=False)))
    admin = GatewayAdmin("http://gateway", httpx.ASGITransport(app))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://gateway") as client:
        async def ask():
            return await client.post("/v1/chat/completions", headers={"Authorization": "Bearer key"},
                                     json={"model": "m", "messages": []})
        assert (await ask()).status_code == 200
        assert (await ask()).status_code == 429
        receipt = (await admin.terminal_refusal("r", "a"))["refusal"]
        assert receipt["reason"] == "usage_unknown"
        assert app.state.gateway.usage["a"]["completion_tokens"] == 0
    await admin.aclose()
    await app.state.gateway.client.aclose()


@pytest.mark.parametrize("team", [False, True])
async def test_completion_count_cannot_fit_remaining_budget_is_terminal(tmp_path, team):
    calls = []

    def respond(request):
        calls.append(request)
        return upstream_response()

    app = create_app(Settings(log_dir=tmp_path,
                             budgets={} if team else {"a": {"completion_tokens": 1}},
                             team_resources={"completion_tokens": 1} if team else {},
                             agent_keys={"key": {"agent": "a", "run": "r"}}), httpx.MockTransport(respond))
    admin = GatewayAdmin("http://gateway", httpx.ASGITransport(app))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://gateway") as client:
        response = await client.post("/v1/chat/completions", headers={"Authorization": "Bearer key"},
                                     json={"model": "m", "messages": [], "n": 2})
        assert response.status_code == 429 and not calls
        receipt = (await admin.terminal_refusal("r", "a"))["refusal"]
        assert receipt["reason"] == "request_exceeds_remaining_budget"
        assert receipt["dimension"] == ("team_completion_tokens" if team else "completion_tokens")
    await admin.aclose()
    await app.state.gateway.client.aclose()
