import asyncio
import hashlib
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
    def __init__(self, root, agents=1, **kwargs):
        config = root / "opencode.json"
        config.write_text('{"model": "internal/model"}')
        compose = root / "compose.yaml"
        names = [f"agent-{index + 1}" for index in range(agents)]
        compose.write_text(yaml.safe_dump({"name": "native-world", "services": {name: {
            "build": {"context": ".", "dockerfile": "Dockerfile.agent"},
            "networks": {"sandbox" if agents == 1 else f"sandbox-{name}": {"ipv4_address": f"172.30.{i}.10"}},
            "volumes": [f"{root / 'workspace'}:/work", f"{config}:/root/.config/opencode/opencode.json:ro"],
            "environment": {"HTTP_PROXY": "http://proxy:3128", "OPENCODE_SERVER_PASSWORD": f"secret-{name}"},
            "healthcheck": {"test": ["CMD", "curl", "-f", "http://localhost:4096/global/health"]},
        } for i, name in enumerate(names)}, "networks": {
            "sandbox" if agents == 1 else f"sandbox-{name}": {"internal": True} for name in names}}))
        kwargs.setdefault("agent_ids", names)
        super().__init__(compose, root / "runtime", **kwargs)
        self.calls = []
        self.cgroups = "cgroupfs"
        self.verifier_error = False
        self.containers = {}
        self.volumes = {}
        self.pause_error = False
        self.up_error = False
        self.cleanup_error = False
        self.probe_private = True
        self.probe_host_available = True

    def _probe_host(self, pid):
        if not self.probe_host_available:
            raise OSError("control host cannot inspect Docker PID namespace")
        return "test-kernel", "pid:[1]", "pid:[2]" if self.probe_private else "pid:[1]"

    def container(self, service):
        cid = hashlib.sha256(service.encode()).hexdigest()
        return {"Id": cid, "Config": {"Labels": {"com.docker.compose.project": "native-world",
            "com.docker.compose.service": service}}, "State": {"Running": True, "Paused": False,
            "Restarting": False, "Pid": 123, "StartedAt": "2026-10-23T00:00:00Z", "ExitCode": 0,
            "Health": {"Status": "healthy"}}, "HostConfig": {"PidMode": "", "Privileged": False,
                "NetworkMode": "none", "CapAdd": [], "RestartPolicy": {"Name": "no"}}}

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
        if args[:3] == ("docker", "run", "-d"):
            service = next(arg.removeprefix("com.docker.compose.service=") for arg in args
                           if arg.startswith("com.docker.compose.service="))
            item = self.container(service)
            self.containers[item["Id"]] = item
            return item["Id"].encode()
        if args[:2] == ("docker", "cp"):
            if ":/logs/artifacts" in args[-2]:
                pathlib.Path(args[-1]).mkdir()
                return b""
            (pathlib.Path(args[-1]) / "native-source.txt").write_text("source from the task image")
        if args[:2] == ("docker", "compose"):
            config = yaml.safe_load(pathlib.Path(args[args.index("-f") + 1]).read_text())
            if "config" in args:
                return json.dumps(config).encode()
            if "up" in args:
                selected = [arg for arg in args[args.index("up") + 1:] if arg in config["services"]]
                for service in selected:
                    item = self.container(service)
                    self.containers[item["Id"]] = item
                for volume in config.get("volumes") or {}:
                    self.volumes[f"native-world_{volume}"] = volume
                if self.up_error and any(name.startswith("agent-") for name in selected):
                    raise TaskEnvironmentError("task main healthcheck failed after creation")
                return b""
            if "ps" in args:
                return "\n".join(cid for cid, item in self.containers.items()
                                 if item["Config"]["Labels"]["com.docker.compose.service"] == args[-1]).encode()
        if args[:3] == ("docker", "container", "inspect"):
            return json.dumps([self.containers[args[3]]]).encode()
        if args[:3] == ("docker", "container", "exec"):
            namespace = "pid:[2]" if self.probe_private else "pid:[1]"
            return f"{namespace}\n{namespace}\ntest-kernel\n".encode()
        if args[:3] == ("docker", "container", "pause"):
            if not self.pause_error:
                self.containers[args[3]]["State"]["Paused"] = True
            return b""
        if args[:3] == ("docker", "container", "unpause"):
            self.containers[args[3]]["State"]["Paused"] = False
            return b""
        if args[:3] == ("docker", "container", "ls"):
            cid = args[-1].removeprefix("id=")
            return cid.encode() if cid in self.containers else b""
        if args[:3] == ("docker", "container", "rm"):
            if self.cleanup_error:
                raise TaskEnvironmentError("container removal failed")
            self.containers.pop(args[-1], None)
            return b""
        if args[:3] == ("docker", "volume", "ls"):
            name = args[-1].removeprefix("name=^").removesuffix("$")
            return name.encode() if name in self.volumes else b""
        if args[:3] == ("docker", "volume", "inspect"):
            return json.dumps([{"Labels": {"com.docker.compose.project": "native-world",
                "com.docker.compose.volume": self.volumes[args[-1]]}}]).encode()
        if args[:3] == ("docker", "volume", "rm"):
            self.volumes.pop(args[-1], None)
            return b""
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
    assert f"com.docker.compose.project={runtime.project}" in cmd
    staging = [call for call in runtime.calls if call[:2] == ("docker", "create") or
               (call[:2] == ("docker", "run") and "--name" not in call)]
    assert staging and all(f"com.docker.compose.project={runtime.project}" in call for call in staging)
    assert any(c[:2] == ("docker", "commit") and c[2] == hashlib.sha256(b"agent-1").hexdigest() for c in runtime.calls)
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


