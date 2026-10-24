"""Protocol fixtures verify terminal handling, not real-agent task acceptance."""
from __future__ import annotations

import asyncio
import copy
import json

import pytest
from test_action_gate import gate_http as gate_http
from test_action_gate import setup_controller
from test_run_e2e import MANIFEST
from test_run_e2e import world as world

from mesocosm.core.records import RunBundle
from mesocosm.core.run import (
    BudgetRejected,
    _budget_state,
    _interruptible_turn,
    _turn_terminator,
    _watch_turn_budget,
    run_world,
)


def transform_turn(components, change):
    original = components.harness_for
    aborted = []

    def harness_for(agent):
        harness = original(agent)
        send, messages = harness.send_message, harness.list_messages
        histories = {}

        async def altered(session_id, text, **kwargs):
            await send(session_id, text, **kwargs)
            history = await messages(session_id)
            reply = change(history)
            histories[session_id] = history
            return history[-1] if reply is None else reply

        async def snapshot(session_id):
            return histories[session_id] if session_id in histories else await messages(session_id)

        async def abort(session_id):
            aborted.append(session_id)
            return True

        harness.send_message, harness.list_messages, harness.abort_session = altered, snapshot, abort
        return harness

    components.harness_for = harness_for
    return aborted


@pytest.mark.parametrize("failure", ["api_error", "length", "unknown_finish", "missing_finish",
                                     "unfinished_time", "running_tool", "pending_tool", "post_only_error",
                                     "summary_error", "summary_length", "summary_missing_finish",
                                     "summary_get_error", "summary_get_unfinished", "summary_stale_post_error",
                                     "summary_stale_post_length", "summary_stale_post_unfinished"])
async def test_incomplete_returned_turn_is_preserved_and_never_graded(world, monkeypatch, failure):
    monkeypatch.setattr("mesocosm.core.run.EVENT_GRACE_SECONDS", 0)
    manifest, components, root = world
    comps = components()

    def change(history):
        reply = history[-1]
        if failure.startswith("summary_"):
            earlier_post = copy.deepcopy(reply)
            reply = copy.deepcopy(reply)
            reply["info"].update(id="summary", summary=True)
            reply["parts"] = [{"type": "text", "text": "Internal compaction summary."}]
            history.append(reply)
            if failure.startswith("summary_stale_post_"):
                if failure.endswith("error"):
                    reply["info"]["error"] = {"name": "APIError"}
                elif failure.endswith("length"):
                    reply["info"]["finish"] = "length"
                else:
                    reply["info"]["time"].pop("completed")
                return earlier_post
            if failure in {"summary_get_error", "summary_get_unfinished"}:
                healthy_post = copy.deepcopy(reply)
                if failure == "summary_get_error":
                    reply["info"]["error"] = {"name": "APIError"}
                else:
                    reply["info"]["time"].pop("completed")
                return healthy_post
            if failure == "summary_error":
                reply["info"]["error"] = {"name": "APIError"}
            elif failure == "summary_length":
                reply["info"]["finish"] = "length"
            else:
                reply["info"].pop("finish")
            return None
        if failure == "post_only_error":
            reply = copy.deepcopy(reply)
            reply["info"]["error"] = {"name": "APIError"}
            return reply
        if failure == "api_error":
            reply["info"].pop("finish")
            reply["info"]["error"] = {"name": "APIError", "data": {"statusCode": 429, "isRetryable": True,
                                        "responseBody": '{"error":{"code":"insufficient_quota"}}'}}
            reply["parts"] = []
        elif failure in {"length", "unknown_finish"}:
            reply["info"]["finish"] = "length" if failure == "length" else "unknown"
        elif failure == "missing_finish":
            reply["info"].pop("finish")
            reply["parts"] = []
        elif failure == "unfinished_time":
            reply["info"]["time"].pop("completed")
        else:
            reply["parts"][0]["state"]["status"] = failure.removesuffix("_tool")

    aborted = transform_turn(comps, change)

    async def never_grade(*args, **kwargs):
        pytest.fail("an incomplete harness turn must not be graded")

    comps.verifier.verify = never_grade
    result = await run_world(manifest, MANIFEST, comps, root / "runs")
    assert result.status == "failed_harness_turn", result.error
    assert len(aborted) == 1
    bundle = RunBundle.open(result.bundle_path)
    events = bundle.events()
    event = next(event for event in events if event.kind == "harness.turn")
    saved = json.loads((bundle.root / event.data["path"]).read_text())
    if failure.startswith(("summary_get_", "summary_stale_post_")):
        assert saved["reply"] != saved["messages"][-1]
    elif failure == "post_only_error":
        assert "error" not in saved["messages"][-1]["info"]
        assert saved["reply"]["info"]["error"] and saved["message_errors"]
    else:
        assert saved["reply"] == saved["messages"][-1]
    assert saved["user_message_id"] == "msg_0"
    if failure == "api_error":
        assert saved["reply"]["info"]["error"]["data"]["statusCode"] == 429
        assert saved["text"] == "" and saved["message_errors"]
    assert any(event.kind == "harness.failure_snapshot" for event in events)
    assert not any(event.kind in {"harness.reply", "verifier.reward"} for event in events)


