"""Control-plane tests with synthetic callback results; no real-agent acceptance claim."""

from __future__ import annotations

import asyncio
import json
import pathlib

import pytest
import yaml

from mesocosm.core.config import absolutize_manifest, deep_merge
from mesocosm.core.experiment import (
    Experiment,
    experiment_manifests,
    experiment_table,
    load_experiment,
    run_experiment,
)
from mesocosm.core.records import RunBundle
from mesocosm.core.run import RunResult


def experiment_file(tmp_path, *, conditions=None, **kwargs):
    task = tmp_path / "task"
    task.mkdir(exist_ok=True)
    (task / "instruction.md").write_text("Test fixture for configuration validation only.")
    world = {"name": "base", "model": {"id": "test-model", "hf_repo": "test/model"},
             "gateway": {"url": "http://localhost:8080"},
             "agents": [{"id": f"agent-{i}"} for i in range(1, 4)],
             "tasks": [{"path": "task"}], "services": {"enabled": False},
             "judge": {"monitor": {"enabled": True, "model": "test-model"}}}
    (tmp_path / "world.yaml").write_text(yaml.safe_dump(world))
    spec = {"name": "conditions", "base": "world.yaml",
            "conditions": conditions or [{"name": "baseline"}, {"name": "carry", "patch": {"memory": "carry"}}],
            **kwargs}
    path = tmp_path / "experiment.yaml"
    path.write_text(yaml.safe_dump(spec))
    return path


def test_patch_and_relocation_do_not_mutate_or_alias_inputs(tmp_path):
    base = {"tasks": [{"path": "old"}], "gateway": {"run_label": "base"},
            "observe": {"capture": {"probes": ["probe.json"]}}}
    patch = {"tasks": [{"path": "new"}], "gateway": {"run_label": None}}
    merged = deep_merge(base, patch)
    absolutize_manifest(merged, tmp_path)
    assert merged["tasks"][0]["path"] == str(tmp_path / "new")
    assert merged["observe"]["capture"]["probes"] == [str(tmp_path / "probe.json")]
    assert merged["gateway"] == {}
    assert base["tasks"][0]["path"] == "old"
    assert base["observe"]["capture"]["probes"] == ["probe.json"]
    assert patch["tasks"][0]["path"] == "new"


def test_schema_rejects_ambiguous_or_ignored_configuration(tmp_path):
    path = experiment_file(tmp_path)
    data = yaml.safe_load(path.read_text())
    for override, message in [({"conditions": [{"name": "same"}, {"name": "same"}]}, "unique"),
                              ({"conditions": [{"name": "x", "patch": {"gateway": {"run_label": "shared"}}}]},
                               "cannot be experimental"),
                              ({"stop_on": ["trap_credential"]}, "Extra inputs"),
                              ({"retries": 1}, "Extra inputs"), ({"repetitions": 0}, "greater than or equal")]:
        with pytest.raises(ValueError, match=message):
            Experiment.model_validate({**data, **override})


def test_trials_have_unique_labels_repeats_and_reproducible_scheduling(tmp_path):
    path = experiment_file(tmp_path, repetitions=4, order="randomized", order_seed=31)
    spec = load_experiment(path)
    first = experiment_manifests(spec, path, tmp_path / "first")
    second = experiment_manifests(spec, path, tmp_path / "second")
    assert [(t["condition"], t["repetition"]) for t in first] == [
        (t["condition"], t["repetition"]) for t in second]
    assert [t["index"] for t in first] != list(range(1, 9))
    assert [t["order"] for t in first] == list(range(1, 9))
    assert len({t["run_label"] for t in first + second}) == 16
    assert len({t["manifest"] for t in first}) == 8
    for trial in first:
        world = yaml.safe_load(pathlib.Path(trial["manifest"]).read_text())
        assert world["gateway"]["run_label"] == world["name"] == trial["run_label"]
        assert len(world["name"]) <= 63
        assert world["tasks"][0]["path"] == str(tmp_path / "task")
        assert world.get("memory", "reset") == ("carry" if trial["condition"] == "carry" else "reset")
    assert yaml.safe_load((tmp_path / "world.yaml").read_text())["tasks"][0]["path"] == "task"
    with pytest.raises(FileExistsError):
        experiment_manifests(spec, path, tmp_path / "first")


@pytest.mark.parametrize("patch", [{"conditions": {"telepathy": True}}, {"gateway": {"typo": True}},
                                  {"agents": [{"id": "a", "budget": {"requestz": 10}}]},
                                  {"gateway": 17}, {"rewards": {"allocation": "team_mean"}},
                                  {"judge": {"monitor": {"enabled": False, "scope": "joint"}}}])
async def test_all_conditions_preflight_before_any_execution(tmp_path, patch):
    path = experiment_file(tmp_path, conditions=[{"name": "valid"}, {"name": "invalid", "patch": patch}])
    calls = []

    async def run_one(manifest):
        calls.append(manifest)

    with pytest.raises(ValueError, match="invalid experiment conditions"):
        await run_experiment(load_experiment(path), path, tmp_path / "out", run_one)
    assert calls == []
    assert not (tmp_path / "out").exists()


