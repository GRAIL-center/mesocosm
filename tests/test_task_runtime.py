import asyncio
import json
import pathlib
import subprocess

import pytest
import yaml

from mesocosm.adapters.harbor.task import load_task, verifier_command
from mesocosm.core.manifest import AgentSpec
from mesocosm.env.task_runtime import (
    DockerTaskRuntime,
    TaskEnvironmentError,
    environment_key,
    harness_dockerfile,
    startup_script,
)


def task(tmp_path, config=None):
    root = tmp_path / "native-task"
    (root / "environment").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "instruction.md").write_text("Repair the actual project.\n")
    (root / "tests" / "test.sh").write_text("#!/bin/bash\nset -o pipefail\nexit 0\n")
    out = load_task(root)
    out.config = config or {"environment": {"docker_image": "publisher/task:version", "cpus": 1, "memory_mb": 256}}
    return out


class DockerCommands(DockerTaskRuntime):
    def __init__(self, root, **kwargs):
        config = root / "opencode.json"
        config.write_text('{"model": "internal/model"}')
        compose = root / "compose.yaml"
        compose.write_text(yaml.safe_dump({"name": "native-world", "services": {"agent-1": {
            "build": {"context": ".", "dockerfile": "Dockerfile.agent"},
            "networks": {"sandbox": {"ipv4_address": "172.30.0.10"}},
            "volumes": [f"{root / 'workspace'}:/work", f"{config}:/root/.config/opencode/opencode.json:ro"],
            "environment": {"HTTP_PROXY": "http://proxy:3128"},
        }}}))
        super().__init__(compose, root / "runtime", **kwargs)
        self.calls = []
        self.cgroups = "cgroupfs"
        self.verifier_error = False

    async def command(self, *args, **kwargs):
        self.calls.append(args)
        if args[:2] == ("docker", "info"):
            return json.dumps({"CgroupDriver": self.cgroups, "Warnings": []}).encode()
        if args[:3] == ("docker", "image", "inspect"):
            return json.dumps([{"Id": "sha256:native-base", "Config": {
                "User": "root", "WorkingDir": "/app/native-project", "Entrypoint": [],
                "Cmd": ["sleep", "infinity"], "Env": ["NATIVE_SETTING=yes"],
            }}]).encode()
        if args[:2] == ("docker", "create"):
            return b"staging-container\n"
        if args[:2] == ("docker", "cp"):
            (pathlib.Path(args[-1]) / "native-source.txt").write_text("source from the task image")
        if args[:2] == ("docker", "compose") and "ps" in args:
            return b"actual-agent-container\n"
        if args[:2] == ("docker", "commit"):
            return b"sha256:private-rootfs-snapshot\n"
        if args[:2] == ("docker", "run") and "--name" in args:
            if self.verifier_error:
                raise TaskEnvironmentError("native verifier exited 9")
            log_mount = next(a for a in args if a.endswith(":/logs"))
            (pathlib.Path(log_mount[:-6]) / "verifier" / "reward.txt").write_text("0.5\n")
            return b"native test output\n"
        return b""


async def test_native_files_are_staged_before_agent_mount_and_native_settings_survive(tmp_path):
    runtime = DockerCommands(tmp_path)
    native = task(tmp_path)
    receipt = await runtime.prepare(AgentSpec(id="agent-1"), native, tmp_path / "workspace", reset=True)
    assert (tmp_path / "workspace" / "native-source.txt").read_text() == "source from the task image"
    rendered = yaml.safe_load(next((tmp_path / "runtime").rglob("compose.yaml")).read_text())
    agent = rendered["services"]["agent-1"]
    assert "build" not in agent and agent["image"].startswith("mesocosm-task-agent:")
    assert agent["working_dir"] == "/app/native-project" and agent["user"] == "root"
    assert any(v.endswith(":/app/native-project") for v in agent["volumes"])
    assert not any(v.endswith(":/work") for v in agent["volumes"])
    assert agent["cpus"] == 1 and agent["mem_limit"] == "256m"
    assert agent["networks"] == {"sandbox": {"ipv4_address": "172.30.0.10"}}
    assert receipt["base_image_id"] == "sha256:native-base"
    assert receipt["applied_resources"] == {"cpus": 1, "mem_limit": "256m"}
    cp = next(i for i, c in enumerate(runtime.calls) if c[:2] == ("docker", "cp"))
    up = next(i for i, c in enumerate(runtime.calls) if c[:2] == ("docker", "compose") and "up" in c)
    assert cp < up


