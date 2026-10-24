"""Prepare fresh Harbor verifier containers with explicitly transferred, checked artifacts.

No agent root filesystem or live workspace is inherited. The caller freezes agent work first,
executes the returned command with the task's timeout, and always executes the cleanup commands.
"""

from __future__ import annotations

import hashlib
import json
import math
import pathlib
import re
import shlex
import shutil
import stat
import tempfile
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from mesocosm.adapters.harbor.task import HarborTask

if TYPE_CHECKING:
    from mesocosm.env.task_runtime import NativeImage


class TaskVerifierError(ValueError):
    """The declared verifier cannot be run without changing its isolation or input contract."""


class Command(Protocol):
    async def __call__(self, *args: str, timeout: float = 600, include_stderr: bool = False) -> bytes: ...


@dataclass
class VerifierPlan:
    command: list[str]
    metadata: dict[str, Any]
    cleanup: list[tuple[str, ...]]


@dataclass(frozen=True)
class Artifact:
    source: str
    destination: str
    optional: bool = False


def _positive(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value) or value <= 0:
        raise TaskVerifierError(f"{name} must be a positive finite number")
    return float(value)


def _path(value: Any, *, absolute: bool) -> pathlib.PurePosixPath:
    if not isinstance(value, str) or not value or any(ch in value for ch in ("\x00", "\n", ":", "*", "?", "[")):
        raise TaskVerifierError("artifact paths must be explicit POSIX paths without globs or control characters")
    path = pathlib.PurePosixPath(value)
    if path.is_absolute() != absolute or ".." in path.parts or str(path) in ("/", ".") or value != str(path):
        raise TaskVerifierError("artifact source must be absolute and destination relative, without traversal")
    return path


def artifact_specs(task: HarborTask) -> list[Artifact]:
    configured = task.config.get("artifacts") or []
    if not isinstance(configured, list):
        raise TaskVerifierError("artifacts must be a list of paths or source/destination objects")
    artifacts: list[Artifact] = []
    forbidden = [pathlib.PurePosixPath(path) for path in
                 ("/tests", "/logs/verifier", "/proc", "/sys", "/dev", "/etc", "/bin", "/sbin",
                  "/usr", "/lib", "/lib64", "/run", "/root/.config", "/opt/coding-harness")]
    for entry in configured:
        if isinstance(entry, str):
            source, destination = entry, entry.lstrip("/")
        elif isinstance(entry, dict) and set(entry) <= {"source", "destination"} and "source" in entry:
            source = entry["source"]
            destination = entry.get("destination", str(source).lstrip("/"))
        else:
            raise TaskVerifierError("unsupported artifact fields; only source and destination are implemented")
        source_path, destination_path = _path(source, absolute=True), _path(destination, absolute=False)
        if any(source_path.is_relative_to(path) or path.is_relative_to(source_path) for path in forbidden):
            raise TaskVerifierError(f"artifact source {source!r} overlaps verifier or system authority")
        for previous in artifacts:
            for path, prior in ((source_path, pathlib.PurePosixPath(previous.source)),
                                (destination_path, pathlib.PurePosixPath(previous.destination))):
                if path.is_relative_to(prior) or prior.is_relative_to(path):
                    raise TaskVerifierError("artifact sources and host destinations must not overlap")
        artifacts.append(Artifact(str(source_path), str(destination_path)))
    publish = pathlib.PurePosixPath("/logs/artifacts")
    if not any(publish.is_relative_to(pathlib.PurePosixPath(item.source)) for item in artifacts):
        if any(pathlib.PurePosixPath(item.source).is_relative_to(publish) for item in artifacts):
            raise TaskVerifierError("declare /logs/artifacts as a whole; automatic publish-directory transfer overlaps")
        if any(pathlib.PurePosixPath(item.destination).is_relative_to("logs/artifacts") or
               pathlib.PurePosixPath("logs/artifacts").is_relative_to(item.destination) for item in artifacts):
            raise TaskVerifierError("artifact host destination overlaps the automatic publish directory")
        artifacts.append(Artifact("/logs/artifacts", "logs/artifacts", optional=True))
    return artifacts


