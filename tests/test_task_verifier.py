"""Docker command/transfer contract tests, not real-container acceptance evidence."""

from __future__ import annotations

import json
import os
import pathlib
import shutil

import pytest
from test_task_runtime import task

from mesocosm.env.task_runtime import NativeImage
from mesocosm.env.task_verifier import (
    TaskVerifierError,
    _checked_tree,
    artifact_specs,
    prepare_verifier,
    verifier_environment,
)

CONTAINER = "a" * 64
IMAGE = "sha256:" + "b" * 64


class Docker:
    def __init__(self):
        self.calls = []
        self.sources = {}
        self.uploaded = {}
        self.cgroups = "cgroupfs"
        self.internal = True
        self.fail_upload = False
        self.baked_script = "#!/usr/bin/python3\nprint('original baked verifier')\n"

    async def __call__(self, *args, **kwargs):
        self.calls.append(args)
        if args[:3] == ("docker", "image", "inspect"):
            return json.dumps([{"Id": IMAGE, "Os": "linux", "Config": {
                "User": "1000", "WorkingDir": "/grade", "Env": ["ORIGINAL=1"]}}]).encode()
        if args[:3] == ("docker", "network", "inspect"):
            return json.dumps([{"Internal": self.internal}]).encode()
        if args[:2] == ("docker", "info"):
            return json.dumps({"CgroupDriver": self.cgroups, "Warnings": []}).encode()
        if args[:2] == ("docker", "exec") and args[-2] == "id":
            return b"1000\n"
        if args[:2] == ("docker", "cp"):
            if args[2] == "-L":
                pathlib.Path(args[-1]).write_text(self.baked_script)
            elif args[2].startswith(CONTAINER + ":"):
                source = self.sources.get(args[2].split(":", 1)[1])
                if source is None:
                    raise RuntimeError("Could not find the file in the container")
                destination = pathlib.Path(args[3])
                if source.is_dir():
                    shutil.copytree(source, destination, symlinks=True)
                else:
                    shutil.copy2(source, destination, follow_symlinks=False)
            else:
                if self.fail_upload:
                    raise RuntimeError("Docker stopped during artifact upload")
                root = pathlib.Path(args[2])
                self.uploaded = {"/" + path.relative_to(root).as_posix(): path.read_bytes()
                                 for path in root.rglob("*") if path.is_file()}
        return b"created-container\n"


def native_image():
    return NativeImage("harness-modified-image", "sha256:" + "c" * 64,
                       {"User": "1000", "WorkingDir": "/grade", "Env": ["ORIGINAL=1"]}, "/grade", "native-key")


def separate_task(tmp_path):
    native = task(tmp_path, {"environment": {"docker_image": "original/task", "cpus": 1},
                             "verifier": {"environment_mode": "separate"}})
    return native


async def test_fresh_base_and_original_script_without_live_workspace_or_agent_rootfs(tmp_path):
    original = separate_task(tmp_path)
    original.config["artifacts"] = [{"source": "/app/report.json", "destination": "report.json"}]
    report = tmp_path / "report.json"
    report.write_text('{"result":42}')
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "undeclared.txt").write_text("must not transfer")
    docker = Docker()
    docker.sources["/app/report.json"] = report
    plan = await prepare_verifier(original, native_image(), CONTAINER, workspace, tmp_path / "logs", docker)
    assert plan.command[:4] == ["docker", "exec", "--user", "1000"]
    assert not any(call[:2] == ("docker", "commit") for call in docker.calls)
    inspected = next(call for call in docker.calls if call[:3] == ("docker", "image", "inspect"))
    assert inspected[-1] == native_image().base_id
    create = next(call for call in docker.calls if call[:2] == ("docker", "create"))
    assert plan.command[-2:] == ["/bin/bash", "/tests/test.sh"]
    assert IMAGE in create
    assert f"{original.path / 'tests'}:/tests:ro" in create
    assert not any(str(workspace) in part for part in create)
    assert "--cpus" in create and create[create.index("--cpus") + 1] == "1"
    assert docker.uploaded == {"/app/report.json": report.read_bytes()}
    chown = next(call for call in docker.calls if "chown" in call)
    assert chown[-3:] == ("1000:1000", "--", "/app/report.json")
    assert (tmp_path / "logs/artifacts/report.json").read_bytes() == report.read_bytes()
    assert plan.metadata["image_selection"] == "fresh_agent_base_image"
    assert plan.metadata["artifact_transfers"][-1]["status"] == "absent"
    # An original image's VOLUME declarations create anonymous volumes even without -v inputs.
    assert plan.cleanup[0] == ("docker", "rm", "-f", "-v", create[create.index("--name") + 1])