async def test_completed_filesystem_task_does_not_require_report_text(world, monkeypatch):
    monkeypatch.setattr("mesocosm.core.run.EVENT_GRACE_SECONDS", 0)
    manifest, components, root = world
    comps = components()
    transform_turn(comps, lambda history: history[-1].update(parts=[]))
    result = await run_world(manifest, MANIFEST, comps, root / "runs")
    assert result.status == "ok" and result.rewards == {"agent-1/hello-world": 1.0}
    turn = json.loads((result.bundle_path / "native/agent-1/hello-world/turn-1.json").read_text())
    assert turn["finish_reason"] == "stop" and turn["text"] == ""


@pytest.mark.parametrize("last_summary,stale_post", [(False, False), (True, False), (True, True)])
async def test_report_compaction_and_recovered_continuation_remain_one_turn(world, monkeypatch, last_summary, stale_post):
    monkeypatch.setattr("mesocosm.core.run.EVENT_GRACE_SECONDS", 0)
    manifest, components, root = world
    comps = components()

    def change(history):
        reply = history[-1]
        reply["info"]["finish"] = "length"
        reply["parts"] = [{"id": "part-before", "type": "text", "text": "Finding before compaction."}]
        session = reply["info"]["sessionID"]
        summary = copy.deepcopy(reply)
        summary["info"].update(id="summary", parentID="compact", summary=True, finish="stop")
        summary["parts"] = [{"type": "text", "text": "Internal summary, not a report."}]
        final = copy.deepcopy(reply)
        final["info"].update(id="final", parentID="continue", finish="stop")
        final["parts"] = [{"id": "part-after", "type": "text", "text": "Finding after continuation."}]
        history.extend([
            {"info": {"id": "compact", "role": "user", "sessionID": session},
             "parts": [{"type": "compaction", "auto": True}]},
            summary,
            {"info": {"id": "continue", "role": "user", "sessionID": session},
             "parts": [{"type": "text", "text": "Continue", "synthetic": True}]},
            final,
        ])
        if last_summary:
            final_summary = copy.deepcopy(summary)
            final_summary["info"]["id"] = "final-summary"
            history.append(final_summary)
        if stale_post:
            return final

    transform_turn(comps, change)
    result = await run_world(manifest, MANIFEST, comps, root / "runs")
    assert result.status == "ok", result.error
    turn = json.loads((result.bundle_path / "native/agent-1/hello-world/turn-1.json").read_text())
    assert turn["text"] == "Finding before compaction.\n\nFinding after continuation."
    assert len(turn["messages"]) == (7 if last_summary else 6)
    assert turn["reply"]["info"]["id"] == ("final-summary" if last_summary and not stale_post else "final")
    assert turn["length_message_ids"] == ["msg_1"]
    assert [part["part_id"] for part in turn["text_parts"]] == ["part-before", "part-after"]