def test_empty_task_compose_is_rejected_instead_of_substituting_a_generic_environment(tmp_path):
    native = task(tmp_path)
    (native.path / "environment" / "docker-compose.yaml").write_text("services: {}\n")
    with pytest.raises(TaskEnvironmentError, match="declares no services"):
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


def compose_task(tmp_path, *, shared=False):
    native = task(tmp_path, {"environment": {"cpus": 1},
                             "verifier": {"environment_mode": "separate", "timeout_sec": 2}})
    specification = {
        "services": {
            "main": {
                "image": "publisher/real-application:version", "working_dir": "/app/native-project",
                "user": "1000:1000", "command": ["python", "app.py"],
                "environment": {"DATABASE_URL": "postgres://db:5432/application", "LITERAL": "$$HOME"},
                "healthcheck": {"test": ["CMD", "curl", "-f", "http://localhost:9000/health"],
                                "interval": "2s", "retries": 5},
                "depends_on": {"db": {"condition": "service_healthy"}},
            },
            "db": {"image": "postgres:17", "volumes": ["data:/var/lib/postgresql/data"],
                   "healthcheck": {"test": ["CMD", "pg_isready"], "interval": "2s"}},
        }, "volumes": {"data": {}},
    }
    (native.path / "environment" / "compose.yaml").write_text(yaml.safe_dump(specification))
    return native


@pytest.fixture
def separate_grader(monkeypatch):
    from mesocosm.adapters.harbor.task import Reward

    calls = []

    async def grade(self, task, group, agent_id, workspace, logs_dir, script):
        assert all(self.containers[cid]["State"]["Paused"] for cid in group.containers.values())
        assert all(self.containers[cid]["State"]["Running"]
                   for cid in self.containers if self.containers[cid]["Config"]["Labels"][
                       "com.docker.compose.service"].startswith("task-"))
        calls.append((agent_id, group.key))
        if self.verifier_error:
            raise TaskEnvironmentError("original service verifier failed")
        logs_dir.mkdir(parents=True, exist_ok=True)
        (logs_dir / "verifier").mkdir()
        (logs_dir / "verifier" / "reward.txt").write_text("0.75\n")
        (logs_dir / "verifier.log").write_text("original service verifier output\n")
        (logs_dir / "environment.json").write_text(json.dumps({"coverage": "fresh_verifier_with_artifacts"}))
        return Reward(0.75, "reward.txt", "0.75")

    monkeypatch.setattr(DockerCommands, "_measure", grade)
    return calls