async def test_fresh_base_preserves_original_compose_main_overrides_before_verifier_overrides(tmp_path):
    original = separate_task(tmp_path)
    original.config["environment"]["env"] = {"TASK_SETTING": "task-value", "PRECEDENCE": "task"}
    original.config["verifier"]["env"] = {"PRECEDENCE": "verifier"}
    native = native_image()
    native.config = {"User": "2000:2000", "WorkingDir": "/app/native-project",
                     "Env": ["DATABASE_URL=postgres://db/application", "PRECEDENCE=compose"]}
    docker = Docker()
    plan = await prepare_verifier(original, native, CONTAINER, tmp_path / "workspace", tmp_path / "logs", docker)
    create = next(call for call in docker.calls if call[:2] == ("docker", "create"))
    assert plan.command[plan.command.index("--user") + 1] == "2000:2000"
    assert plan.command[plan.command.index("--workdir") + 1] == "/app/native-project"
    assert "DATABASE_URL=postgres://db/application" in create
    assert "TASK_SETTING=task-value" in create and "PRECEDENCE=verifier" in create
    assert "PRECEDENCE=compose" not in create and "PRECEDENCE=task" not in create
    assert plan.metadata["original_main_settings_applied"] is True
    assert "postgres://" not in json.dumps(plan.metadata)


@pytest.mark.parametrize("variable", ["OPENCODE_SERVER_PASSWORD", "MESOCOSM_CONTROL_KEY"])
async def test_fresh_base_rejects_original_main_harness_or_control_variables(tmp_path, variable):
    native = native_image()
    native.config["Env"] = [f"{variable}=must-not-inherit"]
    docker = Docker()
    with pytest.raises(TaskVerifierError, match="harness or controller credentials"):
        await prepare_verifier(separate_task(tmp_path), native, CONTAINER, tmp_path / "workspace",
                               tmp_path / "logs", docker)
    assert not any(call[:2] == ("docker", "create") for call in docker.calls)


@pytest.mark.parametrize("image", ["dedicated/verifier:fixed", None])
async def test_dedicated_verifier_definitions_take_precedence_and_keep_baked_tests(tmp_path, image):
    original = separate_task(tmp_path)
    (original.path / "tests/Dockerfile").write_text("FROM ubuntu:24.04\nCOPY . /tests/\n")
    original.config["verifier"]["environment"] = {"cpus": 2, "memory_mb": 512, "env": {"CONFIG": "private"}}
    original.config["verifier"].update(env={"CHECK": "also-private"}, user="2000")
    if image:
        original.config["verifier"]["environment"]["docker_image"] = image
    docker = Docker()
    native = native_image()
    native.config = {"User": "different-main-user", "WorkingDir": "/different-main-cwd", "Env": ["MAIN_ONLY=1"]}
    plan = await prepare_verifier(original, native, CONTAINER, tmp_path / "workspace", tmp_path / "logs", docker)
    create = next(call for call in docker.calls if call[:2] == ("docker", "create"))
    assert plan.command[plan.command.index("--user") + 1] == "2000"
    assert "512m" in create and "2" == create[create.index("--cpus") + 1]
    assert "CONFIG=private" in create and "CHECK=also-private" in create
    assert not any(part.endswith(":/tests:ro") for part in create)
    assert plan.metadata["tests_baked"] is True
    assert plan.command[-2:] == ["/usr/bin/python3", "/tests/test.sh"], "baked shebang wins over host bash"
    assert plan.metadata["verifier_interpreter"] == ["/usr/bin/python3"]
    assert len(plan.metadata["verifier_script_sha256"]) == 64
    assert "private" not in json.dumps(plan.metadata)
    assert plan.metadata["env_names"] == ["CHECK", "CONFIG"]
    assert plan.metadata["workdir"] == "/grade" and plan.metadata["original_main_settings_applied"] is False
    assert "MAIN_ONLY=1" not in create
    if image:
        assert ("docker", "pull", image) in docker.calls
        assert not any(call[:2] == ("docker", "build") for call in docker.calls)
    else:
        build = next(call for call in docker.calls if call[:2] == ("docker", "build"))
        assert build[-1] == str(original.path / "tests")
        assert plan.cleanup[-1][:3] == ("docker", "image", "rm")