async def test_verifier_uses_private_native_rootfs_and_original_bash_without_agent_proxy(tmp_path):
    runtime = DockerCommands(tmp_path, verifier_network="bridge")
    native = task(tmp_path)
    workspace = tmp_path / "workspace"
    await runtime.prepare(AgentSpec(id="agent-1"), native, workspace, reset=True)
    logs = tmp_path / "logs"
    reward = await runtime.verify(native, workspace, logs)
    assert reward.value == 0.5
    cmd = next(c for c in runtime.calls if c[:2] == ("docker", "run") and "--name" in c)
    assert cmd[cmd.index("--network") + 1] == "bridge"
    assert cmd[cmd.index("--entrypoint") + 1] == "/bin/bash"
    assert cmd[cmd.index("--workdir") + 1] == "/app/native-project"
    assert "HTTP_PROXY=" in cmd and "OPENAI_API_KEY=" in cmd
    assert any(c[:2] == ("docker", "commit") and c[2] == "actual-agent-container" for c in runtime.calls)
    evidence = json.loads((logs / "environment.json").read_text())
    assert evidence["coverage"] == "filesystem_snapshot_only"
    assert evidence["verifier_image_id"] == "sha256:private-rootfs-snapshot"
    assert "verifier_only_internet" in evidence["adaptations"]


async def test_native_verifier_failure_is_not_a_reward_and_snapshot_is_removed(tmp_path):
    runtime = DockerCommands(tmp_path)
    native = task(tmp_path)
    workspace = tmp_path / "workspace"
    await runtime.prepare(AgentSpec(id="agent-1"), native, workspace, reset=True)
    runtime.verifier_error = True
    with pytest.raises(TaskEnvironmentError, match="exited 9"):
        await runtime.verify(native, workspace, tmp_path / "logs")
    assert any(c[:4] == ("docker", "image", "rm", "-f") for c in runtime.calls)
    assert "exited 9" in (tmp_path / "logs" / "verifier.log").read_text()


async def test_memory_carry_cannot_silently_change_native_environment(tmp_path):
    runtime = DockerCommands(tmp_path)
    native = task(tmp_path)
    workspace = tmp_path / "workspace"
    await runtime.prepare(AgentSpec(id="agent-1"), native, workspace, reset=True)
    native.config["environment"]["docker_image"] = "publisher/other-task:version"
    with pytest.raises(TaskEnvironmentError, match="carried harness memory"):
        await runtime.prepare(AgentSpec(id="agent-1"), native, workspace, reset=False)


async def test_unsupported_cgroups_and_storage_require_explicit_adapted_opt_in(tmp_path):
    runtime = DockerCommands(tmp_path)
    native = task(tmp_path)
    runtime.cgroups = "none"
    with pytest.raises(TaskEnvironmentError, match="cannot enforce CPU/memory"):
        await runtime.native_image(native)
    runtime.allow_unenforced_resources = True
    native.config["environment"]["storage_mb"] = 10240
    receipt = await runtime.prepare(AgentSpec(id="agent-1"), native, tmp_path / "workspace", reset=True)
    assert receipt["applied_resources"] == {}
    assert set(receipt["adaptations"]) >= {"storage_quota_unenforced", "cpu_memory_limits_unenforced"}


async def test_task_without_native_environment_fails_before_docker_work(tmp_path):
    runtime = DockerCommands(tmp_path)
    native = task(tmp_path, {"environment": {}})
    with pytest.raises(TaskEnvironmentError, match="declare environment"):
        await runtime.native_image(native)
    assert not runtime.calls


def test_environment_content_changes_invalidate_built_image(tmp_path):
    native = task(tmp_path, {"environment": {}})
    dockerfile = native.path / "environment" / "Dockerfile"
    dockerfile.write_text("FROM python:3.13-slim\nWORKDIR /project\n")
    first = environment_key(native, "1.19.3")
    (native.path / "environment" / "real-source.py").write_text("version = 2\n")
    assert environment_key(native, "1.19.3") != first


def test_harness_preserves_npm_declared_executable_and_checks_it_at_build_time():
    dockerfile = harness_dockerfile("native-task", "1.19.3", "1000")
    assert "npm install -g --prefix /opt/coding-harness" in dockerfile
    assert "COPY --from=coding_harness /opt/coding-harness /opt/coding-harness" in dockerfile
    assert "/node_modules/opencode-ai/bin/opencode" not in dockerfile
    assert "/usr/local/bin/opencode --version" in dockerfile