async def test_real_compose_main_and_sidecars_keep_native_settings_and_protected_auth(tmp_path):
    runtime = DockerCommands(tmp_path)
    native = compose_task(tmp_path)
    receipt = await runtime.prepare(AgentSpec(id="agent-1"), native, tmp_path / "workspace", reset=True)
    rendered = yaml.safe_load(runtime.runtime_file.read_text())
    main = rendered["services"]["agent-1"]
    db = next(name for name in rendered["services"] if name.startswith("task-"))
    assert main["user"] == "1000:1000"
    assert main["working_dir"] == "/app/native-project"
    assert main["environment"]["DATABASE_URL"] == "postgres://db:5432/application"
    assert main["environment"]["OPENCODE_SERVER_PASSWORD"] == "secret-agent-1"
    assert json.loads(main["environment"]["OPENCODE_CONFIG_CONTENT"]) == {"compaction": {"auto": False, "prune": False}}
    assert "OPENCODE_SERVER_PASSWORD" not in rendered["services"][db].get("environment", {})
    assert main["depends_on"][db] == {"condition": "service_healthy"}
    assert "localhost:9000/health" in main["healthcheck"]["test"][1]
    assert "localhost:4096/global/health" in main["healthcheck"]["test"][1]
    assert main["healthcheck"]["interval"] == "2s"
    assert main["networks"] == {"sandbox": {"ipv4_address": "172.30.0.10", "aliases": ["main"]}}
    assert rendered["services"][db]["networks"] == {"sandbox": {"aliases": ["db"]}}
    assert receipt["task_services"]["scope"] == "isolated"
    startup = next((tmp_path / "runtime").rglob("start.sh")).read_text()
    assert "python app.py" in startup
    assert not any(name == "main" for name in rendered["services"])
    await runtime.finish()
    assert not runtime.containers and not runtime.volumes
    assert runtime.rendered["services"] == runtime.base_compose["services"]
    assert not runtime.rendered["volumes"]


async def test_n3_isolated_sidecars_use_distinct_state_and_grade_independently(tmp_path, separate_grader):
    runtime = DockerCommands(tmp_path, agents=3)
    native = compose_task(tmp_path)
    workspaces = [tmp_path / f"workspace-{i}" for i in range(1, 4)]
    receipts = await asyncio.gather(*(runtime.prepare(AgentSpec(id=f"agent-{i}"), native, workspaces[i - 1], reset=True)
                                      for i in range(1, 4)))
    assert len({receipt["task_services"]["group_id"] for receipt in receipts}) == 3
    assert len(runtime.volumes) == 3
    for index in range(1, 4):
        reward = await runtime.verify(native, workspaces[index - 1], tmp_path / f"logs-{index}")
        assert reward.value == 0.75
        assert len(runtime.volumes) == 3 - index
    assert len(separate_grader) == 3 and not runtime.containers


async def test_n3_shared_verifier_waits_then_measures_once_and_copies_one_team_receipt(tmp_path, separate_grader):
    runtime = DockerCommands(tmp_path, agents=3, task_services="shared")
    native = compose_task(tmp_path, shared=True)
    workspaces = [tmp_path / f"workspace-{i}" for i in range(1, 4)]
    receipts = await asyncio.gather(*(runtime.prepare(AgentSpec(id=f"agent-{i}"), native, workspaces[i - 1], reset=True)
                                      for i in range(1, 4)))
    assert len({receipt["task_services"]["group_id"] for receipt in receipts}) == 1
    assert len(runtime.volumes) == 1
    for index in range(1, 4):
        service = runtime.rendered["services"][f"agent-{index}"]
        assert len(service["networks"]) == 1
        assert next(iter(service["networks"].values()))["aliases"] == []
    waits = [asyncio.create_task(runtime.verify(native, workspaces[i], tmp_path / f"logs-{i}")) for i in range(2)]
    await asyncio.sleep(0)
    assert not separate_grader and not any(wait.done() for wait in waits)
    waits.append(asyncio.create_task(runtime.verify(native, workspaces[2], tmp_path / "logs-2")))
    rewards = await asyncio.wait_for(asyncio.gather(*waits), 2)
    assert [reward.value for reward in rewards] == [0.75] * 3
    assert len(separate_grader) == 1
    metadata = [json.loads((tmp_path / f"logs-{i}" / "environment.json").read_text()) for i in range(3)]
    assert len({item["measurement_id"] for item in metadata}) == 1
    assert all(item["measurement_scope"] == "team" and item["participant_ids"] == runtime.agent_ids for item in metadata)
    assert not runtime.containers and not runtime.volumes and not runtime.active
    assert runtime.rendered["services"] == runtime.base_compose["services"]