async def test_live_service_verifier_uses_internal_network_and_original_world_export(tmp_path):
    original = separate_task(tmp_path)
    original.config["verifier"]["requires_live_processes"] = True
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "world.json").write_text('{"original":"state"}')
    docker = Docker()
    plan = await prepare_verifier(original, native_image(), CONTAINER, tmp_path / "workspace", logs, docker,
                                  network="world_sandbox-agent-1", service_scope="isolated")
    create = next(call for call in docker.calls if call[:2] == ("docker", "create"))
    assert create[create.index("--network") + 1] == "world_sandbox-agent-1"
    assert f"{logs}/world.json:/logs/world.json:ro" in create
    assert f"{logs}/verifier:/logs/verifier" in create
    assert plan.metadata["network"] == "world_sandbox-agent-1"
    assert plan.metadata["world_state_mounted"] is True


async def test_shared_service_verifier_does_not_mount_leaders_private_world_state(tmp_path):
    original = separate_task(tmp_path)
    original.config["verifier"]["requires_live_processes"] = True
    logs = tmp_path / "logs"
    logs.mkdir()
    private_state = '{"agent_id":"agent-1","private_state":"leader-only"}'
    (logs / "world.json").write_text(private_state)
    docker = Docker()
    plan = await prepare_verifier(original, native_image(), CONTAINER, tmp_path / "workspace", logs, docker,
                                  network="world_sandbox-agent-1", service_scope="shared")
    create = next(call for call in docker.calls if call[:2] == ("docker", "create"))
    assert not any("world.json" in argument for argument in create)
    assert (logs / "world.json").read_text() == private_state
    assert plan.metadata["world_state_mounted"] is False
    assert "leader-only" not in json.dumps(plan.metadata)


@pytest.mark.parametrize("network,internal", [("host", True), ("bridge", True), ("world_sandbox", False)])
async def test_verifier_cannot_gain_external_network(tmp_path, network, internal):
    docker = Docker()
    docker.internal = internal
    with pytest.raises(TaskVerifierError, match="network"):
        await prepare_verifier(separate_task(tmp_path), native_image(), CONTAINER, tmp_path / "workspace",
                               tmp_path / "logs", docker, network=network)
    assert not any(call[:2] == ("docker", "create") for call in docker.calls)


@pytest.mark.parametrize("kind", ["symlink", "fifo", "setuid", "hardlink"])
def test_artifact_sanitizer_rejects_authority_and_special_files(tmp_path, kind):
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    item = artifact / "output"
    if kind == "symlink":
        item.symlink_to("/tests/reference.txt")
    elif kind == "fifo":
        os.mkfifo(item)
    else:
        item.write_text("untrusted")
        if kind == "setuid":
            item.chmod(0o4755)
        else:
            os.link(item, artifact / "another")
    with pytest.raises(TaskVerifierError, match="artifact contains"):
        _checked_tree(artifact)


async def test_rejected_symlink_is_never_uploaded_or_retained_and_verifier_is_cleaned(tmp_path):
    original = separate_task(tmp_path)
    original.config["artifacts"] = ["/app/output"]
    link = tmp_path / "link"
    link.symlink_to("/tests/reference.txt")
    docker = Docker()
    docker.sources["/app/output"] = link
    with pytest.raises(TaskVerifierError, match="symlink"):
        await prepare_verifier(original, native_image(), CONTAINER, tmp_path / "workspace", tmp_path / "logs", docker)
    assert docker.uploaded == {}
    assert not (tmp_path / "logs/artifacts/app/output").is_symlink()
    assert docker.calls[-1][:3] == ("docker", "rm", "-f")


