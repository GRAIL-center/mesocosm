"""Owned workload teardown finishes before the immutable run summary, without Docker fixtures as acceptance."""
from __future__ import annotations

import asyncio
import json

import pytest
from test_run_e2e import MANIFEST
from test_run_e2e import world as world

from mesocosm.adapters.harbor.task import Reward
from mesocosm.core.records import RunBundle
from mesocosm.core.run import run_world


@pytest.mark.parametrize("initial", ["ok", "failed_verifier"])
async def test_cleanup_failure_preserves_scores_and_original_failure(world, monkeypatch, initial):
    manifest, components, tmp_path = world
    monkeypatch.setattr("mesocosm.core.run.EVENT_GRACE_SECONDS", 0)
    calls = []

    class Verifier:
        async def verify(self, *args, **kwargs):
            if initial == "failed_verifier":
                raise RuntimeError("original verifier error")
            return Reward(0.5, "reward.txt")

    async def finish():
        calls.append("finish")
        assert not list((tmp_path / "runs").rglob("summary.json")), "summary must remain unwritten during cleanup"
        raise RuntimeError("owned task container still running")

    comps = components()
    comps.verifier = Verifier()
    comps.finish_runtime = finish
    result = await asyncio.wait_for(run_world(manifest, MANIFEST, comps, tmp_path / "runs"), 3)
    assert calls == ["finish"]
    assert result.status == ("failed_cleanup" if initial == "ok" else initial)
    assert ("owned task container" if initial == "ok" else "original verifier error") in result.error
    summary = json.loads((result.bundle_path / "summary.json").read_text())
    assert summary["status"] == result.status
    if initial == "ok":
        assert list(summary["rewards"].values()) == [0.5]
    cleanup = [event for event in RunBundle.open(result.bundle_path).events() if event.kind == "runtime.cleanup"]
    assert len(cleanup) == 1 and cleanup[0].data["status"] == "failed"
    assert "owned task container" in cleanup[0].data["error"]


async def test_successful_cleanup_precedes_success_summary(world, monkeypatch):
    manifest, components, tmp_path = world
    monkeypatch.setattr("mesocosm.core.run.EVENT_GRACE_SECONDS", 0)
    async def finish():
        assert not list((tmp_path / "runs").rglob("summary.json"))
    comps = components()
    comps.finish_runtime = finish
    result = await asyncio.wait_for(run_world(manifest, MANIFEST, comps, tmp_path / "runs"), 3)
    assert result.status == "ok", result.error
    cleanup = [event for event in RunBundle.open(result.bundle_path).events() if event.kind == "runtime.cleanup"]
    assert len(cleanup) == 1 and cleanup[0].data == {"status": "complete"}


@pytest.mark.parametrize("cleanup_fails", [False, True])
async def test_cancellation_awaits_same_cleanup_and_preserves_its_receipt(world, monkeypatch, cleanup_fails):
    manifest, components, tmp_path = world
    monkeypatch.setattr("mesocosm.core.run.EVENT_GRACE_SECONDS", 0)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def finish():
        calls.append("finish")
        entered.set()
        await release.wait()
        if cleanup_fails:
            raise RuntimeError("cleanup remained unverified")

    comps = components()
    comps.finish_runtime = finish
    run = asyncio.create_task(run_world(manifest, MANIFEST, comps, tmp_path / "runs"))
    await asyncio.wait_for(entered.wait(), 2)
    run.cancel()
    await asyncio.sleep(0)
    run.cancel()  # A repeated interruption must not cancel the owned cleanup either.
    await asyncio.sleep(0)
    assert not run.done()
    assert calls == ["finish"]
    assert not list((tmp_path / "runs").rglob("summary.json"))
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(run, 2)
    summary_path = next((tmp_path / "runs").rglob("summary.json"))
    assert json.loads(summary_path.read_text())["status"] == "cancelled"
    cleanup = [event for event in RunBundle.open(summary_path.parent).events() if event.kind == "runtime.cleanup"]
    assert len(cleanup) == 1
    assert cleanup[0].data["status"] == ("failed" if cleanup_fails else "complete")
    if cleanup_fails:
        assert "cleanup remained unverified" in cleanup[0].data["error"]
    assert calls == ["finish"]
