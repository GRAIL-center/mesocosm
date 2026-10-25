"""Reference calibration orchestration tests; Docker and task execution are not simulated acceptance."""

from __future__ import annotations

import json

import pytest

from mesocosm.adapters.harbor.task import Reward, load_task
from mesocosm.core.manifest import AgentSpec
from mesocosm.env.reference import calibrate_task


class Runtime:
    def __init__(self, scores=(0, 1), fail=None, mutate=None):
        self.scores, self.fail, self.mutate = iter(scores), fail, mutate
        self.calls = []

    async def prepare(self, agent, task, workspace, *, reset, restored=False):
        self.calls.append(("prepare", reset))
        workspace.mkdir()
        if self.fail == "prepare":
            raise RuntimeError("SECRET prepare failure")
        return {"container": "a" * 64, "user": "task-user", "workdir": "/app"}

    async def verify(self, task, workspace, logs_dir, script="test.sh"):
        self.calls.append(("verify", script))
        logs_dir.mkdir()
        (workspace / "verifier-write").write_text("baseline verifier can mutate its workspace")
        if self.fail == logs_dir.name:
            raise RuntimeError("SECRET verifier failure")
        return Reward(next(self.scores), "reward.txt")

    async def finish(self):
        self.calls.append(("finish",))
        if self.fail == "finish":
            raise RuntimeError("SECRET reset failure")
        return {"status": "completed"}

    async def command(self, *args, timeout=600, include_stderr=False):
        self.calls.append(args)
        if args[-1] == "/solution/solve.sh":
            if self.fail == "solve":
                raise RuntimeError("SECRET solver failure")
            if self.mutate:
                self.mutate.write_text("mutated")
        return b"private solver output"


def setup(tmp_path, shebang="#!/bin/bash"):
    task = tmp_path / "task"
    (task / "solution").mkdir(parents=True)
    (task / "tests").mkdir()
    (task / "instruction.md").write_text("Original fixture instruction, not a research task.")
    (task / "tests/test.sh").write_text("#!/bin/sh\nexit 0\n")
    (task / "solution/solve.sh").write_text(shebang + "\n# plumbing reference only\n")
    return load_task(task), AgentSpec(id="reference"), tmp_path / "workspace", tmp_path / "result"


async def test_before_and_after_use_identical_verifier_and_unchanged_native_reference(tmp_path):
    task, agent, workspace, out = setup(tmp_path)
    runtime = Runtime()
    result = await calibrate_task(task, runtime, agent, workspace, out)
    assert result["status"] == "completed"
    assert result["before"]["original_score"] == 0
    assert result["after"]["original_score"] == 1
    assert result["reference_success"] is None  # Caller must compare its declared scale/expectation.
    assert result["score_delta"] == 1
    assert result["agent_rollout"] is False and result["model_calls"] == 0
    assert [call for call in runtime.calls if call[0] == "verify"] == [("verify", "test.sh")] * 2
    solve = next(call for call in runtime.calls if call[-1] == "/solution/solve.sh")
    assert solve == ("docker", "exec", "--user", "task-user", "--workdir", "/app", "a" * 64,
                     "/bin/bash", "/solution/solve.sh")
    assert not (workspace / "solution").exists()
    assert len([call for call in runtime.calls if call[0] == "prepare"]) == 2
    assert runtime.calls.index(("finish",)) > runtime.calls.index(("verify", "test.sh"))
    assert any(call[-3:] == ("-rf", "--", "/solution") for call in runtime.calls)
    assert (workspace / "before/verifier-write").exists()
    assert json.loads((out / "result.json").read_text()) == result
    assert (out / "reference.log").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("scores", [(0, 0), (4, 10), (-1, -2)])
async def test_zero_and_nonbinary_measurements_are_not_infrastructure_failures(tmp_path, scores):
    task, agent, workspace, out = setup(tmp_path)
    result = await calibrate_task(task, Runtime(scores), agent, workspace, out)
    assert result["status"] == "completed"
    assert result["after"]["original_score"] == scores[1]
    assert result["score_delta"] == scores[1] - scores[0]


@pytest.mark.parametrize("phase,stage", [("prepare", "prepare"), ("before", "before_verifier"),
                                        ("solve", "reference_solution"), ("after", "after_verifier"),
                                        ("finish", "reset_environment")])
async def test_failures_preserve_stage_and_private_artifact_without_leaking_error(tmp_path, phase, stage):
    task, agent, workspace, out = setup(tmp_path)
    result = await calibrate_task(task, Runtime(fail=phase), agent, workspace, out)
    assert result["status"] == "failed" and result["stage"] == stage
    assert result["after"] is None
    assert result["before"] is not None if phase in ("solve", "after", "finish") else result["before"] is None
    assert "SECRET" not in json.dumps(result)
    assert "SECRET" in (out / "failure.log").read_text()
    assert json.loads((out / "result.json").read_text()) == result


