"""Shared measurements are control-plane contracts, not native agent acceptance evidence."""

from __future__ import annotations

import asyncio
import json

import pytest
from test_experiment_conditions import _three_agents
from test_run_e2e import two_tasks
from test_run_e2e import world as world

from mesocosm.adapters.harbor.task import Reward
from mesocosm.core.experiment import _outcome_metric
from mesocosm.core.manifest import TaskSpec
from mesocosm.core.records import RunBundle
from mesocosm.core.run import run_world


@pytest.mark.parametrize("cleanup_fails", [False, True])
async def test_three_participants_reference_one_original_measurement(world, monkeypatch, cleanup_fails):
    manifest, components, tmp_path = world
    monkeypatch.setattr("mesocosm.core.run.EVENT_GRACE_SECONDS", 0)
    _three_agents(manifest)
    manifest.task_services = "shared"
    manifest.tasks = [TaskSpec(path="tasks/first")]
    path = two_tasks(tmp_path)
    barrier = asyncio.Barrier(3)

    class TeamVerifier:
        async def verify(self, task, workspace, logs_dir, script="test.sh"):
            await barrier.wait()
            (logs_dir / "environment.json").write_text(json.dumps({
                "measurement_id": "one-original-measurement", "measurement_scope": "team",
                "participant_ids": [a.id for a in manifest.agents]}))
            return Reward(0.5, "reward.txt")

    comps = components()
    comps.verifier = TeamVerifier()
    if cleanup_fails:
        async def finish():
            raise RuntimeError("owned cleanup failed after complete measurement")
        comps.finish_runtime = finish
    result = await asyncio.wait_for(run_world(manifest, path, comps, tmp_path / "runs"), 3)
    assert result.status == ("failed_cleanup" if cleanup_fails else "ok"), result.error
    events = RunBundle.open(result.bundle_path).events()
    measured = [event for event in events if event.kind == "verifier.team_reward"]
    references = [event for event in events if event.kind == "verifier.reward"]
    assert len(measured) == 1 and len(references) == 3
    assert len(measured[0].links) == 3
    assert all(event.links[0].event_id == measured[0].event_id for event in references)
    summary = json.loads((result.bundle_path / "summary.json").read_text())
    assert len(summary["task_measurements"]) == 1
    assert summary["task_measurements"][0]["status"] == "verified"
    assert len(summary["task_outcomes"]) == 3
    metric = _outcome_metric([summary], "task_outcomes", "reward")
    assert metric["records"] == 3
    assert metric["rewards"] == {"observed": 1, "missing": 0, "sum": 0.5, "mean": 0.5}


@pytest.mark.parametrize("values,expected", [([0, 0, 0], 0), ([0, 1, 0], None)])
def test_team_aggregation_deduplicates_within_trial_only(values, expected):
    outcomes = [{"measurement_id": "same-id", "measurement_scope": "team", "reward": value,
                 "status": "verified"} for value in values]
    metric = _outcome_metric([{"task_outcomes": outcomes}] * 2, "task_outcomes", "reward")
    assert metric["records"] == 6
    assert metric["rewards"]["observed"] == (0 if expected is None else 2)
    assert metric["rewards"]["missing"] == (2 if expected is None else 0)
    assert metric["rewards"]["mean"] == expected


@pytest.mark.parametrize("failure", ["missing_metadata", "wrong_participants", "conflicting_reward", "missing_copy"])
async def test_invalid_team_evidence_is_a_failed_verification(world, monkeypatch, failure):
    manifest, components, tmp_path = world
    monkeypatch.setattr("mesocosm.core.run.EVENT_GRACE_SECONDS", 0)
    _three_agents(manifest)
    manifest.task_services = "shared"
    manifest.tasks = [TaskSpec(path="tasks/first")]
    path = two_tasks(tmp_path)
    barrier = asyncio.Barrier(3)

    class Verifier:
        async def verify(self, task, workspace, logs_dir, script="test.sh"):
            await barrier.wait()
            if failure == "missing_copy" and workspace.name == "agent-2":
                await asyncio.sleep(0)
                raise RuntimeError("participant verification copy unavailable")
            if failure != "missing_metadata":
                (logs_dir / "environment.json").write_text(json.dumps({
                    "measurement_id": "one-measurement", "measurement_scope": "team",
                    "participant_ids": ["wrong"] if failure == "wrong_participants"
                    else [a.id for a in manifest.agents]}))
            return Reward(1 if failure == "conflicting_reward" and workspace.name == "agent-2" else 0, "reward.txt")

    comps = components()
    comps.verifier = Verifier()
    result = await asyncio.wait_for(run_world(manifest, path, comps, tmp_path / "runs"), 3)
    assert result.status == "failed_verifier", result.error
    events = RunBundle.open(result.bundle_path).events()
    assert any(event.kind == "verifier.failed" for event in events)
    assert not any(event.kind == "learning.reward" for event in events)
    summary = json.loads((result.bundle_path / "summary.json").read_text())
    metric = _outcome_metric([summary], "task_outcomes", "reward")
    assert metric["rewards"]["observed"] == 0
    if failure in ("conflicting_reward", "missing_copy"):
        assert len(summary["task_measurements"]) == 1
        measurement = summary["task_measurements"][0]
        assert measurement["reward"] is None and measurement["status"] == "unknown"
        assert measurement["recorded_reward"] == 0
        assert all(outcome["reward"] is None and outcome["measurement_id"] == measurement["measurement_id"]
                   for outcome in summary["task_outcomes"])
        assert metric["rewards"] == {"observed": 0, "missing": 1, "sum": None, "mean": None}