@pytest.mark.parametrize("agent_count,fast_return", [(1, False), (3, False), (1, True)])
async def test_actual_budget_denial_interrupts_retrying_harness_and_preserves_partial_turn(
    world, monkeypatch, agent_count, fast_return,
):
    monkeypatch.setattr("mesocosm.core.run.EVENT_GRACE_SECONDS", 0)
    monkeypatch.setattr("mesocosm.core.run.BUDGET_POLL_SECONDS", 60 if fast_return else .01)
    manifest, components, root = world
    manifest.agents[0].budget.requests = 1
    for index in range(2, agent_count + 1):
        manifest.agents.append(manifest.agents[0].model_copy(
            update={"id": f"agent-{index}", "url": f"http://127.0.0.1:{4095 + index}"}))
    comps = components()
    original = comps.harness_for
    statuses, aborted, cancelled = [], [], []
    ready, started = asyncio.Event(), set()

    def harness_for(agent):
        harness = original(agent)
        history = []

        async def send(session_id, text, **kwargs):
            history.append({"info": {"id": "user", "role": "user", "sessionID": session_id},
                            "parts": [{"type": "text", "text": text}]})
            try:
                started.add(agent.id)
                if len(started) == agent_count:
                    ready.set()
                await ready.wait()
                for _ in range(2):
                    response = await comps.gateway.client.post("/v1/chat/completions", json={
                        "model": "mesocosm-3b", "messages": [{"role": "user", "content": text}]},
                        headers={"X-Mesocosm-Agent": agent.id, "X-Mesocosm-Run": "e2e"})
                    statuses.append(response.status_code)
                if fast_return:
                    reply = {"info": {"id": "error", "parentID": "user", "role": "assistant",
                                      "sessionID": session_id, "time": {"created": 1, "completed": 2},
                                      "error": {"name": "APIError", "data": {"statusCode": 429}}}, "parts": []}
                    history.append(reply)
                    return reply
                await asyncio.Event().wait()  # A real harness would wait and retry this permanent refusal.
            finally:
                cancelled.append(session_id)

        async def messages(session_id):
            return history

        async def abort(session_id):
            aborted.append(session_id)
            return True

        harness.send_message, harness.list_messages, harness.abort_session = send, messages, abort
        return harness

    async def never_grade(*args, **kwargs):
        pytest.fail("a budget-terminated task must not be graded")

    comps.harness_for, comps.verifier.verify = harness_for, never_grade
    result = await asyncio.wait_for(run_world(manifest, MANIFEST, comps, root / "runs"), 2)
    assert result.status == "failed_budget" and "requests" in result.error
    assert sorted(statuses) == [200] * agent_count + [429] * agent_count
    assert len(set(aborted)) == len(set(cancelled)) == agent_count
    bundle = RunBundle.open(result.bundle_path)
    refusal = next(event.data["refusal"] for event in bundle.events() if event.kind == "harness.budget_rejected")
    assert refusal["dimension"] == "requests" and refusal["reason"] == "quota_exhausted"
    snapshot = json.loads((bundle.root / "native/agent-1/hello-world/messages.json").read_text())
    assert snapshot[0]["info"]["id"] == "user"
    if fast_return:
        turn = json.loads((bundle.root / "native/agent-1/hello-world/turn-1.json").read_text())
        assert turn["reply"]["info"]["error"]["data"]["statusCode"] == 429
    assert not any(event.kind in {"harness.reply", "verifier.reward"} for event in bundle.events())


@pytest.mark.parametrize("stale_refusal", [False, True])
async def test_last_admitted_request_can_finish_at_exact_budget(world, monkeypatch, stale_refusal):
    monkeypatch.setattr("mesocosm.core.run.EVENT_GRACE_SECONDS", 0)
    monkeypatch.setattr("mesocosm.core.run.BUDGET_POLL_SECONDS", .01)
    manifest, components, root = world
    manifest.agents[0].budget.requests = 3 if stale_refusal else 2
    comps = components()
    if stale_refusal:
        await comps.gateway.set_budget("agent-1", {"requests": 1})
        for status in (200, 429):
            response = await comps.gateway.client.post("/v1/chat/completions", json={
                "model": "mesocosm-3b", "messages": [{"role": "user", "content": "earlier attempt"}]},
                headers={"X-Mesocosm-Agent": "agent-1", "X-Mesocosm-Run": "e2e"})
            assert response.status_code == status
    original = comps.harness_for

    def harness_for(agent):
        harness = original(agent)
        send = harness.send_message

        async def delayed(*args, **kwargs):
            reply = await send(*args, **kwargs)
            await asyncio.sleep(.04)  # Poll while usage equals the cap, without any refused request.
            return reply

        harness.send_message = delayed
        return harness

    comps.harness_for = harness_for
    result = await run_world(manifest, MANIFEST, comps, root / "runs")
    assert result.status == "ok", result.error
    assert not any(event.kind == "harness.budget_rejected" for event in RunBundle.open(result.bundle_path).events())


@pytest.mark.parametrize("bad_field,bad_value", [("dimension", None), ("reason", ""), ("event_id", 12),
                                                ("sequence", 2), ("sequence", True), ("agent", "another")])