def test_disabling_monitor_is_an_effective_condition_and_not_missing_monitoring(tmp_path):
    path = experiment_file(tmp_path, conditions=[{"name": "no-monitor", "patch": {
        "judge": {"monitor": None}}}])
    trials = experiment_manifests(load_experiment(path), path, tmp_path / "out")
    assert trials[0]["monitor_enabled"] is False


def test_disabling_monitor_rejects_inherited_dormant_options_until_section_is_removed(tmp_path):
    path = experiment_file(tmp_path, conditions=[{"name": "no-monitor", "patch": {
        "judge": {"monitor": {"enabled": False}}}}])
    with pytest.raises(ValueError, match=r"judge.monitor.model require judge.monitor.enabled=true"):
        experiment_manifests(load_experiment(path), path, tmp_path / "out")
    assert not (tmp_path / "out").exists()


async def test_task_drift_stops_remaining_trials_without_replacing_completed_outcome(tmp_path):
    path = experiment_file(tmp_path, conditions=[{"name": "baseline"}], repetitions=3, order="listed")
    calls = []

    async def run_one(manifest):
        calls.append(manifest)
        bundle = RunBundle.create(tmp_path / "bundles")
        bundle.record("run.start")
        bundle.write_summary({"status": "ok"})
        (tmp_path / "task/instruction.md").write_text("changed while the trial ran")
        return RunResult(bundle.run_id, bundle.root, "ok", {})

    result = await run_experiment(load_experiment(path), path, tmp_path / "out", run_one)
    assert len(calls) == 1 and result["status"] == "failed_input_drift"
    assert [trial["status"] for trial in result["trials"]] == ["ok", "not_run", "not_run"]
    assert result["trials"][0]["input_integrity"] == "failed"
    assert result["trials"][0]["bundle"]
    assert len(list((tmp_path / "out").glob("*.inputs.json"))) == 3


async def test_corrupted_generated_yaml_stops_remaining_trials_as_input_drift(tmp_path):
    path = experiment_file(tmp_path, conditions=[{"name": "baseline"}], repetitions=2, order="listed")
    calls = []

    async def run_one(manifest):
        calls.append(manifest)
        bundle = RunBundle.create(tmp_path / "bundles")
        bundle.record("run.start")
        bundle.write_summary({"status": "ok"})
        manifest.write_text("model: [unterminated")
        return RunResult(bundle.run_id, bundle.root, "ok", {})

    result = await run_experiment(load_experiment(path), path, tmp_path / "out", run_one)
    assert len(calls) == 1 and result["status"] == "failed_input_drift"
    assert [trial["status"] for trial in result["trials"]] == ["ok", "not_run"]


class TrialResults:
    def __init__(self, root):
        self.root, self.calls = root, []

    async def __call__(self, manifest):
        self.calls.append(manifest)
        index = len(self.calls)
        if index == 3:
            raise RuntimeError("runtime unavailable")
        bundle = RunBundle.create(self.root)
        bundle.record("harness.session", agent_id="agent-1", task="task", session_id="s", ts=100)
        bundle.record("gateway.chat_completion", agent_id="agent-1", ts=110,
                      usage={"prompt_tokens": 30, "completion_tokens": 10})
        reward = 0.0 if index == 1 else None
        status = "ok" if index == 1 else "failed_verifier"
        summary = {"status": status, "rewards": {"agent-1/task": reward},
                   "task_outcomes": [{"agent_id": "agent-1", "task": "task",
                                      "status": "verified" if index == 1 else "failed", "reward": reward,
                                      "error": None if index == 1 else "verifier timeout"}],
                   "learning_rewards": [{"agent_id": "agent-1", "task": "task", "raw_reward": reward,
                                         "allocated_reward": 1.0 if index == 1 else None, "source": "peer_verdict",
                                         "allocation": "individual", "evidence": {}}],
                   "monitor": {"scored": 1, "flagged": 1, "unknown": 0, "calls": 1} if index == 1 else None}
        if index == 1:
            bundle.record_flag({"flag_id": "f", "kind": "monitor_suspicion"})
            summary["flags"] = {"monitor_suspicion": 1}
            summary.update(model_calls=1, agent_seconds={"agent-1": 10}, events=2)
        bundle.write_summary(summary)
        return RunResult(bundle.run_id, bundle.root, status, {"agent-1/task": reward})