async def test_shared_participant_failure_releases_waiters_and_finish_removes_real_groups(tmp_path, separate_grader):
    runtime = DockerCommands(tmp_path, agents=3, task_services="shared")
    native = compose_task(tmp_path)
    workspaces = [tmp_path / f"workspace-{i}" for i in range(1, 4)]
    for i, workspace in enumerate(workspaces, 1):
        await runtime.prepare(AgentSpec(id=f"agent-{i}"), native, workspace, reset=True)
    first = asyncio.create_task(runtime.verify(native, workspaces[0], tmp_path / "logs-first"))
    second = asyncio.create_task(runtime.verify(native, workspaces[1], tmp_path / "logs-second"))
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    with pytest.raises(TaskEnvironmentError, match="interrupted"):
        await asyncio.wait_for(second, 1)
    assert not separate_grader
    await runtime.finish()
    assert not runtime.containers and not runtime.volumes


async def test_shared_original_verifier_failure_is_unknown_for_all_participants(tmp_path, separate_grader):
    runtime = DockerCommands(tmp_path, agents=3, task_services="shared")
    native = compose_task(tmp_path)
    workspaces = [tmp_path / f"workspace-{i}" for i in range(1, 4)]
    for i, workspace in enumerate(workspaces, 1):
        await runtime.prepare(AgentSpec(id=f"agent-{i}"), native, workspace, reset=True)
    runtime.verifier_error = True
    errors = await asyncio.wait_for(asyncio.gather(*(runtime.verify(native, workspace, tmp_path / f"logs-{i}")
                                                     for i, workspace in enumerate(workspaces)), return_exceptions=True), 2)
    assert all(isinstance(error, TaskEnvironmentError) for error in errors)
    assert len(separate_grader) == 1
    await runtime.finish()
    assert not runtime.containers


async def test_failed_main_startup_still_leaves_exact_cleanup_inventory(tmp_path):
    runtime = DockerCommands(tmp_path)
    native = compose_task(tmp_path)
    runtime.up_error = True
    with pytest.raises(TaskEnvironmentError, match="after creation"):
        await runtime.prepare(AgentSpec(id="agent-1"), native, tmp_path / "workspace", reset=True)
    assert len(runtime.containers) == 2
    await runtime.finish()
    assert not runtime.containers and not runtime.volumes
    assert runtime.rendered["services"] == runtime.base_compose["services"]


async def test_unconfirmed_pause_never_runs_original_verifier(tmp_path, separate_grader):
    runtime = DockerCommands(tmp_path)
    native = compose_task(tmp_path)
    await runtime.prepare(AgentSpec(id="agent-1"), native, tmp_path / "workspace", reset=True)
    runtime.pause_error = True
    with pytest.raises(TaskEnvironmentError, match="quiesced"):
        await runtime.verify(native, tmp_path / "workspace", tmp_path / "logs")
    assert not separate_grader and not runtime.containers


async def test_owned_container_mismatch_prevents_cleanup_mutation(tmp_path):
    runtime = DockerCommands(tmp_path)
    native = task(tmp_path)
    await runtime.prepare(AgentSpec(id="agent-1"), native, tmp_path / "workspace", reset=True)
    cid = next(iter(runtime.containers))
    runtime.containers[cid]["Config"]["Labels"]["com.docker.compose.project"] = "another-project"
    with pytest.raises(TaskEnvironmentError, match="project/service"):
        await runtime.finish()
    assert cid in runtime.containers
    assert not any(call[:3] == ("docker", "container", "rm") for call in runtime.calls)


