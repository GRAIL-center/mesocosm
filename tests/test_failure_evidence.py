"""Failure snapshots retain real API payloads without inventing successful or complete runs."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from test_run_e2e import MANIFEST
from test_run_e2e import world as world

import mesocosm.core.run as runtime
from mesocosm.core.records import RunBundle
from mesocosm.core.run import run_world


async def test_gate_cleanup_failure_does_not_replace_original_monitor_failure(world, monkeypatch):
    manifest, components, root = world
    manifest.judge.monitor.enabled = True
    manifest.judge.monitor.timing = "pre_action"
    comps = components()
    comps.monitor = object()

    class Gate:
        def __init__(self, **kwargs):
            pass

        async def prepare(self):
            pass

        async def run_turn(self, turn):
            turn.close()
            raise RuntimeError("original monitor failure")

        async def close(self):
            raise RuntimeError("secondary gate close failure")

    monkeypatch.setattr(runtime, "InterventionGate", Gate)
    result = await run_world(manifest, MANIFEST, comps, root / "runs")
    assert result.status == "failed" and "original monitor failure" in result.error
    bundle = RunBundle.open(result.bundle_path)
    cleanup = next(e for e in bundle.events() if e.kind == "harness.control_cleanup_failed")
    assert "secondary gate close failure" in cleanup.data["error"]
    assert "original monitor failure" in cleanup.data["original_error"]
    assert not any(e.kind == "verifier.reward" for e in bundle.events())


@pytest.mark.parametrize("failure", ["exception", "network_timeout", "cancel"])
async def test_aborted_turn_preserves_original_messages_and_both_gateway_labels(world, failure):
    manifest, components, root = world
    manifest.judge.monitor.enabled = True
    manifest.judge.monitor.model = manifest.model.id
    comps = components()
    original = comps.harness_for
    entered = asyncio.Event()
    snapshots, labels = [], []

    def harness_for(agent):
        harness = original(agent)

        async def send(session_id, text, **kwargs):
            entered.set()
            if failure == "exception":
                raise RuntimeError("original turn error")
            if failure == "network_timeout":
                raise TimeoutError("original network timeout")
            await asyncio.Event().wait()

        async def abort(session_id):
            return True

        async def messages(session_id):
            if not entered.is_set():
                return []
            result = [{"info": {"sessionID": session_id, "role": "assistant"}, "parts": [
                {"type": "tool", "callID": "actual-call", "tool": "bash",
                 "state": {"status": "error", "error": "interrupted"}}]}]
            snapshots.append(result)
            return result

        harness.send_message, harness.abort_session, harness.list_messages = send, abort, messages
        return harness

    async def records(label, since=None):
        labels.append(label)
        yield {"event": "chat_completion", "run": label, "request_id": label + "-request",
               "request": {"messages": [{"role": "user", "content": "original evidence"}]}}

    async def never_verify(*args, **kwargs):
        pytest.fail("an interrupted turn must not be verified")

    comps.harness_for, comps.gateway.records, comps.verifier.verify = harness_for, records, never_verify
    running = asyncio.create_task(run_world(manifest, MANIFEST, comps, root / "runs", "failure-evidence"))
    if failure == "cancel":
        await entered.wait()
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
    else:
        result = await running
        assert "original" in result.error
    bundle = RunBundle.open(root / "runs/failure-evidence")
    saved = json.loads((bundle.root / "native/agent-1/hello-world/messages.json").read_text())
    assert saved == snapshots[0]
    assert labels == ["e2e", "e2e-monitor"]
    assert "original evidence" in (bundle.root / "native/gateway/records.jsonl").read_text()
    assert "original evidence" in (bundle.root / "native/gateway/monitor-records.jsonl").read_text()
    summary = json.loads((bundle.root / "summary.json").read_text())
    assert summary["model_calls"] is None and summary["rewards"] == {}
    assert all(item["snapshot_complete"] and not item["run_complete"] for item in summary["gateway_coverage"])
    assert all(item["observed_model_calls"] == 1 for item in summary["gateway_coverage"])
    event = next(e for e in bundle.events() if e.kind == "harness.failure_snapshot")
    assert event.data["snapshot_complete"] is True and event.data["turn_complete"] is False
    assert not any(e.kind == "verifier.reward" for e in bundle.events())


async def test_snapshot_failures_and_timeouts_preserve_original_error_and_partial_records(world, monkeypatch):
    manifest, components, root = world
    comps = components()
    original = comps.harness_for
    monkeypatch.setattr(runtime, "FAILURE_SNAPSHOT_SECONDS", .02)

    def harness_for(agent):
        harness = original(agent)
        sent = False

        async def send(*args, **kwargs):
            nonlocal sent
            sent = True
            raise RuntimeError("original monitor failure")

        async def abort(*args, **kwargs):
            return True

        async def unavailable(*args, **kwargs):
            if not sent:
                return []
            await asyncio.Event().wait()

        harness.send_message, harness.abort_session, harness.list_messages = send, abort, unavailable
        return harness

    async def partial_records(run, since=None):
        yield {"event": "chat_completion", "request_id": "completed-before-disconnect"}
        raise httpx.ConnectError("snapshot connection failed")

    comps.harness_for, comps.gateway.records = harness_for, partial_records
    result = await asyncio.wait_for(run_world(manifest, MANIFEST, comps, root / "runs"), 2)
    assert result.status == "failed" and "original monitor failure" in result.error
    bundle = RunBundle.open(result.bundle_path)
    event = next(e for e in bundle.events() if e.kind == "harness.failure_snapshot")
    assert event.data["snapshot_complete"] is False and event.data["messages"] is None
    assert "TimeoutError" in event.data["error"]
    summary = json.loads((bundle.root / "summary.json").read_text())
    coverage = summary["gateway_coverage"][0]
    assert coverage["status"] == "partial_or_unavailable" and coverage["observed_model_calls"] == 1
    assert summary["model_calls"] is None and not coverage["snapshot_complete"]
    assert "completed-before-disconnect" in (bundle.root / "native/gateway/records.jsonl").read_text()


async def test_existing_gateway_artifact_is_never_rewritten(world):
    manifest, components, root = world
    bundle = RunBundle.create(root / "runs")
    bundle.write_native("gateway/records.jsonl", "original bytes\n")
    comps = components()

    async def should_not_read(*args, **kwargs):
        pytest.fail("existing native evidence must not be fetched or overwritten")
        yield {}

    comps.gateway.records = should_not_read
    coverage = await runtime._preserve_failure_gateway(manifest, comps, bundle, 0)
    assert coverage[0]["status"] == "existing" and coverage[0]["observed_model_calls"] is None
    assert (bundle.root / "native/gateway/records.jsonl").read_text() == "original bytes\n"
