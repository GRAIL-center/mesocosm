"""Restart tests exercise gateway accounting with HTTP fixtures, not agent acceptance."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from mesocosm.gateway.app import Settings, create_app
from mesocosm.gateway.client import GatewayAdmin


def response(usage=True):
    data = {"choices": [{"message": {"content": "done"}, "finish_reason": "stop"}]}
    if usage:
        data["usage"] = {"prompt_tokens": 3, "completion_tokens": 2}
    return httpx.Response(200, json=data)


async def ask(app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://gateway") as client:
        return await client.post("/v1/chat/completions", headers={"Authorization": "Bearer key"},
                                 json={"model": "m", "messages": []})


def settings(tmp_path, budget=None, **kwargs):
    return Settings(log_dir=tmp_path, budgets={"a": budget or {}},
                    agent_keys={"key": {"agent": "a", "run": "r"}}, **kwargs)


async def test_completed_request_at_cap_stays_spent_across_repeated_restarts(tmp_path):
    calls = 0

    def upstream(_):
        nonlocal calls
        calls += 1
        return response()

    config = settings(tmp_path, {"requests": 1})
    app = create_app(config, httpx.MockTransport(upstream))
    assert (await ask(app)).status_code == 200
    expected = {"requests": 1, "prompt_tokens": 3, "completion_tokens": 2}
    await app.state.gateway.client.aclose()
    for _ in range(2):
        app = create_app(config, httpx.MockTransport(upstream))
        assert app.state.gateway.usage_for("a") == expected
        assert (await ask(app)).status_code == 429
        await app.state.gateway.client.aclose()
    assert calls == 1


async def test_admin_applied_budget_survives_restart_when_settings_have_no_limit(tmp_path):
    config = settings(tmp_path)
    app = create_app(config, httpx.MockTransport(lambda _: response()))
    admin = GatewayAdmin("http://gateway", httpx.ASGITransport(app))
    await admin.set_budget("a", {"requests": 1})
    assert (await ask(app)).status_code == 200
    await admin.aclose()
    await app.state.gateway.client.aclose()
    restarted = create_app(config, httpx.MockTransport(lambda _: response()))
    assert restarted.state.gateway.budgets["a"] == {"requests": 1}
    assert (await ask(restarted)).status_code == 429
    await restarted.state.gateway.client.aclose()


@pytest.mark.parametrize("budget,dimension", [({"requests": 1}, "requests"),
                                             ({"completion_tokens": 100}, "completion_tokens")])
async def test_inflight_admission_is_persisted_before_upstream_and_recovers_unknown_usage(
    tmp_path, budget, dimension,
):
    entered = asyncio.Event()

    async def upstream(_):
        records = [json.loads(line) for line in (tmp_path / "gateway.jsonl").read_text().splitlines()]
        assert records[-1]["event"] == "request_admitted"
        assert records[-1]["usage_total"]["requests"] == 1
        entered.set()
        await asyncio.Event().wait()

    config = settings(tmp_path, budget)
    first = create_app(config, httpx.MockTransport(upstream))
    pending = asyncio.create_task(ask(first))
    await asyncio.wait_for(entered.wait(), 1)
    try:
        restarted = create_app(config, httpx.MockTransport(lambda _: pytest.fail("unknown admission must not be retried")))
        assert restarted.state.gateway.usage_for("a")["requests"] == 1
        assert "a" in restarted.state.gateway.missing_usage
        denied = await ask(restarted)
        assert denied.status_code == 429 and denied.json()["error"]["param"] == dimension
        await restarted.state.gateway.client.aclose()
    finally:
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        await first.state.gateway.client.aclose()


async def test_missing_usage_remains_unknown_after_later_measured_response_and_restart(tmp_path):
    calls = 0

    def upstream(_):
        nonlocal calls
        calls += 1
        return response(usage=calls > 1)

    config = settings(tmp_path, {"requests": 3})
    first = create_app(config, httpx.MockTransport(upstream))
    assert (await ask(first)).status_code == (await ask(first)).status_code == 200
    await first.state.gateway.client.aclose()
    restarted = create_app(config, httpx.MockTransport(upstream))
    assert restarted.state.gateway.usage_for("a") == {"requests": 2, "prompt_tokens": 3, "completion_tokens": 2}
    assert "a" in restarted.state.gateway.missing_usage
    admin = GatewayAdmin("http://gateway", httpx.ASGITransport(restarted))
    await admin.set_budget("a", {"completion_tokens": 100})
    assert (await ask(restarted)).status_code == 429 and calls == 2
    await admin.aclose()
    await restarted.state.gateway.client.aclose()


async def test_failed_admission_counts_once_and_missing_usage_survives_restart(tmp_path):
    def upstream(_):
        raise httpx.ConnectError("Connection lost after dispatch")

    config = settings(tmp_path, {"requests": 2, "completion_tokens": 100})
    first = create_app(config, httpx.MockTransport(upstream))
    assert (await ask(first)).status_code == 502
    await first.state.gateway.client.aclose()
    restarted = create_app(config, httpx.MockTransport(lambda _: response()))
    assert restarted.state.gateway.usage_for("a") == {"requests": 1, "prompt_tokens": 0, "completion_tokens": 0}
    assert "a" in restarted.state.gateway.missing_usage
    assert (await ask(restarted)).status_code == 429
    await restarted.state.gateway.client.aclose()


async def test_team_and_agent_admissions_do_not_double_count_request_after_restart(tmp_path):
    config = settings(tmp_path, {"requests": 1}, team_resources={"requests": 3})
    first = create_app(config, httpx.MockTransport(lambda _: response()))
    assert (await ask(first)).status_code == 200
    await first.state.gateway.client.aclose()
    restarted = create_app(config, httpx.MockTransport(lambda _: response()))
    assert restarted.state.gateway.usage_for("a")["requests"] == 1
    assert restarted.state.gateway.team_resources["r"].used_requests == 1
    assert "a" not in restarted.state.gateway.missing_usage
    assert (await ask(restarted)).status_code == 429
    await restarted.state.gateway.client.aclose()


def legacy_record(event, request_id, **extra):
    return {"event": event, "request_id": request_id, "agent": "a", "run": "r",
            "path": "/v1/chat/completions", "method": "POST", **extra}


def write_records(tmp_path, records):
    (tmp_path / "gateway.jsonl").write_text("".join(json.dumps(record) + "\n" for record in records))


async def test_legacy_completion_and_chat_transport_error_recover_without_double_count(tmp_path):
    write_records(tmp_path, [
        legacy_record("chat_completion", "first", usage_total={"requests": 1, "prompt_tokens": 3, "completion_tokens": 2},
                      response={"usage": {"prompt_tokens": 3, "completion_tokens": 2}}),
        legacy_record("upstream_error", "failed", error="Connection lost"),
        legacy_record("upstream_error", "model-list", path="/v1/models", method="GET", error="Listing failed"),
        legacy_record("chat_completion", "last", usage_total={"requests": 3, "prompt_tokens": 6, "completion_tokens": 4},
                      response={"usage": {"prompt_tokens": 3, "completion_tokens": 2}}),
    ])
    app = create_app(settings(tmp_path, {"requests": 3}), httpx.MockTransport(lambda _: response()))
    assert app.state.gateway.usage_for("a") == {"requests": 3, "prompt_tokens": 6, "completion_tokens": 4}
    assert "a" in app.state.gateway.missing_usage
    assert (await ask(app)).status_code == 429
    await app.state.gateway.client.aclose()


async def test_legacy_unlogged_attempt_gap_keeps_token_usage_unknown(tmp_path):
    write_records(tmp_path, [
        legacy_record("chat_completion", "last", usage_total={"requests": 2, "prompt_tokens": 3, "completion_tokens": 2},
                      response={"usage": {"prompt_tokens": 3, "completion_tokens": 2}}),
    ])
    app = create_app(settings(tmp_path, {"completion_tokens": 100}), httpx.MockTransport(lambda _: response()))
    assert app.state.gateway.usage_for("a")["requests"] == 2
    assert "a" in app.state.gateway.missing_usage
    assert (await ask(app)).status_code == 429
    await app.state.gateway.client.aclose()


@pytest.mark.parametrize("counters", [None, {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0},
                                      {"requests": True, "prompt_tokens": 0, "completion_tokens": 0}])
def test_irreconcilable_legacy_counters_refuse_startup(tmp_path, counters):
    write_records(tmp_path, [
        legacy_record("chat_completion", "first", usage_total={"requests": 1, "prompt_tokens": 3, "completion_tokens": 2},
                      response={"usage": {"prompt_tokens": 3, "completion_tokens": 2}}),
        legacy_record("chat_completion", "last", usage_total=counters),
    ])
    with pytest.raises(ValueError, match="persisted agent usage"):
        create_app(settings(tmp_path), httpx.MockTransport(lambda _: response()))