async def test_memory_carry_retains_sidecars_and_resumes_only_at_next_prepare(tmp_path, separate_grader):
    runtime = DockerCommands(tmp_path, memory="carry")
    native = compose_task(tmp_path)
    workspace = tmp_path / "workspace"
    await runtime.prepare(AgentSpec(id="agent-1"), native, workspace, reset=True)
    await runtime.verify(native, workspace, tmp_path / "logs")
    cid = runtime.active[str(workspace)][1]
    assert runtime.containers[cid]["State"]["Paused"] is True and len(runtime.volumes) == 1
    await runtime.prepare(AgentSpec(id="agent-1"), native, workspace, reset=False)
    assert runtime.containers[cid]["State"]["Paused"] is False and len(runtime.volumes) == 1
    await runtime.finish()
    assert not runtime.containers and not runtime.volumes


@pytest.mark.parametrize("key", ["OPENCODE_SERVER_PASSWORD", "OPENCODE_CONFIG_DIR", "OPENCODE_CONFIG_CONTENT", "MESOCOSM_CONTROL_KEY"])
async def test_native_task_cannot_override_controller_authentication(tmp_path, key):
    runtime = DockerCommands(tmp_path)
    native = compose_task(tmp_path)
    native.config["environment"]["env"] = {key: "task-controlled"}
    with pytest.raises(TaskEnvironmentError, match="override harness"):
        await runtime.prepare(AgentSpec(id="agent-1"), native, tmp_path / "workspace", reset=True)
    assert not runtime.containers


async def test_compose_sidecars_refuse_unauthenticated_harness_before_build(tmp_path):
    runtime = DockerCommands(tmp_path)
    runtime.base_compose["services"]["agent-1"]["environment"].pop("OPENCODE_SERVER_PASSWORD")
    native = compose_task(tmp_path)
    with pytest.raises(TaskEnvironmentError, match="authenticated OpenCode"):
        await runtime.native_image(native)
    assert not any(call[:2] == ("docker", "build") for call in runtime.calls)


async def test_compose_resource_limits_cannot_silently_use_colab_adaptation(tmp_path):
    runtime = DockerCommands(tmp_path, allow_unenforced_resources=True)
    runtime.cgroups = "none"
    native = compose_task(tmp_path)
    path = native.path / "environment" / "compose.yaml"
    source = yaml.safe_load(path.read_text())
    source["services"]["db"]["mem_limit"] = "256m"
    path.write_text(yaml.safe_dump(source))
    with pytest.raises(TaskEnvironmentError, match="Compose resource limits"):
        await runtime.native_image(native)


async def test_task_named_volume_cannot_erase_private_agent_workspace(tmp_path):
    runtime = DockerCommands(tmp_path)
    native = compose_task(tmp_path)
    path = native.path / "environment" / "compose.yaml"
    source = yaml.safe_load(path.read_text())
    source["services"]["main"]["volumes"] = ["data:/app/native-project"]
    path.write_text(yaml.safe_dump(source))
    with pytest.raises(TaskEnvironmentError, match="private agent workspace"):
        await runtime.prepare(AgentSpec(id="agent-1"), native, tmp_path / "workspace", reset=True)
    assert not runtime.containers


async def test_cleanup_attempts_every_owned_group_when_first_container_fails(tmp_path):
    runtime = DockerCommands(tmp_path, agents=3)
    native = compose_task(tmp_path)
    for index in range(1, 4):
        await runtime.prepare(AgentSpec(id=f"agent-{index}"), native, tmp_path / f"workspace-{index}", reset=True)
    runtime.calls.clear()
    runtime.cleanup_error = True
    with pytest.raises(TaskEnvironmentError, match="cleanup incomplete"):
        await runtime.finish()
    removals = [call for call in runtime.calls if call[:3] == ("docker", "container", "rm")]
    assert len(removals) == 6
    assert all("-v" in call for call in removals)
    assert not any(group.released for group in runtime.groups.values())
    runtime.cleanup_error = False
    await runtime.finish()
    assert not runtime.containers


