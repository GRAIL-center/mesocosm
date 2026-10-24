"""Reasoning wire-format compatibility; in-process contracts, not GPU acceptance."""

from __future__ import annotations

import json

import httpx
import pytest

from mesocosm.gateway.app import Settings, create_app, parse_sse, summarize_response


@pytest.mark.parametrize(("fields", "expected"), [
    ({"reasoning": "Inspect. "}, "Inspect. "),
    ({"reasoning_content": "Inspect. "}, "Inspect. "),
    ({"reasoning": "Inspect. ", "reasoning_content": "Inspect. "}, "Inspect. "),
    ({"reasoning": "Current. ", "reasoning_content": "Legacy. "}, "Current. "),
    ({"reasoning": None, "reasoning_content": "Legacy. "}, "Legacy. "),
    ({"reasoning": "", "reasoning_content": "Legacy. "}, "Legacy. "),
    ({}, ""),
])
def test_reasoning_aliases_normalize_once_for_streaming_and_non_streaming(fields, expected):
    # vLLM 0.32 emits `reasoning`; older servers emit `reasoning_content`.
    chunks = [
        {"choices": [{"index": 0, "delta": {"content": None, **fields}}]},
        {"choices": [{"index": 0, "delta": {"content": None, **fields}}]},
        {"choices": [{"index": 0, "delta": {"content": "Answer."}, "finish_reason": "stop"}]},
    ]
    stream = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
    parsed = parse_sse(stream)
    assert parsed["reasoning"] == expected * 2
    assert parsed["content"] == "Answer."
    assert parsed["finish_reason"] == "stop"
    assert parsed["stream_complete"] is True

    parsed = summarize_response({"choices": [{"message": {"content": "Answer.", **fields},
                                             "finish_reason": "stop"}]})
    assert parsed["reasoning"] == expected
    assert parsed["content"] == "Answer."
    assert parsed["finish_reason"] == "stop"


def test_interrupted_reasoning_stream_keeps_observed_text_without_inventing_completion_or_usage():
    raw = 'data: {"choices":[{"index":0,"delta":{"content":null,"reasoning":"Partial."}}]}\n\n'
    parsed = parse_sse(raw)
    assert parsed["reasoning"] == "Partial."
    assert parsed["content"] == ""
    assert parsed["stream_complete"] is False
    assert parsed["finish_reason"] is None
    assert parsed["usage"] is None


@pytest.mark.parametrize("stream", [False, True])
async def test_gateway_logs_current_reasoning_without_changing_upstream_response(tmp_path, stream):
    message = {"role": "assistant", "content": "Answer.", "reasoning": "Inspect. "}
    usage = {"prompt_tokens": 4, "completion_tokens": 5, "total_tokens": 9,
             "completion_tokens_details": {"reasoning_tokens": 3}}
    if stream:
        chunks = [
            {"id": "reasoning-call", "choices": [{"index": 0, "delta": message,
                                                  "finish_reason": "stop"}]},
            {"id": "reasoning-call", "choices": [], "usage": usage},
        ]
        body = ("".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n").encode()
        content_type = "text/event-stream"
    else:
        body = json.dumps({"id": "reasoning-call", "choices": [{"index": 0, "message": message,
                                                               "finish_reason": "stop"}],
                           "usage": usage}).encode()
        content_type = "application/json"

    class UpstreamStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield body

    def upstream(request):
        return httpx.Response(200, stream=UpstreamStream(), headers={"content-type": content_type})

    app = create_app(Settings(upstream="http://upstream", log_dir=tmp_path),
                     transport=httpx.MockTransport(upstream))
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway",
                                    headers={"Authorization": "Bearer mesocosm-local"}) as client:
            response = await client.post("/v1/chat/completions", json={"model": "m", "stream": stream,
                                                                     "messages": []})
        assert response.status_code == 200
        assert response.content == body
        records = [json.loads(line) for line in (tmp_path / "gateway.jsonl").read_text().splitlines()]
        completion = next(record for record in records if record["event"] == "chat_completion")
        assert completion["response"]["reasoning"] == "Inspect. "
        assert completion["response"]["content"] == "Answer."
        assert completion["response"]["usage"] == usage
    finally:
        await app.state.gateway.client.aclose()
