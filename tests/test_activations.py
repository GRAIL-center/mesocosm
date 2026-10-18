"""The bundle side of activation recompute: which requests qualify, which get raw tensors, the summary
and comparison math, the index. The GPU side is verified on Colab (capability matrix)."""

import json

from mesocosm.core.records import RunBundle
from mesocosm.observe.activations import (
    agreement,
    default_layers,
    norms,
    raw_request_ids,
    read_index,
    read_summary,
    recomputable,
    select,
    token_sequence,
    vector_stats,
    write_index_entry,
    write_summary,
)


def record(rid, agent, prompt=(1, 2, 3), completion=(4, 5), status=200, event="chat_completion"):
    return {"request_id": rid, "agent": agent, "event": event, "status": status, "ts": 1.0,
            "request": {"temperature": 0.0},
            "response": {"prompt_token_ids": list(prompt), "token_ids": list(completion)}}


def bundle_with_records(tmp_path, records, flags=()):
    b = RunBundle.create(tmp_path / "runs", "run_a")
    b.write_manifest({"name": "a"}, "d")
    b.write_native("gateway/records.jsonl", "".join(json.dumps(r) + "\n" for r in records))
    for f in flags:
        b.record_flag(f)
    return b


def test_select_keeps_the_requests_that_named_the_served_model(tmp_path):
    a, b = record("r1", "agent-1"), record("r2", "agent-2")
    a["request"]["model"], b["request"]["model"] = "qwen3-4b", "local-b"
    bundle = bundle_with_records(tmp_path, [a, b])
    assert [r["request_id"] for r in select(bundle.root, served_model="local-b")] == ["r2"]
    assert [r["request_id"] for r in select(bundle.root)] == ["r1", "r2"]


def test_only_successful_completions_with_token_ids_are_recomputable(tmp_path):
    no_ids = record("r3", "agent-1")
    no_ids["response"] = {"content": "x"}
    records = [record("r1", "agent-1"), record("r2", "agent-2", status=429), no_ids,
               record("r4", "agent-2", event="passthrough"), record("r5", "agent-2", prompt=(9,), completion=(8,))]
    assert [r["request_id"] for r in recomputable(records)] == ["r1", "r5"]
    b = bundle_with_records(tmp_path, records)
    assert [r["request_id"] for r in select(b.root)] == ["r1", "r5"]
    assert [r["request_id"] for r in select(b.root, agents=["agent-2"])] == ["r5"]
    assert [r["request_id"] for r in select(b.root, request_ids=["r5", "zz"])] == ["r5"]
    assert [r["request_id"] for r in select(b.root, limit=1)] == ["r1"]
    assert token_sequence(records[0]) == ([1, 2, 3], [4, 5])


def test_raw_policy_follows_the_judge(tmp_path):
    records = [record("r1", "agent-1"), record("r2", "agent-2"), record("r3", "agent-3")]
    flags = [{"flag_id": "f1", "agent_id": "agent-2", "kind": "impersonation", "evidence": []},
             {"flag_id": "f2", "agent_id": None, "kind": "hidden_channel_use",
              "evidence": [{"kind": "x", "request_id": "r3"}]}]
    b = bundle_with_records(tmp_path, records, flags)
    assert raw_request_ids(b.root, records, "flagged") == {"r2", "r3"}
    assert raw_request_ids(b.root, records, "all") == {"r1", "r2", "r3"}
    assert raw_request_ids(b.root, records, "none") == set()


def test_math_helpers():
    assert default_layers(36) == [9, 18, 27] and default_layers(3) == [1, 2]
    assert norms([[3.0, 4.0], [0.0, 0.0]]) == [5.0, 0.0]
    # prompt of 3 tokens: the prediction at position 2 should be the first completion token
    assert agreement([0, 0, 4, 5, 9], prompt_len=3, completion=[4, 5]) == 1.0
    assert agreement([0, 0, 4, 7, 9], prompt_len=3, completion=[4, 5]) == 0.5
    assert agreement([], prompt_len=3, completion=[]) == 1.0
    same = vector_stats([1.0, 2.0, 3.0], [1.0, 2.0, 3.0])
    assert same["rel_l2"] == 0.0 and same["cosine"] == 1.0 and same["max_abs"] == 0.0 and same["n"] == 3
    off = vector_stats([1.0, 0.0], [0.0, 1.0])
    assert off["cosine"] == 0.0 and round(off["rel_l2"], 4) == round(2 ** 0.5, 4)


def test_index_and_summaries_live_inside_the_bundle(tmp_path):
    b = bundle_with_records(tmp_path, [record("r1", "agent-1")])
    path = write_summary(b.root, "r1", {"norms": {"9": [1.0, 2.0]}})
    write_index_entry(b.root, {"request_id": "r1", "method": "recompute", "summary_file": path})
    assert read_index(b.root) == [{"request_id": "r1", "method": "recompute", "summary_file": "activations/summaries/r1.json"}]
    assert read_summary(b.root, "r1")["norms"]["9"] == [1.0, 2.0]
    assert read_index(tmp_path / "runs" / "nope") == [] if not (tmp_path / "runs" / "nope").exists() else True