async def test_shared_artifacts_from_nonleader_are_rejected_before_any_team_score(tmp_path, separate_grader):
    runtime = DockerCommands(tmp_path, agents=3, task_services="shared")
    native = compose_task(tmp_path)
    workspaces = [tmp_path / f"workspace-{i}" for i in range(1, 4)]
    for i, workspace in enumerate(workspaces, 1):
        await runtime.prepare(AgentSpec(id=f"agent-{i}"), native, workspace, reset=True)
    original_command = runtime.command
    second = hashlib.sha256(b"agent-2").hexdigest()

    async def command(*args, **kwargs):
        if args[:2] == ("docker", "cp") and args[-2] == f"{second}:/logs/artifacts":
            pathlib.Path(args[-1]).mkdir()
            (pathlib.Path(args[-1]) / "result.txt").write_text("published result")
            return b""
        return await original_command(*args, **kwargs)

    runtime.command = command
    results = await asyncio.wait_for(asyncio.gather(*(runtime.verify(native, workspace, tmp_path / f"logs-{i}")
                                                      for i, workspace in enumerate(workspaces)), return_exceptions=True), 2)
    assert all(isinstance(result, TaskEnvironmentError) and "published artifacts" in str(result) for result in results)
    assert not separate_grader
    await runtime.finish()


@pytest.mark.parametrize("field,value", [("user", "root"), ("network_mode", "host"),
                                         ("allowed_hosts", ["example.org"]), ("unknown", True)])
async def test_snapshot_verifier_does_not_ignore_unsupported_declarations(tmp_path, field, value):
    runtime = DockerCommands(tmp_path)
    native = task(tmp_path)
    native.config["verifier"] = {field: value}
    with pytest.raises(TaskEnvironmentError, match="unsupported snapshot verifier"):
        await runtime.native_image(native)
    assert not runtime.calls


async def test_main_platform_is_used_for_native_pull_harness_build_and_workspace_staging(tmp_path):
    runtime = DockerCommands(tmp_path)
    native = compose_task(tmp_path)
    path = native.path / "environment" / "compose.yaml"
    source = yaml.safe_load(path.read_text())
    source["services"]["main"]["platform"] = "linux/amd64"
    path.write_text(yaml.safe_dump(source))
    await runtime.prepare(AgentSpec(id="agent-1"), native, tmp_path / "workspace", reset=True)
    for verb in ("pull", "build", "create", "run"):
        commands = [call for call in runtime.calls if call[:2] == ("docker", verb)]
        assert commands and all("--platform" in call and "linux/amd64" in call for call in commands)
    assert runtime.rendered["services"]["agent-1"]["platform"] == "linux/amd64"
    await runtime.finish()


async def test_separate_verifier_receives_owned_internal_network_and_all_cleanup_runs(tmp_path, monkeypatch):
    from mesocosm.env.task_verifier import VerifierPlan

    runtime = DockerCommands(tmp_path)
    native = compose_task(tmp_path)
    workspace = tmp_path / "workspace"
    logs = tmp_path / "logs"
    await runtime.prepare(AgentSpec(id="agent-1"), native, workspace, reset=True)
    prepared = []
    original_command = runtime.command

    async def prepare(task, image, container, selected_workspace, logs_dir, command, **kwargs):
        assert runtime.containers[container]["State"]["Paused"]
        assert selected_workspace == workspace
        prepared.append(kwargs)
        logs_dir.mkdir()
        (logs_dir / "verifier").mkdir()
        return VerifierPlan(["docker", "original-verifier"], {"environment_mode": "separate"},
                            [("docker", "image", "rm", "-f", "temporary-one"),
                             ("docker", "image", "rm", "-f", "temporary-two")])

    async def command(*args, **kwargs):
        if args == ("docker", "original-verifier"):
            (logs / "verifier" / "reward.txt").write_text("1\n")
            return b"original verifier ran"
        return await original_command(*args, **kwargs)

    monkeypatch.setattr("mesocosm.env.task_verifier.prepare_verifier", prepare)
    runtime.command = command
    reward = await runtime.verify(native, workspace, logs)
    assert reward.value == 1
    assert prepared == [{"network": "native-world_sandbox", "service_scope": "isolated", "script": "test.sh",
                         "project": "native-world"}]
    assert not runtime.verifier_cleanup
    assert json.loads((logs / "cleanup.json").read_text())["confirmed"] is True
    assert (logs / "task-services.log").exists()
    assert not runtime.containers