def test_runtime_wrapper_changes_invalidate_environment_image(tmp_path, monkeypatch):
    native = task(tmp_path)
    before = environment_key(native, "1.19.3")
    monkeypatch.setattr("mesocosm.env.task_runtime.harness_dockerfile", lambda *args: "different harness layer")
    assert environment_key(native, "1.19.3") != before


def test_task_compose_is_rejected_instead_of_discarding_its_sidecars(tmp_path):
    native = task(tmp_path)
    (native.path / "environment" / "docker-compose.yaml").write_text("services: {}\n")
    with pytest.raises(TaskEnvironmentError, match="Compose environments"):
        environment_key(native, "1.19.3")


@pytest.mark.parametrize("seconds", ['"soon"', "0", "-1", "nan", "true"])
def test_invalid_round_deadlines_fail_during_load(tmp_path, seconds):
    native = task(tmp_path)
    (native.path / "task.toml").write_text(f'[[rounds]]\nprompt = "answer"\nseconds = {seconds}\n')
    with pytest.raises(ValueError, match="positive finite"):
        load_task(native.path)


def test_verifier_preserves_native_workdir_shebang_and_environment(tmp_path):
    native = task(tmp_path)
    native.config["verifier"] = {"env": {"EXPECTED": "native"}}
    command = verifier_command(native, workspace=tmp_path / "ws", logs_dir=tmp_path / "logs",
                               image="native-rootfs", workdir="/repo/site")
    assert command[command.index("--entrypoint") + 1] == "/bin/bash"
    assert f"{tmp_path / 'ws'}:/repo/site" in command and "EXPECTED=native" in command


def test_failing_native_startup_stops_the_harness(tmp_path):
    harness = tmp_path / "harness"
    harness.write_text("#!/bin/sh\nexec sleep 30\n")
    harness.chmod(0o755)
    script = tmp_path / "startup.sh"
    script.write_text(startup_script({"Cmd": ["/bin/sh", "-c", "sleep .1; exit 7"]})
                      .replace("/usr/local/bin/opencode", str(harness)))
    result = subprocess.run(["/bin/sh", str(script)], timeout=5, capture_output=True)
    assert result.returncode == 7


async def test_cancelling_docker_work_kills_and_reaps_the_cli(tmp_path, monkeypatch):
    runtime = DockerCommands(tmp_path)
    started = asyncio.Event()

    class Process:
        killed = False
        waited = False

        async def communicate(self):
            started.set()
            await asyncio.Future()

        def kill(self):
            self.killed = True

        async def wait(self):
            self.waited = True

    process = Process()

    async def create(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    running = asyncio.create_task(DockerTaskRuntime.command(runtime, "docker", "build", "task"))
    await started.wait()
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert process.killed and process.waited


@pytest.mark.parametrize("version,user,account", [("1.19.3", "root", "root"), ("1.19.3", "1000:1000", "1000")])
def test_native_harness_preinstalls_matching_sdk_in_custom_and_native_global_config(version, user, account):
    dockerfile = harness_dockerfile("native-task", version, user)
    assert f"--save-exact @opencode-ai/plugin@{version}" in dockerfile
    assert "--ignore-scripts --no-audit --no-fund" in dockerfile
    assert "COPY --from=coding_harness /opt/harness-config /etc/opencode" in dockerfile
    assert f"getent passwd {account}" in dockerfile
    assert '${XDG_CONFIG_HOME:-${HOME}/.config}/opencode' in dockerfile
    assert '$task_home/.config/opencode' in dockerfile
    assert "ENV XDG_CONFIG_HOME" not in dockerfile, "the native application's config environment survives"
    assert dockerfile.endswith(f"USER {user}\n")
    assert dockerfile.index("npm install --prefix /opt/harness-config") < dockerfile.index("FROM native-task")


def test_bootstrap_harness_sdk_is_pinned_to_the_same_version():
    dockerfile = (pathlib.Path(__file__).resolve().parents[1] / "spike/docker/Dockerfile.agent").read_text()
    assert 'opencode-ai@${OPENCODE_VERSION}' in dockerfile
    assert 'npm install --prefix /root/.config/opencode' in dockerfile
    assert '@opencode-ai/plugin@${OPENCODE_VERSION}' in dockerfile
    assert '--save-exact' in dockerfile and '--ignore-scripts' in dockerfile
