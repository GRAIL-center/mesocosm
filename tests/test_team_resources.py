"""Synthetic HTTP peers test real admission logic, not agent behavior."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from starlette.requests import Request

from mesocosm.gateway.app import Settings, create_app
from mesocosm.gateway.resources import ResourceExhausted, TeamResources


def test_reservations_do_not_oversubscribe_and_unknown_usage_is_not_free():
    pool = TeamResources(completion_tokens=10)
    first = {"max_tokens": 6}
    second = {"max_tokens": 8}
    pool.acquire("one", first)
    pool.acquire("two", second)
    assert second["max_tokens"] == 4
    with pytest.raises(ResourceExhausted):
        pool.acquire("three", {})
    pool.release("one", {"completion_tokens": 2})
    assert pool.snapshot()["reserved_completion_tokens"] == 4
    pool.acquire("three", {"max_tokens": 4})
    pool.release("two", None)
    pool.release("three", {"completion_tokens": 0})
    assert pool.active == 0 and pool.unknown_usage
    with pytest.raises(ResourceExhausted):
        pool.acquire("four", {})


@pytest.mark.parametrize("limits", [{"max_tokens": 1, "max_completion_tokens": 100},
                                    {"max_tokens": 100, "max_completion_tokens": 1}])
@pytest.mark.parametrize("count", [1, 3])
def test_conflicting_token_limit_fields_cannot_underreserve_concurrent_requests(limits, count):
    pool = TeamResources(completion_tokens=100, concurrency=3)
    first = {**limits, "n": count}
    second = {"max_completion_tokens": 99, "n": count}
    pool.acquire("one", first)
    pool.acquire("two", second)
    # Either provider interpretation of the two fields must fit the same admitted reservation.
    first_consumption = max(first["max_tokens"], first["max_completion_tokens"]) * count
    second_consumption = second["max_completion_tokens"] * count
    assert first_consumption <= pool.leases["one"]
    assert first_consumption + second_consumption <= 100
    pool.release("one", {"completion_tokens": first_consumption})
    pool.release("two", {"completion_tokens": second_consumption})
    assert pool.used_completion_tokens <= 100 and not pool.unknown_usage


@pytest.mark.parametrize("stream", [False, True])
async def test_three_agents_share_admission_and_release_on_completion(tmp_path, stream):
    started, finish = asyncio.Event(), asyncio.Event()
    calls = []

    class Chunks(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[],"usage":{"completion_tokens":2,"prompt_tokens":3}}\n\n'
            yield b'data: [DONE]\n\n'

    async def upstream(request):
        calls.append(request)
        started.set()
        await finish.wait()
        if stream:
            return httpx.Response(200, stream=Chunks(), headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json={"choices": [], "usage": {"completion_tokens": 2, "prompt_tokens": 3}})

    app = create_app(Settings(log_dir=tmp_path, team_resources={"requests": 2, "concurrency": 1},
                             agent_keys={f"key{i}": {"agent": f"a{i}", "run": "r"} for i in range(3)}),
                     transport=httpx.MockTransport(upstream))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://gateway") as client:
        async def ask(i):
            return await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer key{i}"},
                                     json={"model": "m", "messages": [], "stream": stream})
        first = asyncio.create_task(ask(0))
        await started.wait()
        denied = await ask(1)
        assert denied.status_code == 429 and denied.json()["error"]["param"] == "team_concurrency"
        finish.set()
        assert (await first).status_code == 200
        assert (await ask(2)).status_code == 200
        assert (await ask(1)).json()["error"]["param"] == "team_requests"
        assert len(calls) == 2
        assert app.state.gateway.team_resources["r"].snapshot() == {
            "requests": 2, "completion_tokens": 4, "active": 0,
            "reserved_completion_tokens": 0, "unknown_usage": False}


async def test_cancelled_upstream_releases_slot_but_keeps_unknown_cost(tmp_path):
    started = asyncio.Event()

    async def upstream(request):
        started.set()
        await asyncio.Event().wait()

    app = create_app(Settings(log_dir=tmp_path, team_resources={"completion_tokens": 10, "concurrency": 1},
                             agent_keys={"key": {"agent": "a", "run": "r"}}),
                     transport=httpx.MockTransport(upstream))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://gateway") as client:
        call = asyncio.create_task(client.post("/v1/chat/completions", headers={"Authorization": "Bearer key"},
                                              json={"model": "m", "messages": []}))
        await started.wait()
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call
        assert app.state.gateway.team_resources["r"].active == 0
        assert app.state.gateway.team_resources["r"].unknown_usage


@pytest.mark.parametrize("close_fails", [False, True])
async def test_disconnected_stream_holds_shared_lease_until_upstream_close_is_confirmed(tmp_path, close_fails):
    closing, allow_close = asyncio.Event(), asyncio.Event()

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"index":0,"delta":{"content":"partial"}}]}\n\n'
            await asyncio.Event().wait()

        async def aclose(self):
            closing.set()
            await allow_close.wait()
            if close_fails:
                raise httpx.ReadError("synthetic upstream close failed")

    async def upstream(request):
        if json.loads(request.content).get("stream"):
            return httpx.Response(200, stream=Stream(), headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json={"choices": [], "usage": {"completion_tokens": 1, "prompt_tokens": 1}})

    app = create_app(Settings(log_dir=tmp_path, team_resources={"concurrency": 1},
                             agent_keys={f"key{i}": {"agent": f"a{i}", "run": "r"} for i in range(3)}),
                     transport=httpx.MockTransport(upstream))

    async def receive():
        return {"type": "http.request", "body": b'{"model":"m","messages":[],"stream":true}',
                "more_body": False}

    request = Request({"type": "http", "method": "POST", "path": "/v1/chat/completions", "query_string": b"",
                       "headers": [(b"authorization", b"Bearer key0")]}, receive)
    proxy = next(route.endpoint for route in app.routes if getattr(route, "path", None) == "/{path:path}")
    response = await proxy("v1/chat/completions", request)
    assert b"partial" in await anext(response.body_iterator)
    # Close the response generator before upstream exhaustion, as a disconnected consumer does.
    close = asyncio.create_task(response.body_iterator.aclose())
    await asyncio.wait_for(closing.wait(), 1)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://gateway") as client:
        async def ask(agent):
            return await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer key{agent}"},
                                     json={"model": "m", "messages": []})

        try:
            pool = app.state.gateway.team_resources["r"]
            assert pool.active == 1
            denied = await ask(1)
            assert denied.status_code == 429 and denied.json()["error"]["param"] == "team_concurrency"
        finally:
            allow_close.set()
        if close_fails:
            with pytest.raises(httpx.ReadError, match="close failed"):
                await close
            assert pool.active == 1 and pool.unknown_usage
            assert (await ask(2)).status_code == 429
        else:
            await close
            assert pool.active == 0 and pool.unknown_usage  # Interrupted output has no final usage measurement.
            assert (await ask(2)).status_code == 200


@pytest.mark.parametrize("token_cap", [None, 10])
async def test_nonstream_body_failure_closes_transport_and_preserves_unknown_cost(tmp_path, token_cap):
    class BrokenResponse(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield b'{"choices":'
            raise httpx.ReadError("synthetic body read failed")

        async def aclose(self):
            self.closed = True

    broken = BrokenResponse()
    upstream_calls = 0

    async def upstream(request):
        nonlocal upstream_calls
        upstream_calls += 1
        if upstream_calls == 1:
            return httpx.Response(200, stream=broken)
        return httpx.Response(200, json={"choices": [], "usage": {"completion_tokens": 1, "prompt_tokens": 1}})

    app = create_app(Settings(log_dir=tmp_path, team_resources={"concurrency": 1, "completion_tokens": token_cap},
                             agent_keys={f"key{i}": {"agent": f"a{i}", "run": "r"} for i in range(3)}),
                     transport=httpx.MockTransport(upstream))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://gateway") as client:
        async def ask(agent):
            return await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer key{agent}"},
                                     json={"model": "m", "messages": [], "stream": False})

        failed = await ask(0)
        assert failed.status_code == 502 and failed.json()["error"]["type"] == "server_error"
        pool = app.state.gateway.team_resources["r"]
        assert broken.closed
        assert pool.active == 0 and pool.reserved == 0 and pool.leases == {}
        assert pool.unknown_usage and pool.used_requests == 1
        assert "a0" in app.state.gateway.missing_usage
        # A closed connection frees concurrency, but absent usage never refunds a token budget.
        next_call = await ask(1)
        if token_cap is None:
            assert next_call.status_code == 200 and upstream_calls == 2
        else:
            assert next_call.status_code == 429
            assert next_call.json()["error"]["param"] == "team_completion_tokens"
            assert upstream_calls == 1
        records = list(app.state.gateway.records_for_run("r"))
        parsed = [json.loads(line) for line in records]
        failure = next(record for record in parsed if record["event"] == "upstream_error")
        assert failure["response_complete"] is False
        released = next(record for record in parsed if record["event"] == "resource_released")
        assert released["active"] == 0 and released["unknown_usage"] is True


async def test_gateway_restart_restores_shared_requests_and_measured_tokens(tmp_path):
    calls = 0

    async def upstream(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"choices": [], "usage": {"completion_tokens": 3, "prompt_tokens": 1}})

    settings = Settings(log_dir=tmp_path, team_resources={"requests": 2, "completion_tokens": 10, "concurrency": 1},
                        agent_keys={f"key{i}": {"agent": f"a{i}", "run": "r"} for i in range(3)})
    first = create_app(settings, transport=httpx.MockTransport(upstream))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(first), base_url="http://gateway") as client:
        assert (await client.post("/v1/chat/completions", headers={"Authorization": "Bearer key0"},
                                  json={"model": "m", "messages": []})).status_code == 200
    restarted = create_app(settings, transport=httpx.MockTransport(upstream))
    restored = restarted.state.gateway.team_resources["r"]
    assert restored.snapshot() == {"requests": 1, "completion_tokens": 3, "active": 0,
                                    "reserved_completion_tokens": 0, "unknown_usage": False}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(restarted), base_url="http://gateway") as client:
        async def ask(agent):
            return await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer key{agent}"},
                                     json={"model": "m", "messages": []})

        assert (await ask(1)).status_code == 200
        denied = await ask(2)
        assert denied.status_code == 429 and denied.json()["error"]["param"] == "team_requests"
    assert calls == 2 and restored.used_completion_tokens == 6


async def test_gateway_restart_preserves_inflight_reservation_as_unknown(tmp_path):
    class Unfinished(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
            await asyncio.Event().wait()

    async def upstream(request):
        return httpx.Response(200, stream=Unfinished(), headers={"content-type": "text/event-stream"})

    settings = Settings(log_dir=tmp_path, team_resources={"completion_tokens": 10, "concurrency": 3},
                        agent_keys={f"key{i}": {"agent": f"a{i}", "run": "r"} for i in range(3)})
    first = create_app(settings, transport=httpx.MockTransport(upstream))

    async def receive():
        return {"type": "http.request", "body": b'{"model":"m","messages":[],"stream":true,"max_tokens":5}',
                "more_body": False}

    request = Request({"type": "http", "method": "POST", "path": "/v1/chat/completions", "query_string": b"",
                       "headers": [(b"authorization", b"Bearer key0")]}, receive)
    proxy = next(route.endpoint for route in first.routes if getattr(route, "path", None) == "/{path:path}")
    response = await proxy("v1/chat/completions", request)
    assert b"partial" in await anext(response.body_iterator)
    try:
        # The persisted history ends at admission, as after a process loss before stream accounting.
        restarted = create_app(settings, transport=httpx.MockTransport(upstream))
        restored = restarted.state.gateway.team_resources["r"]
        assert restored.active == 1 and restored.reserved == 5 and restored.unknown_usage
        async with httpx.AsyncClient(transport=httpx.ASGITransport(restarted), base_url="http://gateway") as client:
            denied = await client.post("/v1/chat/completions", headers={"Authorization": "Bearer key1"},
                                       json={"model": "m", "messages": []})
            assert denied.status_code == 429 and denied.json()["error"]["param"] == "team_completion_tokens"
        assert restored.used_requests == 1 and restored.used_completion_tokens == 0
    finally:
        await response.body_iterator.aclose()
