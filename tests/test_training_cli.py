"""CLI/runtime wiring tests; mocked orchestration does not establish GPU execution."""

import json

import pytest
import yaml
from typer.testing import CliRunner

from mesocosm.cli.main import app
from mesocosm.core.manifest import load_manifest
from mesocosm.core.run import RunResult
from mesocosm.env.compose import agent_api_key, render_compose, write_agent_files


def training_file(tmp_path, server="http://10.0.2.2:8000"):
    path = tmp_path / "training.yaml"
    path.write_text(yaml.safe_dump({"name": "test", "world": "world.yaml", "base_model": "test/model",
                                   "base_revision": "a" * 40, "initial_model": "policy", "server_url": server}))
    return path


@pytest.mark.parametrize("args", [["--vllm-host", "wrong"], ["--vllm-port", "9000"]])
def test_training_refuses_two_distinct_inference_servers(tmp_path, args):
    result = CliRunner().invoke(app, ["train", "run", str(training_file(tmp_path)), *args, "--json"])
    assert result.exit_code == 1
    assert "same TrainingSpec.server_url" in json.loads(result.stdout)["error"]


def test_training_preserves_world_result_before_cleanup_failure(tmp_path, monkeypatch):
    from mesocosm.cli import main
    from mesocosm.learning import controller

    out = tmp_path / "output"
    observed = []

    class Stack:
        async def down(self):
            assert json.loads((out / "episode.result.json").read_text())["run_id"] == "run-kept"
            observed.append("cleanup")
            raise RuntimeError("cleanup remained uncertain")

    def factory(compose_dir, runs, workspace, verifier, port, host, stacks, **kwargs):
        assert (host, port) == ("10.0.2.2", 8000)
        assert kwargs["fresh_workspace"] and kwargs["task_runtime"] == "native"
        stacks.append(Stack())

        async def run(manifest):
            return RunResult("run-kept", tmp_path / "run-kept", "ok", {"a/task": 1})
        return run

    async def train(spec, path, destination, run_one):
        destination.mkdir()
        await run_one(destination / "episode.yaml", object())
        pytest.fail("cleanup failure must prevent a training update")

    monkeypatch.setattr(main, "_stack_runner", factory)
    monkeypatch.setattr(controller, "run_training", train)
    result = CliRunner().invoke(app, ["train", "run", str(training_file(tmp_path)), "--out", str(out), "--json"])
    assert result.exit_code == 1 and observed == ["cleanup"]
    assert "cleanup remained uncertain" in json.loads(result.stdout)["error"]


def test_generated_native_config_contains_the_exact_readonly_gate(tmp_path):
    from pathlib import Path

    from mesocosm.adapters.opencode.action_plugin import action_plugin_digest
    from mesocosm.env.compose import GATEWAY_URL

    root = Path(__file__).resolve().parents[1]
    world = load_manifest(root / "examples" / "world-native-three.yaml")
    world.judge.monitor.enabled = True
    world.judge.monitor.timing = "pre_action"
    world.judge.monitor.model = world.model.id
    files = tmp_path / "agents"
    write_agent_files(world, "test", files)
    compose = render_compose(world, repo_root=root, run_label="test", files_dir=files,
                              require_rollout_binding=True)
    environment = compose["services"]["gateway"]["environment"]
    assert environment["MESOCOSM_REQUIRE_ROLLOUT_BINDING"] == "1"
    assert environment["MESOCOSM_RETURN_TOKEN_IDS"] == "1" and not world.observe.activations
    for agent in world.agents:
        import hashlib

        plugin = files / agent.id / "plugins" / "approval-gate.js"
        assert hashlib.sha256(plugin.read_bytes()).hexdigest() == action_plugin_digest(
            GATEWAY_URL, agent_api_key(world, "test", agent.id), world.judge.monitor.decision_timeout_seconds + 5)
        assert f"{plugin}:/root/.config/opencode/plugins/approval-gate.js:ro" in compose["services"][agent.id]["volumes"]
