import json

import pytest

from mesocosm.core.records import RunBundle, new_run_id


def test_run_id_is_time_ordered_and_unique():
    a, b = new_run_id(), new_run_id()
    assert a.startswith("run_") and a != b
    assert a[:17] <= b[:17]


def test_bundle_is_append_only_and_joins_on_ids(tmp_path):
    bundle = RunBundle.create(tmp_path, "run_test")
    bundle.write_manifest({"name": "w"}, digest="abc")
    with pytest.raises(FileExistsError):
        bundle.write_manifest({"name": "w"}, digest="abc")

    s1 = bundle.next_step("agent-1")
    s2 = bundle.next_step("agent-1")
    assert (s1, s2) == (1, 2)
    bundle.record("harness.message", agent_id="agent-1", step=s1, text="hi")
    bundle.record("gateway.chat_completion", agent_id="agent-1", step=s1, request_id="req-1", prompt_tokens=10)
    bundle.record("verifier.reward", reward=1.0)
    bundle.record_receipt({"op": "conditions.set", "result": "applied"})
    bundle.write_native("agent-1/messages.json", json.dumps([{"id": 1}]))
    with pytest.raises(FileExistsError):
        bundle.write_native("agent-1/messages.json", "again")

    events = bundle.events()
    assert [e.kind for e in events] == ["harness.message", "gateway.chat_completion", "verifier.reward"]
    assert all(e.run_id == "run_test" for e in events)
    by_request = {e.request_id: e for e in events if e.request_id}
    assert by_request["req-1"].data["prompt_tokens"] == 10 and by_request["req-1"].step == 1
    assert bundle.receipts()[0]["result"] == "applied"
    assert bundle.manifest()["digest"] == "abc"

    reopened = RunBundle.open(bundle.root)
    assert len(reopened.events()) == 3
    bundle.write_summary({"ok": True})
    with pytest.raises(FileExistsError):
        bundle.write_summary({"ok": True})


def test_create_refuses_existing_dir(tmp_path):
    RunBundle.create(tmp_path, "run_x")
    with pytest.raises(FileExistsError):
        RunBundle.create(tmp_path, "run_x")
