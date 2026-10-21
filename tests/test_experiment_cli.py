"""CLI orchestration tests with fake Docker/runtime boundaries, not real-agent acceptance evidence."""

from __future__ import annotations

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