def verifier_environment(task: HarborTask) -> dict[str, Any]:
    """Validate the separate-verifier subset before any Docker work or agent task execution."""
    verifier = task.config.get("verifier") or {}
    if not isinstance(verifier, dict):
        raise TaskVerifierError("verifier must be a mapping")
    known = {"environment", "environment_mode", "timeout_sec", "env", "user", "requires_live_processes",
             "network_mode", "allowed_hosts"}
    if set(verifier) - known:
        raise TaskVerifierError(f"unsupported verifier fields: {sorted(set(verifier) - known)}")
    mode = verifier.get("environment_mode") or ("separate" if "environment" in verifier else "shared")
    if mode != "separate":
        raise TaskVerifierError("fresh verifier preparation requires environment_mode: separate")
    if not isinstance(verifier.get("requires_live_processes", False), bool):
        raise TaskVerifierError("requires_live_processes must be boolean")
    env = verifier.get("environment", task.config.get("environment") or {})
    if not isinstance(env, dict):
        raise TaskVerifierError("verifier.environment must be a mapping")
    known_env = {"docker_image", "build_timeout_sec", "cpus", "memory_mb", "memory", "storage_mb", "storage",
                 "gpus", "gpu_types", "env", "workdir", "os", "network_mode", "allowed_hosts", "allow_internet"}
    if set(env) - known_env:
        raise TaskVerifierError(f"unsupported verifier environment fields: {sorted(set(env) - known_env)}")
    for key in ("storage_mb", "storage", "gpus", "gpu_types"):
        if env.get(key):
            raise TaskVerifierError(f"separate verifier cannot enforce {key}")
    if env.get("os", "linux") != "linux":
        raise TaskVerifierError("separate verifier currently requires Linux")
    network_mode = verifier.get("network_mode") or env.get("network_mode")
    if network_mode not in (None, "no-network") or verifier.get("allowed_hosts") or env.get("allowed_hosts"):
        raise TaskVerifierError("separate verifier internet policies are unsupported; use an owned internal network")
    if env.get("allow_internet"):
        raise TaskVerifierError("separate verifier cannot grant internet access")
    for field in ("cpus", "memory_mb", "build_timeout_sec"):
        if env.get(field) is not None:
            _positive(env[field], f"verifier.environment.{field}")
    if env.get("memory_mb") is not None and type(env["memory_mb"]) is not int:
        raise TaskVerifierError("verifier.environment.memory_mb must be an integer")
    if env.get("memory") is not None and not re.fullmatch(r"[1-9][0-9]*[bkmgBKMG]?", str(env["memory"])):
        raise TaskVerifierError("verifier.environment.memory must be a positive Docker memory size")
    if env.get("memory") is not None and env.get("memory_mb") is not None:
        raise TaskVerifierError("declare verifier memory or memory_mb, not both")
    _positive(verifier.get("timeout_sec", 600), "verifier.timeout_sec")
    for values in (env.get("env") or {}, verifier.get("env") or {}):
        if not isinstance(values, dict) or any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(key)) or
            not isinstance(value, str | int | float | bool) or "\x00" in str(value) for key, value in values.items()):
            raise TaskVerifierError("verifier environment variables require names and scalar values")
        if any("${" in str(value) for value in values.values()):
            raise TaskVerifierError("verifier environment interpolation must be resolved before execution")
    artifact_specs(task)
    return env


def _checked_tree(path: pathlib.Path) -> dict[str, Any]:
    """Reject links, special files and privilege bits without dereferencing any untrusted entry."""
    files: dict[str, Any] = {}
    pending = [path]
    while pending:
        current = pending.pop()
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            raise TaskVerifierError("artifact contains a symlink or special file")
        if info.st_mode & 0o7000 or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1):
            raise TaskVerifierError("artifact contains privilege bits or hard links")
        rel = current.relative_to(path).as_posix()
        files[rel] = {"mode": stat.S_IMODE(info.st_mode), "type": "directory" if current.is_dir() else "file"}
        if current.is_dir():
            pending.extend(current.iterdir())
        else:
            files[rel].update(size=info.st_size, sha256=hashlib.sha256(current.read_bytes()).hexdigest())
    return files


def _interpreter(script: pathlib.Path) -> list[str]:
    with script.open("rb") as stream:
        first = stream.readline(4096).decode("utf-8", "strict").strip()
    args = shlex.split(first[2:]) if first.startswith("#!") else ["/bin/bash"]
    if not args or not args[0].startswith("/") or any("\x00" in arg for arg in args):
        raise TaskVerifierError("original verifier shebang requires an absolute interpreter")
    return args