@pytest.mark.parametrize("artifacts", [
    ["/tests/reference"], ["/logs/verifier/reward.txt"], ["/app/../tests/key"],
    [{"source": "/app/output", "destination": "../escape"}],
    [{"source": "/app/output", "exclude": ["*.tmp"]}],
    [{"source": "/app/output", "service": "database"}],
    ["/app/output", "/app/output/file"],
    [{"source": "/app/one", "destination": "result"}, {"source": "/app/two", "destination": "result"}],
])
def test_unsafe_or_unimplemented_artifact_declarations_are_explicitly_refused(tmp_path, artifacts):
    original = separate_task(tmp_path)
    original.config["artifacts"] = artifacts
    with pytest.raises(TaskVerifierError):
        artifact_specs(original)


async def test_shared_team_cannot_grade_one_agents_private_or_published_artifacts(tmp_path):
    original = separate_task(tmp_path)
    original.config["artifacts"] = ["/app/report"]
    docker = Docker()
    with pytest.raises(TaskVerifierError, match="service state only"):
        await prepare_verifier(original, native_image(), CONTAINER, tmp_path / "workspace", tmp_path / "logs", docker,
                               service_scope="shared")
    assert docker.calls == []
    original.config["artifacts"] = []
    publish = tmp_path / "publish"
    publish.mkdir()
    (publish / "report.txt").write_text("one agent's work")
    docker.sources["/logs/artifacts"] = publish
    with pytest.raises(TaskVerifierError, match="one agent's published artifacts"):
        await prepare_verifier(original, native_image(), CONTAINER, tmp_path / "workspace", tmp_path / "logs", docker,
                               service_scope="shared")


@pytest.mark.parametrize("environment", [{"gpus": 1}, {"storage_mb": 1024}, {"memory_mb": 0},
                                       {"cpus": True}, {"unexpected": 1}, {"os": "windows"},
                                       {"network_mode": "public"}, {"env": {"KEY": "${UNRESOLVED}"}}])
def test_unsupported_verifier_environment_fails_before_docker_work(tmp_path, environment):
    original = separate_task(tmp_path)
    original.config["verifier"]["environment"] = environment
    with pytest.raises(TaskVerifierError):
        verifier_environment(original)


async def test_requested_verifier_resources_require_enforcement(tmp_path):
    docker = Docker()
    docker.cgroups = "none"
    with pytest.raises(TaskVerifierError, match="cannot enforce"):
        await prepare_verifier(separate_task(tmp_path), native_image(), CONTAINER, tmp_path / "workspace",
                               tmp_path / "logs", docker)
    assert not any(call[:2] == ("docker", "create") for call in docker.calls)


async def test_failed_artifact_upload_cleans_the_owned_verifier(tmp_path):
    original = separate_task(tmp_path)
    original.config["artifacts"] = ["/app/report"]
    report = tmp_path / "report"
    report.write_text("result")
    docker = Docker()
    docker.sources["/app/report"] = report
    docker.fail_upload = True
    with pytest.raises(RuntimeError, match="stopped during artifact upload"):
        await prepare_verifier(original, native_image(), CONTAINER, tmp_path / "workspace", tmp_path / "logs", docker)
    assert docker.calls[-1][:3] == ("docker", "rm", "-f")


async def test_failed_preparation_cleanup_is_reported_and_retained(tmp_path):
    class BrokenDocker(Docker):
        async def __call__(self, *args, **kwargs):
            if args[:2] in (("docker", "create"), ("docker", "rm")):
                raise RuntimeError("Docker daemon is unavailable")
            return await super().__call__(*args, **kwargs)

    with pytest.raises(TaskVerifierError, match="cleanup is unverified"):
        await prepare_verifier(separate_task(tmp_path), native_image(), CONTAINER, tmp_path / "workspace",
                               tmp_path / "logs", BrokenDocker())
    receipt = json.loads((tmp_path / "logs/preparation-cleanup.json").read_text())
    assert receipt["status"] == "unverified"
    assert receipt["cleanup_commands"][0][:3] == ["docker", "rm", "-f"]
