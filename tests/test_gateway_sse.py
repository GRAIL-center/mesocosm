"""Unit tests for the gateway's SSE collapsing. The spike gateway is a standalone script,
so it is loaded by path; when the gateway moves into src/mesocosm/gateway (M1) this
import becomes a normal one."""

import importlib.util
import pathlib

SPIKE = pathlib.Path(__file__).resolve().parents[1] / "spike" / "02_gateway.py"


def _load():
    spec = importlib.util.spec_from_file_location("spike_gateway", SPIKE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_parse_sse_collapses_content_tool_calls_usage_and_token_ids():
    gw = _load()
    raw = "\n".join([
        'data: {"id":"c1","model":"m","prompt_token_ids":[1,2,3],"choices":[{"index":0,"delta":{"role":"assistant","content":"Hel"},"token_ids":[10]}]}',
        'data: {"id":"c1","choices":[{"index":0,"delta":{"content":"lo"},"token_ids":[11]}]}',
        'data: {"id":"c1","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"bash","arguments":"{\\"cmd\\": \\"ls"}}]}}]}',
        'data: {"id":"c1","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\"}"}}]},"finish_reason":"tool_calls"}]}',
        'data: {"id":"c1","choices":[],"usage":{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}}',
        "data: [DONE]",
        "",
    ])
    out = gw.parse_sse(raw)
    assert out["content"] == "Hello"
    assert out["tool_calls"] == [{"id": "call_1", "type": "function",
                                  "function": {"name": "bash", "arguments": '{"cmd": "ls"}'}}]
    assert out["finish_reason"] == "tool_calls"
    assert out["usage"]["completion_tokens"] == 2
    assert out["prompt_token_ids"] == [1, 2, 3]
    assert out["token_ids"] == [10, 11]
    assert out["chunks"] == 5
    assert out["unparsed_lines"] == 0


def test_parse_sse_tolerates_garbage_and_missing_fields():
    gw = _load()
    out = gw.parse_sse("data: not json\n\nevent: ping\ndata: {\"choices\":[{\"delta\":{}}]}\n")
    assert out["unparsed_lines"] == 1
    assert out["content"] == ""
    assert out["tool_calls"] == []
    assert out["token_ids"] is None


def test_budget_exhaustion_is_per_dimension():
    gw = _load()
    gw.BUDGET.clear()
    gw.BUDGET["agent-x"] = {"completion_tokens": 10, "requests": 100}
    gw._usage.clear()
    assert gw._budget_exhausted("agent-x") is None
    gw._add_usage("agent-x", {"prompt_tokens": 5, "completion_tokens": 10})
    assert gw._budget_exhausted("agent-x") == "completion_tokens"
    assert gw._budget_exhausted("agent-without-budget") is None