async def prepare_verifier(task: HarborTask, native: NativeImage, container_id: str, workspace: pathlib.Path,
                           logs_dir: pathlib.Path, command: Command, *, project: str, network: str = "none",
                           service_scope: str = "isolated", script: str = "test.sh") -> VerifierPlan:
    env = verifier_environment(task)
    verifier = task.config.get("verifier") or {}
    artifacts = artifact_specs(task)
    if service_scope not in ("isolated", "shared"):
        raise TaskVerifierError("unknown task service scope")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", project):
        raise TaskVerifierError("invalid verifier project ownership")
    if service_scope == "shared" and any(not item.optional for item in artifacts):
        raise TaskVerifierError("shared-team verification requires service state only, without agent artifacts")
    if not re.fullmatch(r"[a-f0-9]{64}", container_id):
        raise TaskVerifierError("artifact source requires the inspected full agent container ID")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", script):
        raise TaskVerifierError("verifier script must be a filename under /tests")
    if network != "none":
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", network) or network in ("host", "bridge", "default"):
            raise TaskVerifierError("verifier network must be none or an owned internal task network")
        info = json.loads((await command("docker", "network", "inspect", network)).decode())
        if len(info) != 1 or info[0].get("Internal") is not True:
            raise TaskVerifierError("verifier task network is not internal")
    if verifier.get("requires_live_processes") and network == "none":
        raise TaskVerifierError("live service verification requires the owned internal task network")
    if (verifier.get("network_mode") or env.get("network_mode")) == "no-network" and network != "none":
        raise TaskVerifierError("declared verifier no-network policy cannot reach live services")
    resources: list[str] = []
    if env.get("cpus") is not None:
        resources.extend(["--cpus", str(env["cpus"])])
    if env.get("memory_mb") is not None or env.get("memory") is not None:
        memory = f"{env['memory_mb']}m" if env.get("memory_mb") is not None else str(env["memory"])
        resources.extend(["--memory", memory])
    if resources:
        info = json.loads((await command("docker", "info", "--format", "{{json .}}")).decode())
        warnings = " ".join(info.get("Warnings") or []).lower()
        if info.get("CgroupDriver") in (None, "", "none") or any(
                text in warnings for text in ("without cgroups", "no memory limit", "no cpu cfs")):
            raise TaskVerifierError("Docker cannot enforce separate verifier CPU/memory limits")
    tests = task.path / "tests"
    if any((tests / filename).exists() for filename in ("docker-compose.yaml", "docker-compose.yml", "compose.yaml")):
        raise TaskVerifierError("verifier-specific Compose stacks are unsupported")
    logs_dir = logs_dir.resolve()
    if logs_dir.is_relative_to(workspace.resolve()) or workspace.resolve().is_relative_to(logs_dir):
        raise TaskVerifierError("verifier logs must stay outside the live agent workspace")
    logs_dir.mkdir(parents=True, exist_ok=True)
    output = logs_dir / "verifier"
    output.mkdir(exist_ok=True)
    output.chmod(0o777)
    name = f"verifier-{uuid.uuid4().hex}"
    cleanup: list[tuple[str, ...]] = [("docker", "rm", "-f", "-v", name)]
    build_timeout = float(env.get("build_timeout_sec") or 1200)
    try:
        declared = (verifier.get("environment") or {}).get("docker_image")
        if declared:
            image, selection = str(declared), "verifier_image"
            await command("docker", "pull", image, timeout=build_timeout)
        elif (tests / "Dockerfile").is_file():
            image, selection = f"mesocosm-task-verifier:{uuid.uuid4().hex}", "verifier_dockerfile"
            cleanup.append(("docker", "image", "rm", "-f", image))
            await command("docker", "build", "-t", image, "-f", str(tests / "Dockerfile"), str(tests),
                          timeout=build_timeout)
        else:
            image, selection = native.base_id, "fresh_agent_base_image"
        inspected = json.loads((await command("docker", "image", "inspect", image)).decode())[0]
        image_id, config = inspected["Id"], inspected["Config"]
        if inspected.get("Os", "linux") != "linux":
            raise TaskVerifierError("separate verifier image must run Linux")
        baked = selection != "fresh_agent_base_image"
        test = tests / script
        if not baked and not test.is_file():
            raise TaskVerifierError(f"original tests/{script} is missing")
        # Native config is captured before the controller adds harness authentication or proxies.
        # A fresh base still needs original Compose main overrides, which are not image metadata.
        original_variables: dict[str, str] = {}
        if not baked:
            config = native.config
            for item in config.get("Env") or []:
                if not isinstance(item, str) or "=" not in item:
                    raise TaskVerifierError("original main environment contains an invalid variable")
                key, value = item.split("=", 1)
                if key.startswith(("OPENCODE_", "MESOCOSM_")):
                    raise TaskVerifierError("original main environment cannot supply harness or controller credentials")
                original_variables[key] = value
        workdir = str(env.get("workdir") or config.get("WorkingDir") or "/")
        if not workdir.startswith("/") or ".." in pathlib.PurePosixPath(workdir).parts:
            raise TaskVerifierError("verifier workdir must be absolute without traversal")
        user = verifier.get("user")
        if user is None:
            user = config.get("User") or "root"
        if not re.fullmatch(r"[A-Za-z0-9_.-]+(?::[A-Za-z0-9_.-]+)?", str(user)):
            raise TaskVerifierError("unsupported verifier user")
        variables = {**original_variables, **(env.get("env") or {}), **(verifier.get("env") or {})}
        mounts = ["-v", f"{output}:/logs/verifier"]
        world_state_mounted = service_scope == "isolated" and (logs_dir / "world.json").is_file()
        if world_state_mounted:
            mounts.extend(["-v", f"{logs_dir / 'world.json'}:/logs/world.json:ro"])
        if not baked:
            mounts.extend(["-v", f"{tests.resolve()}:/tests:ro"])
        create = ["docker", "create", "--name", name, "--network", network, "--cap-drop", "NET_RAW",
                  "--cap-drop", "NET_ADMIN", "--security-opt", "no-new-privileges", *resources,
                  "--workdir", workdir, "--user", "0", "--entrypoint", "/bin/sh", *mounts,
                  *[item for key, value in sorted(variables.items()) for item in ("-e", f"{key}={value}")],
                  image_id, "-c", "while :; do sleep 3600; done"]
        create[2:2] = ["--label", f"com.docker.compose.project={project}",
                       "--label", f"com.docker.compose.service={name}"]
        await command(*create)
        await command("docker", "start", name)
        retained = logs_dir / "artifacts"
        retained.mkdir(exist_ok=False)
        transfers = []
        with tempfile.TemporaryDirectory(prefix="verifier-artifacts-") as tmp:
            original_script = test
            if baked:
                original_script = pathlib.Path(tmp) / "original-verifier-script"
                await command("docker", "cp", "-L", f"{name}:/tests/{script}", str(original_script))
            interpreter = _interpreter(original_script)
            script_sha256 = hashlib.sha256(original_script.read_bytes()).hexdigest()
            root = pathlib.Path(tmp) / "root"
            root.mkdir()
            for index, artifact in enumerate(artifacts):
                destination = retained / artifact.destination
                destination.parent.mkdir(parents=True, exist_ok=True)
                collected = pathlib.Path(tmp) / f"collected-{index}"
                try:
                    await command("docker", "cp", f"{container_id}:{artifact.source}", str(collected))
                except RuntimeError as exc:
                    if artifact.optional and any(text in str(exc).lower() for text in
                                                 ("could not find the file", "no such file or directory")):
                        transfers.append({"source": artifact.source, "status": "absent", "optional": True})
                        continue
                    raise
                files = _checked_tree(collected)
                if service_scope == "shared" and (not collected.is_dir() or any(collected.iterdir())):
                    raise TaskVerifierError("shared-team verification cannot select one agent's published artifacts")
                target = root / artifact.source.lstrip("/")
                target.parent.mkdir(parents=True, exist_ok=True)
                if collected.is_dir():
                    shutil.copytree(collected, target)
                    shutil.copytree(collected, destination)
                else:
                    shutil.copy2(collected, target)
                    shutil.copy2(collected, destination)
                transfers.append({"source": artifact.source, "destination": artifact.destination,
                                  "status": "copied", "files": files})
            if any(root.iterdir()):
                await command("docker", "cp", f"{root}/.", f"{name}:/")
                # Docker cp owns files as root. Restore ownership to the declared verifier user
                # inside its fresh container, without changing host ownership or permission modes.
                identity = []
                for flag in ("-u", "-g"):
                    number = (await command("docker", "exec", "--user", str(user), name, "id", flag)).decode().strip()
                    if not re.fullmatch(r"[0-9]+", number):
                        raise TaskVerifierError("cannot resolve original verifier user/group for artifact transfer")
                    identity.append(number)
                paths = [str(entry["source"]) for entry in transfers if entry["status"] == "copied"]
                await command("docker", "exec", "--user", "0", name, "chown", "-R", ":".join(identity), "--", *paths)
        return VerifierPlan(["docker", "exec", "--user", str(user), "--workdir", workdir,
                             name, *interpreter, f"/tests/{script}"],
            {"verifier_image_id": image_id, "image_selection": selection, "environment_mode": "separate",
             "coverage": "fresh_environment_with_declared_artifacts", "network": network, "workdir": workdir,
             "user": str(user), "tests_baked": baked, "artifact_transfers": transfers,
             "original_main_settings_applied": not baked,
             "world_state_mounted": world_state_mounted,
             "verifier_interpreter": interpreter, "verifier_script_sha256": script_sha256,
             "env_names": sorted(variables), "resource_arguments": resources}, cleanup)
    except BaseException as failure:
        unverified = []
        for args in cleanup:
            try:
                await command(*args, timeout=30)
            except Exception as exc:
                if not any(text in str(exc).lower() for text in ("no such container", "no such image")):
                    unverified.append(list(args))
        if unverified:
            (logs_dir / "preparation-cleanup.json").write_text(json.dumps(
                {"status": "unverified", "cleanup_commands": unverified}, indent=2))
            message = "verifier preparation failed and owned resource cleanup is unverified"
            if isinstance(failure, Exception):
                raise TaskVerifierError(message) from failure
            failure.add_note(message)
        raise