async def test_invalid_budget_receipt_is_rejected(bad_field, bad_value):
    receipt = {"run": "r", "agent": "a", "dimension": "requests", "reason": "quota_exhausted",
               "event_id": "event", "sequence": 1}
    receipt[bad_field] = bad_value

    class Gateway:
        async def terminal_refusal(self, run, agent_id, *, after):
            return {"cursor": 1, "refusal": receipt}

    with pytest.raises(RuntimeError, match="invalid terminal budget refusal receipt"):
        await _budget_state(Gateway(), "r", "a", after=0)


async def test_budget_control_timeout_is_not_reported_as_task_deadline(monkeypatch):
    monkeypatch.setattr("mesocosm.core.run.BUDGET_CONTROL_TIMEOUT_SECONDS", .01)

    class Gateway:
        async def terminal_refusal(self, run, agent_id, *, after):
            await asyncio.Event().wait()

    with pytest.raises(RuntimeError, match="terminal budget refusal control timed out"):
        await _budget_state(Gateway(), "r", "a", after=0)


@pytest.mark.parametrize("cleanup_fails", [False, True])
async def test_budget_abort_starts_workload_termination_before_rpc_cancellation_waits(world, monkeypatch, cleanup_fails):
    monkeypatch.setattr("mesocosm.core.run.BUDGET_POLL_SECONDS", .01)
    _, components, root = world
    comps = components()
    release, cancelled = asyncio.Event(), asyncio.Event()
    calls = []

    class Workloads:
        async def terminate(self, agent_ids):
            calls.append(agent_ids)
            release.set()
            if cleanup_fails:
                raise RuntimeError("termination evidence unavailable")
            return {"agent-1": {"status": "terminated"}}

    class Gateway:
        async def terminal_refusal(self, run, agent_id, *, after):
            return {"cursor": 1, "refusal": {"run": run, "agent": agent_id, "dimension": "requests",
                    "reason": "quota_exhausted", "event_id": "denial", "sequence": 1}}

    async def operation():
        try:
            await asyncio.Event().wait()
        finally:
            await release.wait()  # Cancellation cannot finish until the owned workload exits.
            cancelled.set()

    comps.workloads = Workloads()
    bundle = RunBundle.create(root / "runs")
    terminate = _turn_terminator(comps, bundle, "agent-1", "task")
    watched = _watch_turn_budget(operation, Gateway(), bundle, "r", "agent-1", "task", after=0,
                                on_interrupt=terminate)
    with pytest.raises(BudgetRejected, match="requests"):
        await asyncio.wait_for(_interruptible_turn(watched, bundle, terminate), 1)
    assert cancelled.is_set() and calls == [["agent-1"]]
    events = bundle.events()
    assert sum(event.kind == "workload.termination" for event in events) == 1
    if cleanup_fails:
        assert next(event for event in events if event.kind == "workload.termination").data["evidence"]["status"] == "unverified"


async def test_monitored_budget_wrapper_retains_budget_status_and_cleanup_evidence(world, gate_http, monkeypatch):
    manifest, components, root = world
    comps = components()
    manifest.judge.monitor.enabled = True
    manifest.judge.monitor.timing = "pre_action"
    http, _ = gate_http
    gate, harness, _, monitor, admin = await setup_controller(
        http, root / "gate-fixture", {"score": 5}, state=comps.intervention)
    comps.monitor = monitor

    async def run_monitored_failure(manifest, path, components, bundle):
        gate.bundle = bundle

        async def denied():
            raise BudgetRejected("agent-1/task: requests (quota_exhausted)")

        await gate.run_turn(denied())

    monkeypatch.setattr("mesocosm.core.run._run", run_monitored_failure)
    try:
        result = await run_world(manifest, MANIFEST, comps, root / "runs")
        assert result.status == "failed_budget" and "InterventionError" in result.error
        assert "requests (quota_exhausted)" in result.error
        assert harness.aborted and gate.binding is None
        bundle = RunBundle.open(result.bundle_path)
        assert any(event.kind == "monitor.runtime_failed" for event in bundle.events())
        assert not any(event.kind == "verifier.reward" for event in bundle.events())
        assert json.loads((bundle.root / "summary.json").read_text())["status"] == "failed_budget"
    finally:
        await monitor.aclose()
        await admin.aclose()
