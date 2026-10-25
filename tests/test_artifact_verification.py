"""Native runtime capture ordering with recorded Docker commands, not GPU acceptance evidence."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from test_task_runtime import DockerCommands, task

from mesocosm.core.artifacts import ArtifactSpec
from mesocosm.core.manifest import AgentSpec
from mesocosm.env.task_runtime import TaskEnvironmentError
from mesocosm.integrations.tasks import Reward


async def prepared(tmp_path, **options):
    runtime = DockerCommands(tmp_path, **options)
    native = task(tmp_path)
    workspace = tmp_path / "workspace"
    await runtime.prepare(AgentSpec(id="agent-1"), native, workspace, reset=True)
    (workspace / "answer.py").write_text("before pause")
    return runtime, native, workspace


async def test_capture_follows_acknowledged_pause_and_precedes_original_grading(tmp_path, monkeypatch):
    runtime, native, workspace = await prepared(tmp_path)
    spec = ArtifactSpec(enabled=True, paths=["answer.py", "verifier-only.txt"])
    snapshots = []
    command = runtime.command

    async def last_background_write(*args, **kwargs):
        if args[:3] == ("docker", "container", "pause"):
            # A live agent finishes a background write while pause is being requested.
            (workspace / "answer.py").write_text("at acknowledged pause")
        return await command(*args, **kwargs)

    def captured(snapshot):
        container = snapshot["capture_evidence"]["container_id"]
        assert runtime.containers[container]["State"]["Paused"] is True
        assert snapshot["files"]["answer.py"]["text"] == "at acknowledged pause"
        assert snapshot["missing"] == ["verifier-only.txt"]
        snapshots.append(snapshot)

    async def grade(task, group, agent_id, workspace, logs_dir, script):
        assert len(snapshots) == 1, "persist input capture before any grader starts"
        assert script == "test.sh"
        assert (workspace / "answer.py").read_text() == snapshots[0]["files"]["answer.py"]["text"]
        # An original grader may create scratch outputs; those are never publication sources.
        (workspace / "answer.py").write_text("grader scratch")
        (workspace / "verifier-only.txt").write_text("private test output")
        return Reward(1.0, "original")

    monkeypatch.setattr(runtime, "command", last_background_write)
    monkeypatch.setattr(runtime, "_measure", grade)
    reward = await runtime.verify_with_artifacts(native, workspace, tmp_path / "logs", spec, on_capture=captured)
    assert reward.value == 1.0
    assert snapshots[0]["files"]["answer.py"]["text"] == "at acknowledged pause"
    assert snapshots[0]["capture_evidence"]["measurement_scope"] == "original_task_not_individual_file"
    assert not runtime.active, "original reset-mode cleanup still runs"


async def test_failed_pause_cannot_capture_or_grade(tmp_path, monkeypatch):
    runtime, native, workspace = await prepared(tmp_path)
    runtime.pause_error = True
    snapshots = []

    async def must_not_grade(*args, **kwargs):
        pytest.fail("unquiesced inputs reached the verifier")

    monkeypatch.setattr(runtime, "_measure", must_not_grade)
    with pytest.raises(TaskEnvironmentError, match="quiesced"):
        await runtime.verify_with_artifacts(native, workspace, tmp_path / "logs",
                                            ArtifactSpec(enabled=True, paths=["answer.py"]), on_capture=snapshots.append)
    assert snapshots == []


async def test_original_grader_failure_preserves_controller_capture(tmp_path, monkeypatch):
    runtime, native, workspace = await prepared(tmp_path)
    snapshots = []

    async def broken_grade(*args, **kwargs):
        assert len(snapshots) == 1
        raise TaskEnvironmentError("original grader failed")

    monkeypatch.setattr(runtime, "_measure", broken_grade)
    with pytest.raises(TaskEnvironmentError, match="original grader failed"):
        await runtime.verify_with_artifacts(native, workspace, tmp_path / "logs",
                                            ArtifactSpec(enabled=True, paths=["answer.py"]), on_capture=snapshots.append)
    assert len(snapshots) == 1 and snapshots[0]["files"]["answer.py"]["text"] == "before pause"
    assert not runtime.active


async def test_shared_measurements_cannot_label_individual_artifacts(tmp_path):
    runtime = DockerCommands(tmp_path, task_services="shared")
    native = task(tmp_path)
    snapshots = []
    with pytest.raises(TaskEnvironmentError, match="isolated verification"):
        await runtime.verify_with_artifacts(native, tmp_path / "workspace", tmp_path / "logs",
                                            ArtifactSpec(enabled=True, paths=["answer.py"]), on_capture=snapshots.append)
    assert snapshots == [] and runtime.calls == []


@pytest.mark.parametrize("covered", [False, True])
async def test_separate_verifier_requires_the_same_declared_input_path(tmp_path, monkeypatch, covered):
    runtime, native, workspace = await prepared(tmp_path)
    native.config["verifier"] = {"environment_mode": "separate"}
    native.config["artifacts"] = ["/app/native-project/answer.py" if covered else "/some-other-output"]
    snapshots = []

    async def grade(*args, **kwargs):
        assert len(snapshots) == 1
        return Reward(1.0, "original")

    monkeypatch.setattr(runtime, "_measure", grade)
    call = runtime.verify_with_artifacts(native, workspace, tmp_path / "logs",
                                        ArtifactSpec(enabled=True, paths=["answer.py"]), on_capture=snapshots.append)
    if covered:
        assert (await call).value == 1.0
        assert len(snapshots) == 1
    else:
        with pytest.raises(TaskEnvironmentError, match="original verifier inputs"):
            await call
        assert snapshots == []
        assert not any(args[:3] == ("docker", "container", "pause") for args in runtime.calls)
        await runtime.finish()


async def test_child_volume_is_not_mistaken_for_host_workspace_content(tmp_path):
    runtime, native, workspace = await prepared(tmp_path)
    group = runtime.groups[runtime.workspace_groups[str(workspace.resolve())]]
    group.stack = SimpleNamespace(main_by_agent={"agent-1": {"volumes": [{"target": "/app/native-project"}]}})
    snapshots = []
    with pytest.raises(TaskEnvironmentError, match="child mounts"):
        await runtime.verify_with_artifacts(native, workspace, tmp_path / "logs",
                                            ArtifactSpec(enabled=True, paths=["answer.py"]), on_capture=snapshots.append)
    assert snapshots == []
    group.stack = None
    await runtime.finish()