async def test_private_pid_probe_precedes_service_and_authenticated_main_launch(tmp_path):
    runtime = DockerCommands(tmp_path)
    native = compose_task(tmp_path)
    receipt = await runtime.prepare(AgentSpec(id="agent-1"), native, tmp_path / "workspace", reset=True)
    probe_index, probe = next((i, call) for i, call in enumerate(runtime.calls) if call[:3] == ("docker", "run", "-d"))
    assert probe[probe.index("--network") + 1] == "none"
    assert probe[probe.index("--entrypoint") + 1] == "/bin/sh"
    assert "--no-healthcheck" in probe
    assert "-e" not in probe and "-v" not in probe and "--env" not in probe and "--volume" not in probe
    assert not any("secret-agent" in value for value in probe)
    first_up = next(i for i, call in enumerate(runtime.calls) if call[:2] == ("docker", "compose") and "up" in call)
    probe_removal = next(i for i, call in enumerate(runtime.calls) if call[:3] == ("docker", "container", "rm"))
    assert probe_index < probe_removal < first_up
    proof = receipt["task_service_containment"]
    assert proof["status"] == "supported" and proof["private_pid_namespace"] is True
    assert proof["container_id"] not in runtime.containers and not runtime.probes
    retained = next((tmp_path / "runtime").rglob("task-containment.json")).read_text()
    assert json.loads(retained) == proof and "secret-agent" not in retained
    await runtime.finish()


@pytest.mark.parametrize("host_available,private,status", [(True, False, "unsupported"), (False, True, "unverified")])
async def test_shared_or_unverified_procfs_rejects_services_even_with_resource_adaptation(
    tmp_path, host_available, private, status
):
    runtime = DockerCommands(tmp_path, allow_unenforced_resources=True)
    runtime.probe_host_available, runtime.probe_private = host_available, private
    native = compose_task(tmp_path)
    with pytest.raises(TaskEnvironmentError, match="requires verified private PID containment"):
        await runtime.prepare(AgentSpec(id="agent-1"), native, tmp_path / "workspace", reset=True)
    assert not any(call[:2] == ("docker", "compose") and "up" in call for call in runtime.calls)
    assert not runtime.containers and not runtime.probes and not runtime.task_containment
    proof = json.loads(next((tmp_path / "runtime").rglob("task-containment.json")).read_text())
    assert proof["status"] == status
    await runtime.finish()


async def test_probe_cleanup_failure_is_retained_for_finish_and_never_cached_as_safe(tmp_path):
    runtime = DockerCommands(tmp_path)
    runtime.cleanup_error = True
    native = compose_task(tmp_path)
    with pytest.raises(TaskEnvironmentError, match="removal failed"):
        await runtime.prepare(AgentSpec(id="agent-1"), native, tmp_path / "workspace", reset=True)
    assert runtime.probes and runtime.containers and not runtime.task_containment
    assert not any(call[:2] == ("docker", "compose") and "up" in call for call in runtime.calls)
    runtime.cleanup_error = False
    await runtime.finish()
    assert not runtime.probes and not runtime.containers


async def test_n3_groups_reuse_successful_containment_probe_for_same_immutable_base(tmp_path):
    runtime = DockerCommands(tmp_path, agents=3)
    native = compose_task(tmp_path)
    receipts = []
    for index in range(1, 4):
        receipts.append(await runtime.prepare(AgentSpec(id=f"agent-{index}"), native,
                                              tmp_path / f"workspace-{index}", reset=True))
    probes = [call for call in runtime.calls if call[:3] == ("docker", "run", "-d")]
    assert len(probes) == 1
    assert all(receipt["task_service_containment"] == receipts[0]["task_service_containment"] for receipt in receipts)
    await runtime.finish()