async def test_failures_and_flags_do_not_select_trials_and_outcomes_remain_separate(tmp_path):
    path = experiment_file(tmp_path, conditions=[{"name": "baseline"}], repetitions=3, order="listed")
    runs = TrialResults(tmp_path / "bundles")
    out = tmp_path / "out"
    result = await run_experiment(load_experiment(path), path, out, runs)
    assert len(runs.calls) == 3
    assert [t["status"] for t in result["trials"]] == ["ok", "failed_verifier", "failed_exception"]
    assert result["status"] == "completed" and result["ok"] == 1 and result["failed"] == 2
    assert result["not_run"] == 0
    row = result["conditions"][0]
    assert row["task_outcomes"]["rewards"] == {"observed": 1, "missing": 1, "sum": 0.0, "mean": 0.0}
    assert row["task_outcomes"]["missing_trials"] == 1
    assert row["task_outcomes"]["statuses"] == {"verified": 1, "failed": 1}
    assert row["learning_rewards"]["rewards"]["mean"] == 1.0
    assert row["learning_rewards"]["rewards"]["missing"] == 1
    assert row["monitor"]["missing_trials"] == 2
    assert row["monitor"]["metrics"]["flagged"]["sum"] == 1
    assert row["flags"] == {"counts": {"monitor_suspicion": 1}, "observed_trials": 1,
                            "missing_trials": 2, "trials_with_flags": 1}
    assert row["model_calls"] == {"observed": 1, "missing": 2, "sum": 1.0, "mean": 1.0}
    assert result["trials"][1]["recorded_metrics"]["model_calls"] == 1
    assert result["trials"][1]["model_calls"] is None
    assert result["trials"][1]["metric_coverage"]["gateway_records_complete"] is False
    assert len((out / "runs.jsonl").read_text().splitlines()) == 3
    assert experiment_table(out)["conditions"] == result["conditions"]
    assert json.loads((out / "experiment.json").read_text())["order_seed_scope"] == "trial scheduling only"


async def test_missing_bundle_does_not_become_zero_flags_or_successful_measurement(tmp_path):
    path = experiment_file(tmp_path, conditions=[{"name": "baseline"}])

    async def run_one(manifest):
        return RunResult("missing", tmp_path / "missing", "failed", {})

    result = await run_experiment(load_experiment(path), path, tmp_path / "out", run_one)
    assert result["incomplete_records"] == 1
    row = result["conditions"][0]
    assert row["flags"]["missing_trials"] == 1
    assert row["flags"]["observed_trials"] == 0
    assert row["model_calls"]["sum"] is None
    assert row["learning_rewards"]["rewards"]["mean"] is None


@pytest.mark.parametrize("complete", [False, True])
async def test_zero_recorded_calls_only_means_zero_usage_after_complete_collection(tmp_path, complete):
    path = experiment_file(tmp_path, conditions=[{"name": "baseline"}])

    async def run_one(manifest):
        bundle = RunBundle.create(tmp_path / "bundles")
        bundle.record("run.start", ts=100)
        status = "ok" if complete else "failed_isolation"
        summary = {"status": status}
        if complete:
            summary.update(model_calls=0, flags={}, agent_seconds={}, events=1)
        bundle.write_summary(summary)
        return RunResult(bundle.run_id, bundle.root, status, {})

    result = await run_experiment(load_experiment(path), path, tmp_path / "out", run_one)
    trial = result["trials"][0]
    assert trial["recorded_metrics"]["model_calls"] == 0
    for key in ("model_calls", "prompt_tokens", "completion_tokens"):
        assert trial[key] == (0 if complete else None)
        assert result["conditions"][0][key]["missing"] == (0 if complete else 1)


async def test_collected_calls_with_missing_token_usage_do_not_report_zero_tokens(tmp_path):
    path = experiment_file(tmp_path, conditions=[{"name": "baseline"}])

    async def run_one(manifest):
        bundle = RunBundle.create(tmp_path / "bundles")
        bundle.record("gateway.chat_completion", agent_id="agent-1", usage=None)
        bundle.write_summary({"status": "ok", "model_calls": 1, "agent_seconds": {}, "events": 1, "flags": {}})
        return RunResult(bundle.run_id, bundle.root, "ok", {})

    result = await run_experiment(load_experiment(path), path, tmp_path / "out", run_one)
    trial = result["trials"][0]
    assert trial["model_calls"] == 1
    assert trial["prompt_tokens"] is None and trial["completion_tokens"] is None
    assert trial["metric_coverage"]["gateway_records_complete"] is True
    assert trial["metric_coverage"]["usage_records"] == {"prompt_tokens": 0, "completion_tokens": 0}


async def test_cancellation_persists_completed_outcomes_and_unrun_plan(tmp_path):
    path = experiment_file(tmp_path, conditions=[{"name": "baseline"}], repetitions=3, order="listed")
    runs = TrialResults(tmp_path / "bundles")

    async def run_one(manifest):
        if runs.calls:
            raise asyncio.CancelledError
        return await runs(manifest)

    out = tmp_path / "out"
    with pytest.raises(asyncio.CancelledError):
        await run_experiment(load_experiment(path), path, out, run_one)
    result = json.loads((out / "experiment.json").read_text())
    assert result["status"] == "interrupted"
    assert [t["status"] for t in result["trials"]] == ["ok", "interrupted", "not_run"]
    assert result["ok"] == 1 and result["not_run"] == 1
