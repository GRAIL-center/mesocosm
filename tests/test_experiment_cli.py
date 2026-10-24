"""CLI orchestration tests with fake Docker/runtime boundaries, not real-agent acceptance evidence."""

from __future__ import annotations

import asyncio
import json
import pathlib
from types import SimpleNamespace

import pytest
import yaml
from typer.testing import CliRunner

from mesocosm.cli import main
from mesocosm.core import run as runtime
from mesocosm.core.manifest import load_manifest
from mesocosm.core.records import RunBundle
from mesocosm.core.run import RunResult
from mesocosm.env import stack


def write_experiment(tmp_path, *, invalid=False):
    world = {"name": "cli-test", "model": {"id": "test-model", "hf_repo": "test/model"},
             "gateway": {"url": "http://localhost:8080", "api_key": "unit-test-control-key"},
             "agents": [{"id": f"agent-{i}"} for i in range(1, 4)],
             "tasks": [{"dataset": "terminal-bench@2.0/fix-git"}], "services": {"enabled": False},
             "memory": "carry"}
    (tmp_path / "world.yaml").write_text(yaml.safe_dump(world))
    conditions = [{"name": "baseline"}, {"name": "second"}, {"name": "third"}]
    if invalid:
        conditions[-1]["patch"] = {"agents": [{"id": "a", "budget": {"unknown_budget": 4}}]}
    plan = {"name": "cli-test", "base": "world.yaml", "order": "listed", "conditions": conditions}
    path = tmp_path / "experiment.yaml"
    path.write_text(yaml.safe_dump(plan))
    return path


def invoke(*args):
    result = CliRunner().invoke(main.app, [*map(str, args), "--json"])
    lines = result.stdout.splitlines()
    assert len(lines) == 1, (result.stdout, result.exception)
    return result.exit_code, json.loads(lines[0])


@pytest.mark.parametrize("failure", ["run_result", "startup_exception"])
def test_cli_runs_every_trial_and_keeps_custom_paths_fresh(tmp_path, monkeypatch, failure):
    experiment = write_experiment(tmp_path)
    runs, workspaces, output = tmp_path / "custom-runs", tmp_path / "custom-workspaces", tmp_path / "output"
    transitions, runtime_calls = [], []

    class FakeStack:
        def __init__(self, compose_file):
            self.path = compose_file

        async def up(self):
            transitions.append(("up", self.path))
            if failure == "startup_exception" and len(transitions) == 1:
                raise RuntimeError("synthetic Docker startup failure")

        async def down(self):
            transitions.append(("down", self.path))

    async def fake_runtime(manifest, compose_file, runs_root, workspace_root, *args, **kwargs):
        spec = load_manifest(manifest)
        label = spec.gateway.run_label
        runtime_calls.append(manifest)
        assert kwargs["task_runtime"] == "native"
        assert runs_root == runs
        assert workspace_root == workspaces / label
        compose = yaml.safe_load(pathlib.Path(compose_file).read_text())
        assert compose["name"] == f"mesocosm-{spec.name}"
        assert f"{runs}:/runs" in compose["services"]["gateway"]["volumes"]
        for agent in spec.agents:
            workspace = workspace_root / agent.id
            assert not workspace.exists()  # memory=carry cannot inherit another trial's bind mount
            workspace.mkdir(parents=True)
            (workspace / "previous-trial-marker").write_text(label)
            mounts = compose["services"][agent.id]["volumes"]
            assert f"{workspace}:/work" in mounts
            config = runs / label / "agents" / agent.id / "opencode.json"
            assert config.is_file()
            assert f"{config}:/root/.config/opencode/opencode.json:ro" in mounts
        bundle = RunBundle.create(runs_root)
        bundle.record("run.start")
        status = "failed_verifier" if failure == "run_result" and len(runtime_calls) == 1 else "ok"
        summary = {"status": status, "task_outcomes": [], "learning_rewards": []}
        if status == "ok":
            summary.update(model_calls=0, flags={}, agent_seconds={}, events=1)
        bundle.write_summary(summary)
        return RunResult(bundle.run_id, bundle.root, status, {})

    monkeypatch.setattr(stack, "DockerStack", FakeStack)
    monkeypatch.setattr(main, "_run_manifest", fake_runtime)
    code, report = invoke("experiment", "run", experiment, "--runs-root", runs,
                          "--workspace-root", workspaces, "--out", output)
    assert code == 1 and report["status"] == "completed"
    assert report["ok"] == 2 and report["failed"] == 1 and report["not_run"] == 0
    assert len(runtime_calls) == (3 if failure == "run_result" else 2)
    assert [operation for operation, _ in transitions] == ["up", "down", "up", "down", "up", "down"]
    assert len({str(path) for operation, path in transitions if operation == "up"}) == 3
    result = json.loads((output / "experiment.json").read_text())
    assert len({trial["run_label"] for trial in result["trials"]}) == 3
    assert len(list(workspaces.iterdir())) == len(runtime_calls)
    assert invoke("experiment", "report", output) == (0, report)