async def test_mutating_original_inputs_stops_before_after_verifier(tmp_path):
    task, agent, workspace, out = setup(tmp_path)
    runtime = Runtime(mutate=task.path / "tests/test.sh")
    result = await calibrate_task(task, runtime, agent, workspace, out)
    assert result["status"] == "failed" and result["input_drift"] is True
    assert result["after"] is None
    assert len([call for call in runtime.calls if call[0] == "verify"]) == 1


@pytest.mark.parametrize("issue", ["workspace", "overlap", "out_exists", "missing_solution", "solution_link"])
async def test_unsafe_or_reused_calibration_inputs_fail_before_execution(tmp_path, issue):
    task, agent, workspace, out = setup(tmp_path)
    if issue == "workspace":
        workspace.mkdir()
        (workspace / "user-work").write_text("preserve")
    elif issue == "overlap":
        out = workspace / "outputs"
    elif issue == "out_exists":
        out.mkdir()
    elif issue == "missing_solution":
        (task.path / "solution/solve.sh").unlink()
    else:
        (task.path / "solution/solve.sh").unlink()
        (task.path / "solution/solve.sh").symlink_to(task.path / "tests/test.sh")
    runtime = Runtime()
    with pytest.raises(ValueError if issue != "out_exists" else FileExistsError):
        await calibrate_task(task, runtime, agent, workspace, out)
    assert runtime.calls == []


@pytest.mark.parametrize("isolation,cleanup_fails,status", [("supported", False, "completed"),
                                                           ("unverified", False, "failed"),
                                                           ("supported", True, "failed_cleanup")])
@pytest.mark.parametrize("operator_options", [{}, {"verifier_network": "bridge", "allow_unenforced_resources": True}])
async def test_native_calibration_requires_observed_containment_and_always_cleans_owned_project(
        tmp_path, monkeypatch, isolation, cleanup_fails, status, operator_options):
    import shutil
    from types import SimpleNamespace

    import yaml

    import mesocosm.env.stack as stack_module
    import mesocosm.env.task_runtime as runtime_module
    from mesocosm.env.reference import calibrate_native

    task, _, _, out = setup(tmp_path)
    instances, stacks = [], []

    class NativeRuntime(Runtime):
        def __init__(self, compose_file, state_dir, **kwargs):
            super().__init__()
            self.base_compose = yaml.safe_load(compose_file.read_text())
            self.finish_count = 0
            self.options = kwargs
            instances.append(self)
            assert kwargs["task_services"] == "isolated" and kwargs["agent_ids"] == ["reference"]

        async def native_image(self, selected_task):
            return SimpleNamespace(image="actual-native-image")

        async def command(self, *args, **kwargs):
            if args[:2] == ("docker", "info"):
                return b'{"OSType":"linux"}'
            return await super().command(*args, **kwargs)

        async def finish(self):
            self.finish_count += 1
            if cleanup_fails and self.finish_count == 2:
                raise RuntimeError("private cleanup error")
            return await super().finish()

    class Stack:
        def __init__(self, compose_file):
            self.compose_file = compose_file
            self.cleaned = False
            stacks.append(self)

        async def isolation(self, agent_ids):
            return {agent_ids[0]: {"status": isolation}}

        async def down(self):
            self.cleaned = True
            return "removed"

    monkeypatch.setattr(shutil, "which", lambda command: "/available/docker")
    monkeypatch.setattr(runtime_module, "DockerTaskRuntime", NativeRuntime)
    monkeypatch.setattr(stack_module, "DockerStack", Stack)
    result = await calibrate_native(task.path, out, **operator_options)
    assert result["status"] == status
    assert instances[0].options["verifier_network"] == operator_options.get("verifier_network", "none")
    assert instances[0].options["allow_unenforced_resources"] is operator_options.get("allow_unenforced_resources", False)
    assert stacks[0].cleaned
    assert instances[0].finish_count >= 1
    assert json.loads((out / "result.json").read_text()) == result
    compose = yaml.safe_load(stacks[0].compose_file.read_text())
    service = compose["services"]["reference"]
    assert service["image"] == "actual-native-image"
    assert len(service["environment"]["OPENCODE_SERVER_PASSWORD"]) == 64
    assert service["environment"]["OPENCODE_SERVER_PASSWORD"] not in json.dumps(result)
    assert compose["networks"] == {"sandbox": {"internal": True}}
    assert "ports" not in service
    if isolation != "supported":
        assert not any(call[-1] == "/solution/solve.sh" for call in instances[0].calls)
    else:
        assert result["after"]["original_score"] == 1
        assert result["matches_expected"] is True