def test_cli_validates_late_invalid_condition_before_starting_any_stack(tmp_path, monkeypatch):
    experiment = write_experiment(tmp_path, invalid=True)
    calls = []

    class NeverStarted:
        def __init__(self, *args):
            calls.append(args)

    monkeypatch.setattr(stack, "DockerStack", NeverStarted)
    code, report = invoke("experiment", "run", experiment, "--out", tmp_path / "out")
    assert code == 1 and report["status"] == "failed_experiment"
    assert "unknown_budget" in report["error"]
    assert calls == []
    assert not (tmp_path / "out").exists()


def test_cli_malformed_yaml_has_one_json_error_response(tmp_path):
    path = tmp_path / "malformed.yaml"
    path.write_text("conditions: [\n")
    code, report = invoke("experiment", "run", path, "--out", tmp_path / "out")
    assert code == 1 and report["status"] == "invalid_experiment"
    assert "invalid experiment YAML" in report["error"]
    assert not (tmp_path / "out").exists()


def test_cli_final_trial_input_drift_exits_nonzero_even_when_all_trial_counters_are_successful(tmp_path, monkeypatch):
    experiment = write_experiment(tmp_path)
    plan = yaml.safe_load(experiment.read_text())
    plan["conditions"] = plan["conditions"][:1]
    experiment.write_text(yaml.safe_dump(plan))
    output = tmp_path / "out"

    class FakeStack:
        def __init__(self, compose_file):
            self.cleanup_error = None

        async def up(self):
            pass

        async def down(self):
            pass

    async def fake_runtime(manifest, compose_file, runs_root, *args, **kwargs):
        bundle = RunBundle.create(runs_root)
        bundle.record("run.start")
        bundle.write_summary({"status": "ok", "model_calls": 0, "flags": {}, "events": 1, "agent_seconds": {}})
        changed = yaml.safe_load(manifest.read_text())
        changed["model"]["hf_repo"] = "changed/model"
        manifest.write_text(yaml.safe_dump(changed))
        return RunResult(bundle.run_id, bundle.root, "ok", {})

    monkeypatch.setattr(stack, "DockerStack", FakeStack)
    monkeypatch.setattr(main, "_run_manifest", fake_runtime)
    code, report = invoke("experiment", "run", experiment, "--out", output,
                          "--runs-root", tmp_path / "runs", "--workspace-root", tmp_path / "workspaces")
    assert report["status"] == "failed_input_drift" and report["ok"] == 1
    assert all(report[key] == 0 for key in ("failed", "not_run", "stopped", "incomplete_records"))
    assert report["lifecycle_errors"] == []
    assert code == 1, "input drift invalidates the experiment even after its last successful trial"
    saved = json.loads((output / "experiment.json").read_text())
    assert saved["trials"][0]["status"] == "ok" and saved["trials"][0]["input_integrity"] == "failed"


@pytest.mark.parametrize("fails", [False, True])
async def test_run_manifest_closes_all_control_clients_even_when_runtime_raises(tmp_path, monkeypatch, fails):
    write_experiment(tmp_path)
    closed = []

    class Client:
        def __init__(self, name):
            self.name = name

        async def aclose(self):
            closed.append(self.name)

    components = SimpleNamespace(gateway=Client("gateway"), services=Client("services"), monitor=Client("monitor"))

    async def run_world(*args):
        assert closed == []
        if fails:
            raise RuntimeError("synthetic failure")
        return "completed"

    monkeypatch.setattr(main, "_components", lambda *args, **kwargs: components)
    monkeypatch.setattr(runtime, "run_world", run_world)
    args = (tmp_path / "world.yaml", tmp_path / "compose.yaml", tmp_path / "runs", tmp_path / "workspaces", None)
    if fails:
        with pytest.raises(RuntimeError, match="synthetic failure"):
            await main._run_manifest(*args)
    else:
        assert await main._run_manifest(*args) == "completed"
    assert sorted(closed) == ["gateway", "monitor", "services"]


def test_native_runtime_rejects_documentation_placeholder_before_starting_components(tmp_path):
    write_experiment(tmp_path)
    world = load_manifest(tmp_path / "world.yaml")
    world.gateway.api_key = "replace-with-a-fresh-random-control-key"
    with pytest.raises(ValueError, match="non-default gateway control key"):
        main._components(world, tmp_path / "missing-compose.yaml", tmp_path / "runs", tmp_path / "workspaces",
                         None, "localhost", 8000, task_runtime="native")


@pytest.mark.parametrize("failure_phase", ["transition", "final_cleanup"])
def test_cleanup_failure_preserves_completed_trials_and_stops_new_worlds(tmp_path, monkeypatch, failure_phase):
    experiment = write_experiment(tmp_path)
    output = tmp_path / "out"
    stacks, calls, downs = [], [], []

    class FakeStack:
        def __init__(self, compose_file):
            self.path, self.cleanup_error = compose_file, None
            stacks.append(self)

        async def up(self):
            pass

        async def down(self):
            downs.append(self.path)
            if failure_phase == "transition" or len(stacks) == 3:
                self.cleanup_error = stack.StackCleanupError(str(self.path), "synthetic teardown timeout")
                raise self.cleanup_error

    async def fake_runtime(manifest, compose_file, runs_root, *args, **kwargs):
        bundle = RunBundle.create(runs_root)
        bundle.record("run.start")
        bundle.write_summary({"status": "ok", "model_calls": 0, "flags": {}, "events": 1, "agent_seconds": {}})
        calls.append(bundle.run_id)
        return RunResult(bundle.run_id, bundle.root, "ok", {})

    monkeypatch.setattr(stack, "DockerStack", FakeStack)
    monkeypatch.setattr(main, "_run_manifest", fake_runtime)
    code, report = invoke("experiment", "run", experiment, "--out", output,
                          "--runs-root", tmp_path / "runs", "--workspace-root", tmp_path / "workspaces")
    completed = 1 if failure_phase == "transition" else 3
    assert code == 1 and report["status"] == "failed_cleanup"
    assert report["ok"] == completed and report["not_run"] == 3 - completed
    assert len(calls) == len(stacks) == len(downs) == completed
    assert len(report["lifecycle_errors"]) == 1
    assert "synthetic teardown timeout" in report["lifecycle_errors"][0]["error"]
    saved = json.loads((output / "experiment.json").read_text())
    assert [trial["run_id"] for trial in saved["trials"] if trial["status"] == "ok"] == calls
    assert all(trial["status"] == "not_run" for trial in saved["trials"][completed:])
    assert saved["lifecycle_errors"] == report["lifecycle_errors"]
    assert invoke("experiment", "report", output) == (0, report)


def test_cancelled_final_cleanup_is_persisted_without_swallowing_cancellation(tmp_path, monkeypatch):
    experiment = write_experiment(tmp_path)
    plan = yaml.safe_load(experiment.read_text())
    plan["conditions"] = plan["conditions"][:1]
    experiment.write_text(yaml.safe_dump(plan))
    output = tmp_path / "out"
    completed = []

    class FakeStack:
        def __init__(self, compose_file):
            self.path, self.cleanup_error = compose_file, None

        async def up(self):
            pass

        async def down(self):
            self.cleanup_error = stack.StackCleanupError(str(self.path), "teardown cancelled; world state is unknown")
            raise asyncio.CancelledError

    async def fake_runtime(manifest, compose_file, runs_root, *args, **kwargs):
        bundle = RunBundle.create(runs_root)
        bundle.record("run.start")
        bundle.write_summary({"status": "ok", "model_calls": 0, "flags": {}, "events": 1, "agent_seconds": {}})
        completed.append(bundle.run_id)
        return RunResult(bundle.run_id, bundle.root, "ok", {})

    monkeypatch.setattr(stack, "DockerStack", FakeStack)
    monkeypatch.setattr(main, "_run_manifest", fake_runtime)
    with pytest.raises(asyncio.CancelledError):
        invoke("experiment", "run", experiment, "--out", output,
               "--runs-root", tmp_path / "runs", "--workspace-root", tmp_path / "workspaces")
    saved = json.loads((output / "experiment.json").read_text())
    assert saved["status"] == "failed_cleanup" and saved["ok"] == 1
    assert saved["trials"][0]["run_id"] == completed[0] and saved["trials"][0]["status"] == "ok"
    assert saved["lifecycle_errors"][0]["phase"] == "final_cleanup"
    assert "cancelled" in saved["lifecycle_errors"][0]["error"]
    code, report = invoke("experiment", "report", output)
    assert code == 0 and report["lifecycle_errors"] == saved["lifecycle_errors"]
