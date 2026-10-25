"""Run Harbor tasks in their native image, adding only the unmodified OpenCode harness.

The task's root filesystem, user, working directory and startup command survive. Its working tree
is copied out before the host workspace is mounted over it. Verification uses a private snapshot of
the resulting container, with tests introduced only after the agent has finished (ADR-0042).
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import math
import os
import pathlib
import shlex
import shutil
import stat
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

import yaml

from mesocosm.adapters.harbor.task import read_reward
from mesocosm.adapters.opencode.client import CONTEXT_CONFIG
from mesocosm.core.artifacts import ArtifactSpec, capture_artifacts
from mesocosm.core.manifest import AgentSpec
from mesocosm.env.egress import DockerEgressVerifier
from mesocosm.env.lifecycle import CONTAINER_ID, container_evidence, isolation_evidence
from mesocosm.env.task_stack import (
    TaskComposeError,
    TaskStack,
    compile_task_stack,
    find_compose_file,
    prevalidate_compose,
)
from mesocosm.integrations.tasks import Reward, Task


class TaskEnvironmentError(RuntimeError):
    """A native task environment could not be built, started or verified."""


def environment_source(task: Task) -> tuple[str | None, pathlib.Path]:
    """Select the task's declared image or Dockerfile; never substitute a generic environment."""
    root = task.path / "environment"
    try:
        compose = find_compose_file(root)
        if compose is not None:
            prevalidate_compose(yaml.safe_load(compose.read_text()), root)
            return None, root
    except TaskComposeError as exc:
        raise TaskEnvironmentError(f"{task.name}: {exc}") from exc
    image = (task.config.get("environment") or {}).get("docker_image")
    if image:
        return str(image), root
    if not (root / "Dockerfile").is_file():
        raise TaskEnvironmentError(f"{task.name}: declare environment.docker_image or environment/Dockerfile")
    return None, root


def environment_key(task: Task, opencode_version: str) -> str:
    image, root = environment_source(task)
    identity = {"image": image, "version": opencode_version, "environment": task.config.get("environment") or {}}
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode())
    digest.update(harness_dockerfile("native-image", opencode_version, "native-user").encode())
    digest.update(startup_script({"Cmd": ["native-command"]}).encode())
    if image is None:
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:24]


def harness_dockerfile(base: str, version: str, user: str) -> str:
    """The published harness and plugin SDK, retaining the native user, cwd and config environment.

    OpenCode 1.19.3 waits for SDK installation when loading a local plugin. Prepare its official
    package/lock/node_modules in both custom and global config directories before network isolation.
    """
    account = shlex.quote((user or "root").split(":", 1)[0])
    return ("FROM node:22-bookworm-slim AS coding_harness\n"
            f"RUN npm install -g --prefix /opt/coding-harness {shlex.quote('opencode-ai@' + version)}\n"
            "RUN npm install --prefix /opt/harness-config --ignore-scripts --no-audit --no-fund "
            f"--save-exact {shlex.quote('@opencode-ai/plugin@' + version)}\n"
            f"FROM {base}\nUSER root\n"
            "RUN command -v apt-get >/dev/null || "
            "{ echo 'Native harness currently requires Debian/Ubuntu' >&2; exit 1; }; "
            "apt-get update && apt-get install -y --no-install-recommends libstdc++6 curl ca-certificates "
            "&& rm -rf /var/lib/apt/lists/*\n"
            "COPY --from=coding_harness /usr/local/bin/node /usr/local/bin/node\n"
            "COPY --from=coding_harness /opt/coding-harness /opt/coding-harness\n"
            "COPY --from=coding_harness /opt/harness-config /etc/opencode\n"
            "RUN config_dir=\"${XDG_CONFIG_HOME:-${HOME}/.config}/opencode\" "
            "&& if [ \"$config_dir\" != /etc/opencode ]; then "
            "mkdir -p \"$config_dir\" && cp -a /etc/opencode/. \"$config_dir/\"; fi "
            f"&& task_home=\"$(getent passwd {account} | cut -d: -f6)\" "
            "&& if [ -n \"$task_home\" ] && [ -z \"${XDG_CONFIG_HOME:-}\" ]; then "
            "mkdir -p \"$task_home/.config/opencode\" "
            "&& cp -a /etc/opencode/. \"$task_home/.config/opencode/\"; fi\n"
            "RUN ln -sf /opt/coding-harness/bin/opencode /usr/local/bin/opencode "
            "&& test -x /usr/local/bin/opencode && /usr/local/bin/opencode --version\n"
            f"USER {user or 'root'}\n")


def startup_script(config: dict[str, Any]) -> str:
    native = [*(config.get("Entrypoint") or []), *(config.get("Cmd") or [])]
    if not native:
        return "#!/bin/sh\nset -eu\nexec /usr/local/bin/opencode serve --hostname 0.0.0.0 --port 4096\n"
    return ("#!/bin/sh\nset -u\n"
            f"{shlex.join(native)} > /tmp/application-startup.log 2>&1 &\napplication=$!\n"
            "/usr/local/bin/opencode serve --hostname 0.0.0.0 --port 4096 &\nharness=$!\n"
            "trap 'kill \"$application\" \"$harness\" 2>/dev/null || true' EXIT INT TERM\n"
            "while kill -0 \"$harness\" 2>/dev/null; do\n"
            "  if [ -n \"$application\" ] && ! kill -0 \"$application\" 2>/dev/null; then\n"
            "    wait \"$application\"; result=$?\n"
            "    [ \"$result\" -eq 0 ] || exit \"$result\"\n"
            "    application=''\n  fi\n  sleep 1\ndone\nwait \"$harness\"\n")


@dataclass
class NativeImage:
    image: str
    base_id: str
    config: dict[str, Any]
    workdir: str
    key: str
    compose: dict[str, Any] | None = None
    platform: str | None = None


@dataclass
class TaskGroup:
    key: str
    task: Task
    native: NativeImage
    stack: TaskStack | None
    expected: list[str]
    participants: dict[str, str] = field(default_factory=dict)
    containers: dict[str, str] = field(default_factory=dict)
    logs: dict[str, pathlib.Path] = field(default_factory=dict)
    paused: set[str] = field(default_factory=set)
    future: asyncio.Future[Reward] | None = None
    grading: asyncio.Task[None] | None = None
    released: bool = False
    started: bool = False
    publications: dict[str, str] = field(default_factory=dict)
    containment: dict[str, Any] | None = None


def _compose_literals(value: Any) -> Any:
    """Resolved task strings must not undergo a second host-variable interpolation."""
    if isinstance(value, str):
        return value.replace("$", "$$")
    if isinstance(value, list):
        return [_compose_literals(item) for item in value]
    if isinstance(value, dict):
        return {key: _compose_literals(item) for key, item in value.items()}
    return value


class DockerTaskRuntime:
    def __init__(self, compose_file: pathlib.Path, state_dir: pathlib.Path, opencode_version: str = "1.19.3", *,
                 verifier_network: str = "none", allow_unenforced_storage: bool = False,
                 allow_unenforced_resources: bool = False, egress: DockerEgressVerifier | None = None,
                 task_services: Literal["isolated", "shared"] = "isolated", agent_ids: list[str] | None = None,
                 memory: Literal["reset", "carry"] = "reset"):
        if task_services not in ("isolated", "shared") or memory not in ("reset", "carry"):
            raise TaskEnvironmentError("invalid native task service scope or memory policy")
        if task_services == "shared" and (memory != "reset" or not agent_ids):
            raise TaskEnvironmentError("shared services require reset memory and explicit participant agent IDs")
        if verifier_network not in ("none", "bridge"):
            raise TaskEnvironmentError("verifier network must be none or bridge; sandbox networks are forbidden")
        self.compose_file = compose_file.resolve()
        self.state_dir = state_dir.resolve()
        self.opencode_version = opencode_version
        self.verifier_network = verifier_network
        self.allow_unenforced_resources = allow_unenforced_resources or allow_unenforced_storage
        self.limits_available: bool | None = None
        self.egress = egress
        self.base_compose = yaml.safe_load(self.compose_file.read_text())
        for service in self.base_compose.get("services", {}).values():
            build = service.get("build")
            if isinstance(build, dict) and build.get("context"):
                build["context"] = str((self.compose_file.parent / build["context"]).resolve())
            for i, mount in enumerate(service.get("volumes") or []):
                if isinstance(mount, str) and mount.startswith("."):
                    source, target = mount.split(":", 1)
                    service["volumes"][i] = f"{(self.compose_file.parent / source).resolve()}:{target}"
        self.images: dict[str, NativeImage] = {}
        self.active: dict[str, tuple[str, str, NativeImage]] = {}  # workspace -> (agent, container id, image)
        self.build_lock = asyncio.Lock()
        self.runtime_lock = asyncio.Lock()
        self.task_services = task_services
        self.memory = memory
        self.agent_ids = agent_ids or [name for name in self.base_compose["services"] if name.startswith("agent-")]
        self.runtime_file = pathlib.Path(str(self.compose_file) + ".runtime.yaml")
        self.rendered = copy.deepcopy(self.base_compose)
        self.groups: dict[str, TaskGroup] = {}
        self.workspace_groups: dict[str, str] = {}
        self.episodes: dict[tuple[str, str], int] = {}
        self.verifier_cleanup: list[tuple[str, ...]] = []
        self.task_containment: dict[tuple[str, str | None], dict[str, Any]] = {}
        self.probes: dict[str, str] = {}
        self.project = self.base_compose.get("name")
        if not isinstance(self.project, str) or not self.project:
            raise TaskEnvironmentError("native runtime requires an explicit Compose project name")

    async def command(self, *args: str, timeout: float = 600, include_stderr: bool = False) -> bytes:
        proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout)
        except TimeoutError as e:
            proc.kill()
            await proc.wait()
            raise TaskEnvironmentError(f"command timed out after {timeout}s: {shlex.join(args[:4])}") from e
        except asyncio.CancelledError:
            proc.kill()
            await proc.wait()
            raise
        if proc.returncode:
            raise TaskEnvironmentError(f"{shlex.join(args[:4])} exited {proc.returncode}: "
                                       + (out + err).decode("utf-8", "replace")[-4000:])
        return out + err if include_stderr else out

    async def native_image(self, task: Task) -> NativeImage:
        env = task.config.get("environment") or {}
        supported = {"docker_image", "build_timeout_sec", "cpus", "memory_mb", "memory", "storage_mb", "storage",
                     "gpus", "gpu_types", "allow_internet", "mcp_servers", "env"}
        unknown = {key for key, value in env.items() if key not in supported and value is not None}
        if unknown:
            raise TaskEnvironmentError(f"{task.name}: unsupported environment fields: {', '.join(sorted(unknown))}")
        if float(env.get("gpus") or 0) > 0:
            raise TaskEnvironmentError(f"{task.name}: GPU task environments are unsupported")
        if env.get("mcp_servers"):
            raise TaskEnvironmentError(f"{task.name}: task-declared MCP servers are unsupported; use agents[].mcp")
        if (env.get("storage_mb") or env.get("storage")) and not self.allow_unenforced_resources:
            raise TaskEnvironmentError(f"{task.name}: storage quotas are unsupported; explicitly opt in with "
                                       "--allow-unenforced-resources to record an adapted run")
        verifier = task.config.get("verifier") or {}
        if not isinstance(verifier, dict):
            raise TaskEnvironmentError("task verifier configuration must be a mapping")
        if verifier.get("environment_mode") not in (None, "shared", "separate"):
            raise TaskEnvironmentError("unsupported original verifier environment mode")
        separate = verifier.get("environment") is not None or verifier.get("environment_mode") == "separate"
        if separate:
            from mesocosm.env.task_verifier import artifact_specs, verifier_environment

            verifier_environment(task)
            if self.task_services == "shared" and any(not item.optional for item in artifact_specs(task)):
                raise TaskEnvironmentError("shared verification supports service state only, not agent artifacts")
        elif verifier.get("requires_live_processes") or self.task_services == "shared":
            raise TaskEnvironmentError("live/shared service verification requires a separate original verifier")
        else:
            known_verifier = {"environment_mode", "timeout_sec", "env", "requires_live_processes"}
            if set(verifier) - known_verifier:
                raise TaskEnvironmentError("unsupported snapshot verifier fields: "
                                           + ", ".join(sorted(set(verifier) - known_verifier)))
            timeout = verifier.get("timeout_sec", 600)
            if isinstance(timeout, bool) or not isinstance(timeout, int | float) or not math.isfinite(timeout) \
                    or timeout <= 0:
                raise TaskEnvironmentError("verifier timeout must be positive and finite")
            variables = verifier.get("env") or {}
            if not isinstance(variables, dict) or any(
                not isinstance(key, str) or not isinstance(value, str | int | float)
                for key, value in variables.items()
            ):
                raise TaskEnvironmentError("snapshot verifier environment requires explicit scalar values")
        key = environment_key(task, self.opencode_version)
        async with self.build_lock:
            if self.limits_available is None:
                info = json.loads((await self.command("docker", "info", "--format", "{{json .}}")).decode())
                warnings = " ".join(info.get("Warnings") or []).lower()
                self.limits_available = info.get("CgroupDriver") not in (None, "", "none") and not any(
                    phrase in warnings for phrase in ("without cgroups", "no memory limit", "no cpu cfs"))
            if resource_options(task) and not self.limits_available and not self.allow_unenforced_resources:
                raise TaskEnvironmentError(f"{task.name}: Docker cannot enforce CPU/memory limits; "
                                           "explicit --allow-unenforced-resources is required")
            if key in self.images:
                return self.images[key]
            declared, context = environment_source(task)
            source = find_compose_file(context)
            native_compose = None
            platform = None
            if source is not None:
                raw = yaml.safe_load(source.read_text())
                prevalidate_compose(raw, context)
                native_compose = json.loads((await self.command(
                    "docker", "compose", "-f", str(source), "config", "--format", "json")).decode())
                self._compile(task, native_compose, key)
                declared = native_compose["services"]["main"].get("image")
                main = native_compose["services"]["main"]
                platform = main.get("platform")
                compose_limits = any(service.get(field) is not None for service in native_compose["services"].values()
                                     for field in ("cpus", "mem_limit", "mem_reservation", "pids_limit"))
                if compose_limits and not self.limits_available:
                    raise TaskEnvironmentError("Docker cannot enforce task Compose resource limits")
            build_dir = self.state_dir / key
            build_dir.mkdir(parents=True, exist_ok=True)
            timeout = float((task.config.get("environment") or {}).get("build_timeout_sec") or 1200)
            base = declared or f"mesocosm-task-base:{key}"
            if native_compose is not None and main.get("build"):
                compiled = self._compile(task, native_compose, key)
                build_spec = next(iter(compiled.main_by_agent.values()))["build"]
                build_file = build_dir / "build.yaml"
                base = f"mesocosm-task-base:{key}"
                build_file.write_text(yaml.safe_dump({"services": {"main": {"image": base,
                    "build": _compose_literals(build_spec), **({"platform": platform} if platform else {})}}}))
                await self.command("docker", "compose", "-f", str(build_file), "build", "main", timeout=timeout)
            elif declared:
                await self.command("docker", "pull", *(["--platform", platform] if platform else []),
                                   base, timeout=timeout)
            else:
                await self.command("docker", "build", "-t", base, "-f", str(context / "Dockerfile"), str(context),
                                   timeout=timeout)
            inspected = json.loads((await self.command("docker", "image", "inspect", base)).decode())[0]
            config = copy.deepcopy(inspected["Config"])
            if native_compose is not None:
                main = native_compose["services"]["main"]
                for compose_field, image_field in (("user", "User"), ("working_dir", "WorkingDir"),
                                                   ("entrypoint", "Entrypoint"), ("command", "Cmd")):
                    if main.get(compose_field) is not None:
                        config[image_field] = main[compose_field]
                if main.get("entrypoint") is not None and main.get("command") is None:
                    config["Cmd"] = []
                if any(not isinstance(config.get(field) or [], list) for field in ("Entrypoint", "Cmd")):
                    raise TaskEnvironmentError("Compose did not resolve main command/entrypoint to argument arrays")
                values = dict(item.split("=", 1) for item in config.get("Env") or [] if "=" in item)
                values.update(main.get("environment") or {})
                config["Env"] = [f"{name}={value}" for name, value in values.items()]
            workdir = config.get("WorkingDir") or "/"
            if not workdir.startswith("/") or workdir in ("/", "/usr", "/etc", "/bin", "/root"):
                raise TaskEnvironmentError(f"{task.name}: native WORKDIR {workdir!r} cannot be workspace-mounted")
            source = build_dir / "Dockerfile"
            selected = f"mesocosm-task-selected:{key}"
            await self.command("docker", "tag", inspected["Id"], selected)
            source.write_text(harness_dockerfile(selected, self.opencode_version, config.get("User") or ""))
            image = f"mesocosm-task-agent:{key}"
            await self.command("docker", "build", *(["--platform", platform] if platform else []),
                               "-t", image, "-f", str(source), str(build_dir), timeout=timeout)
            native = NativeImage(image, inspected["Id"], config, workdir, key, native_compose, platform)
            self.images[key] = native
            return native

    async def prebuild(self, tasks: list[Task]) -> list[dict[str, Any]]:
        """Build declared environments before the run's exposure clock starts."""
        prepared = []
        for task in tasks:
            native = await self.native_image(task)
            prepared.append({"task": task.name, **self.receipt("", native), "adaptations": self.adaptations(task)})
        return prepared

    def _compile(self, task: Task, compose: dict[str, Any], key: str,
                 participants: list[str] | None = None) -> TaskStack:
        selected = participants or self.agent_ids
        networks = {}
        aliases = set(self.base_compose["services"])
        for service in self.base_compose["services"].values():
            for spec in (service.get("networks") or {}).values():
                aliases.update((spec or {}).get("aliases") or [])
        for agent_id in selected:
            service = self.base_compose["services"].get(agent_id)
            if service is None or len(service.get("networks") or {}) != 1:
                raise TaskEnvironmentError("each native agent requires exactly one controlled network")
            network = next(iter(service["networks"]))
            if (self.base_compose.get("networks", {}).get(network) or {}).get("internal") is not True:
                raise TaskEnvironmentError("native task services require an existing internal network")
            if not (service.get("environment") or {}).get("OPENCODE_SERVER_PASSWORD"):
                raise TaskEnvironmentError("task sidecars require an authenticated OpenCode server")
            networks[agent_id] = network
        try:
            return compile_task_stack(compose, environment_root=task.path / "environment", world_name=self.project,
                                      task_key=key, agent_networks=networks, scope=self.task_services,
                                      reserved_aliases=aliases)
        except TaskComposeError as exc:
            raise TaskEnvironmentError(f"{task.name}: {exc}") from exc

    def _write_rendered(self) -> None:
        self.runtime_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.runtime_file.with_suffix(self.runtime_file.suffix + ".tmp")
        temporary.write_text(yaml.safe_dump(self.rendered, sort_keys=False))
        temporary.replace(self.runtime_file)

    def _new_group(self, agent: AgentSpec, task: Task, native: NativeImage, workspace: pathlib.Path) -> TaskGroup:
        identity = hashlib.sha256((str(task.path.resolve()) + "\0" + native.key).encode()).hexdigest()[:24]
        counter_key = (agent.id, identity)
        episode = self.episodes.get(counter_key, 0) + 1
        self.episodes[counter_key] = episode
        participant = "team" if self.task_services == "shared" else agent.id
        key = f"{identity}-{participant}-{episode}"
        if key not in self.groups:
            expected = self.agent_ids if self.task_services == "shared" else [agent.id]
            stack = self._compile(task, native.compose, key, expected) if native.compose is not None else None
            if self.task_services == "shared" and (stack is None or not stack.services):
                raise TaskEnvironmentError("shared task services require a native Compose sidecar environment")
            self.groups[key] = TaskGroup(key, task, native, stack, list(expected))
        group = self.groups[key]
        group.participants[agent.id] = str(workspace)
        self.workspace_groups[str(workspace)] = key
        return group

    async def _owned(self, container: str, service: str) -> dict[str, Any]:
        if not CONTAINER_ID.fullmatch(container):
            raise TaskEnvironmentError("native runtime requires the full owned container ID")
        raw = json.loads((await self.command("docker", "container", "inspect", container)).decode())
        if not isinstance(raw, list) or len(raw) != 1:
            raise TaskEnvironmentError("native container inspection returned an invalid record")
        try:
            container_evidence(raw[0], container, self.project, service)
        except ValueError as exc:
            raise TaskEnvironmentError(str(exc)) from exc
        return raw[0]

    async def _quiesce(self, group: TaskGroup) -> None:
        for agent_id, container in group.containers.items():
            before = await self._owned(container, agent_id)
            state = before.get("State") or {}
            if state.get("Running") and not state.get("Paused"):
                await self.command("docker", "container", "pause", container, timeout=30)
            after = await self._owned(container, agent_id)
            state = after.get("State") or {}
            if (before.get("State") or {}).get("StartedAt") != state.get("StartedAt"):
                raise TaskEnvironmentError("agent execution changed while quiescing for verification")
            if state.get("Running") and not state.get("Paused"):
                raise TaskEnvironmentError("agent work could not be quiesced before verification")
            if state.get("Restarting"):
                raise TaskEnvironmentError("agent restarted during verifier preparation")
            if state.get("Paused"):
                group.paused.add(container)

    async def _remove_container(self, container: str, service: str) -> None:
        found = (await self.command("docker", "container", "ls", "--all", "--quiet", "--no-trunc",
                                    "--filter", f"id={container}")).decode().split()
        if not found:
            return
        if found != [container]:
            raise TaskEnvironmentError("native cleanup inventory did not match the exact owned container")
        await self._owned(container, service)
        await self.command("docker", "container", "rm", "-f", "-v", container, timeout=30)
        remaining = (await self.command("docker", "container", "ls", "--all", "--quiet", "--no-trunc",
                                        "--filter", f"id={container}")).decode().split()
        if remaining:
            raise TaskEnvironmentError("native task container remains after removal")

    async def _release(self, group: TaskGroup) -> None:
        if group.released:
            return
        errors = []
        targets = dict(group.containers)
        inventory = self.runtime_file if self.runtime_file.is_file() else self.compose_file
        for agent_id in group.participants:
            if agent_id in targets or agent_id not in self.rendered["services"]:
                continue
            try:
                found = (await self.command("docker", "compose", "-f", str(inventory),
                                            "ps", "--all", "--quiet", agent_id)).decode().split()
                if len(found) > 1:
                    raise TaskEnvironmentError("native agent cleanup inventory is ambiguous")
                if found:
                    targets[agent_id] = found[0]
            except Exception as exc:
                errors.append(f"{agent_id}: {exc}")
        if group.stack is not None:
            for service in group.stack.services:
                if service not in self.rendered["services"]:
                    continue
                try:
                    found = (await self.command("docker", "compose", "-f", str(inventory),
                                                "ps", "--all", "--quiet", service)).decode().split()
                    if len(found) > 1:
                        raise TaskEnvironmentError("task service cleanup inventory is ambiguous")
                    if found:
                        targets[service] = found[0]
                except Exception as exc:
                    errors.append(f"{service}: {exc}")
        for service, container in targets.items():
            try:
                await self._remove_container(container, service)
            except Exception as exc:
                errors.append(f"{service}: {exc}")
        if group.stack is not None:
            for volume in group.stack.volumes:
                name = f"{self.project}_{volume}"
                try:
                    found = (await self.command("docker", "volume", "ls", "--quiet", "--filter", f"name=^{name}$"
                                                )).decode().split()
                    if not found:
                        continue
                    if found != [name]:
                        raise TaskEnvironmentError("task volume cleanup inventory is ambiguous")
                    raw = json.loads((await self.command("docker", "volume", "inspect", name)).decode())
                    labels = (raw[0].get("Labels") or {}) if len(raw) == 1 else {}
                    if labels.get("com.docker.compose.project") != self.project or labels.get(
                            "com.docker.compose.volume") != volume:
                        raise TaskEnvironmentError("task volume ownership could not be established")
                    await self.command("docker", "volume", "rm", name, timeout=30)
                except Exception as exc:
                    errors.append(f"{name}: {exc}")
        if errors:
            raise TaskEnvironmentError("native task cleanup failed: " + "; ".join(errors))
        if group.stack is not None:
            for service in group.stack.services:
                self.rendered["services"].pop(service, None)
            for volume in group.stack.volumes:
                self.rendered.get("volumes", {}).pop(volume, None)
        for workspace in group.participants.values():
            self.active.pop(workspace, None)
            self.workspace_groups.pop(workspace, None)
        for agent_id in group.participants:
            self.rendered["services"][agent_id] = copy.deepcopy(self.base_compose["services"][agent_id])
        group.released = True
        self._write_rendered()

    async def finish(self) -> None:
        """Remove only inspected owned task resources; preserve gateways and service evidence."""
        for group in self.groups.values():
            if group.grading is not None and not group.grading.done():
                group.grading.cancel()
                await asyncio.gather(group.grading, return_exceptions=True)
            if group.future is not None and not group.future.done():
                group.future.set_exception(TaskEnvironmentError("native runtime finished before team verification"))
                group.future.exception()
        errors = []
        async with self.runtime_lock:
            for group in self.groups.values():
                try:
                    await self._release(group)
                except Exception as exc:
                    errors.append(str(exc))
            for command in list(self.verifier_cleanup):
                try:
                    await self._verifier_cleanup(command)
                except Exception as exc:
                    errors.append(str(exc))
            for container, service in list(self.probes.items()):
                try:
                    await self._remove_container(container, service)
                    self.probes.pop(container, None)
                except Exception as exc:
                    errors.append(str(exc))
        if errors:
            raise TaskEnvironmentError("native runtime cleanup incomplete: " + "; ".join(errors))

    async def _verifier_cleanup(self, command: tuple[str, ...]) -> None:
        # docker run --rm may already have removed a completed verifier. Absence is confirmed
        # through inventory, not by suppressing an arbitrary Docker error.
        if command[:3] == ("docker", "rm", "-f"):
            name = command[-1]
            found = (await self.command("docker", "container", "ls", "--all", "--quiet", "--no-trunc",
                                        "--filter", f"name=^/{name}$")).decode().split()
            if found:
                raw = json.loads((await self.command("docker", "container", "inspect", *found)).decode())
                if len(raw) != 1 or raw[0].get("Name") != f"/{name}" or not CONTAINER_ID.fullmatch(found[0]):
                    raise TaskEnvironmentError("verifier cleanup inventory did not match its owned name")
                await self.command("docker", "rm", "-f", "-v", found[0], timeout=30)
        else:
            await self.command(*command, timeout=30)
        if command in self.verifier_cleanup:
            self.verifier_cleanup.remove(command)

    async def _start_services(self, group: TaskGroup) -> None:
        if group.stack is None or group.started:
            return
        group.containment = await self._task_containment_probe(group.native)
        self.rendered["services"].update(_compose_literals(group.stack.services))
        self.rendered.setdefault("volumes", {}).update(group.stack.volumes)
        self._write_rendered()
        names = list(group.stack.services)
        if names:
            await self.command("docker", "compose", "-f", str(self.runtime_file), "up", "-d", "--build", *names,
                               timeout=1200)
            completed = {dependency for spec in [*group.stack.services.values(), *group.stack.main_by_agent.values()]
                         for dependency, options in spec.get("depends_on", {}).items()
                         if options.get("condition") == "service_completed_successfully"}
            async with asyncio.timeout(180):
                while True:
                    ready = True
                    for service in names:
                        found = (await self.command("docker", "compose", "-f", str(self.runtime_file),
                                                    "ps", "--all", "--quiet", service)).decode().split()
                        if len(found) != 1:
                            raise TaskEnvironmentError(f"task service {service} has no unique container")
                        inspected = await self._owned(found[0], service)
                        state = inspected.get("State") or {}
                        if service in completed:
                            if not state.get("Running") and state.get("ExitCode") != 0:
                                raise TaskEnvironmentError(f"task initialization service {service} failed")
                            ready &= not state.get("Running") and state.get("ExitCode") == 0
                        else:
                            if not state.get("Running"):
                                raise TaskEnvironmentError(f"task service {service} exited before readiness")
                            health = (state.get("Health") or {}).get("Status")
                            if health == "unhealthy":
                                raise TaskEnvironmentError(f"task service {service} is unhealthy")
                            ready &= health in (None, "healthy")
                    if ready:
                        break
                    await asyncio.sleep(0.2)
        group.started = True

    def _probe_host(self, pid: int) -> tuple[str, str, str]:
        return (pathlib.Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
                os.readlink("/proc/self/ns/pid"), os.readlink(f"/proc/{pid}/ns/pid"))

    async def _task_containment_probe(self, native: NativeImage) -> dict[str, Any]:
        """Require same-kernel private PID evidence before any task service sees the agent network.

        The disposable probe receives no mounts or controller credentials. The original application
        entrypoint and healthcheck are disabled. Colab/shared-procfs and remote-daemon uncertainty
        are rejected for task Compose even when agent-only execution permits an adapted host.
        """
        key = (native.base_id, native.platform)
        if key in self.task_containment:
            return copy.deepcopy(self.task_containment[key])
        service = f"containment-probe-{uuid.uuid4().hex}"
        container = ""
        try:
            container = (await self.command(
                "docker", "run", "-d", "--name", service, "--network", "none", "--no-healthcheck",
                "--label", f"com.docker.compose.project={self.project}",
                "--label", f"com.docker.compose.service={service}", "--cap-drop", "NET_RAW",
                "--cap-drop", "NET_ADMIN", "--security-opt", "no-new-privileges",
                *(["--platform", native.platform] if native.platform else []),
                "--entrypoint", "/bin/sh", native.base_id, "-c", "while :; do sleep 3600; done", timeout=30
            )).decode().strip()
            if not CONTAINER_ID.fullmatch(container):
                container = ""
                raise TaskEnvironmentError("containment probe did not return a full container ID")
            self.probes[container] = service
            raw = await self._owned(container, service)
            evidence = container_evidence(raw, container, self.project, service)
            host_boot = host_ns = init_ns = probe = None
            error = None
            try:
                host_boot, host_ns, init_ns = self._probe_host(evidence["pid"])
                probe = (await self.command("docker", "container", "exec", container, "/bin/sh", "-c",
                    "readlink /proc/self/ns/pid; readlink /proc/1/ns/pid; "
                    "cat /proc/sys/kernel/random/boot_id", timeout=30)).decode()
            except (OSError, TaskEnvironmentError) as exc:
                error = str(exc)
            proof = isolation_evidence(evidence, probe, host_boot_id=host_boot,
                                       host_pid_namespace=host_ns, init_pid_namespace=init_ns)
            if error:
                proof["error"] = error
            path = self.state_dir / native.key / "task-containment.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(proof, indent=2))
            if proof["status"] != "supported":
                message = "task Compose requires verified private PID containment before services start: "
                raise TaskEnvironmentError(message + proof["reason"])
        finally:
            if not container:
                found = (await self.command("docker", "container", "ls", "--all", "--quiet", "--no-trunc",
                    "--filter", f"label=com.docker.compose.project={self.project}",
                    "--filter", f"label=com.docker.compose.service={service}")).decode().split()
                if len(found) > 1:
                    raise TaskEnvironmentError("containment probe cleanup inventory is ambiguous")
                container = found[0] if found else ""
            if container:
                self.probes[container] = service
                await self._remove_container(container, service)
                self.probes.pop(container, None)
        self.task_containment[key] = proof
        return copy.deepcopy(proof)

    async def prepare(self, agent: AgentSpec, task: Task, workspace: pathlib.Path, *, reset: bool,
                      restored: bool = False) -> dict[str, Any]:
        await self.native_image(task)
        async with self.runtime_lock:
            return await self._prepare(agent, task, workspace, reset=reset, restored=restored)

    async def _prepare(self, agent: AgentSpec, task: Task, workspace: pathlib.Path, *, reset: bool,
                      restored: bool = False) -> dict[str, Any]:
        native = await self.native_image(task)
        workspace = workspace.resolve()
        previous = self.active.get(str(workspace))
        if previous and not reset:
            if previous[2].key != native.key:
                raise TaskEnvironmentError("carried harness memory across different task environments is unsupported")
            group = self.groups[self.workspace_groups[str(workspace)]]
            if previous[1] in group.paused:
                await self._owned(previous[1], agent.id)
                await self.command("docker", "container", "unpause", previous[1], timeout=30)
                group.paused.remove(previous[1])
            receipt = self.receipt(previous[1], native)
            receipt["adaptations"] = self.adaptations(task)
            receipt["applied_resources"] = self.applied_resources(task)
            return receipt
        for old in list(self.groups.values()):
            if agent.id in old.participants and not old.released:
                if self.task_services == "shared":
                    raise TaskEnvironmentError("previous shared task has not completed team verification")
                await self._release(old)
        group = self._new_group(agent, task, native, workspace)
        main = group.stack.main_by_agent[agent.id] if group.stack is not None else {}
        task_env = {**(main.get("environment") or {}), **((task.config.get("environment") or {}).get("env") or {})}
        if any(key.startswith(("OPENCODE_", "MESOCOSM_")) for key in task_env):
            raise TaskEnvironmentError("task environment cannot override harness authentication or controller settings")
        for mount in main.get("volumes") or []:
            target = pathlib.PurePosixPath(mount["target"])
            if pathlib.PurePosixPath(native.workdir).is_relative_to(target):
                raise TaskEnvironmentError("task main mounts cannot replace the private agent workspace")
        verifier = task.config.get("verifier") or {}
        if main.get("volumes") and not (verifier.get("environment") is not None or
                                         verifier.get("environment_mode") == "separate"):
            raise TaskEnvironmentError("task main mounts require a separate verifier with explicit artifact inputs")
        workspace.mkdir(parents=True, exist_ok=True)
        platform_args = ["--platform", native.platform] if native.platform else []
        ownership = ["--label", f"com.docker.compose.project={self.project}",
                     "--label", "com.docker.compose.service=workspace-staging"]
        if not restored:
            if any(workspace.iterdir()):
                raise TaskEnvironmentError(f"{agent.id}: native environment needs an empty initial workspace")
            staging = (await self.command("docker", "create", *platform_args, *ownership, "--network", "none",
                                          "--entrypoint", "/bin/sh", native.image, "-c", "true")).decode().strip()
            try:
                await self.command("docker", "cp", f"{staging}:{native.workdir}/.", str(workspace))
            finally:
                await self.command("docker", "rm", "-f", "-v", staging)
        await self.command("docker", "run", "--rm", *platform_args, *ownership,
                           "--network", "none", "--entrypoint", "/bin/sh",
                           "--workdir", native.workdir, "-v", f"{workspace}:{native.workdir}", native.image,
                           "-c", 'test -w . || { echo "Native user cannot write mounted workspace" >&2; exit 1; }')
        build_dir = self.state_dir / native.key / agent.id
        build_dir.mkdir(parents=True, exist_ok=True)
        startup = build_dir / "start.sh"
        startup.write_text(startup_script(native.config))
        service = self.base_compose["services"][agent.id]
        mounts = []
        for mount in service.get("volumes") or []:
            source, target, *mode = mount.split(":")
            if target == "/work":
                mounts.append(f"{workspace}:{native.workdir}")
            elif target.startswith("/root/.config/opencode/"):
                destination = target.replace("/root/.config/opencode", "/etc/opencode")
                if destination == "/etc/opencode/opencode.json":
                    config = json.loads(pathlib.Path(source).read_text())
                    if agent.persona and agent.persona.brief:
                        config["instructions"] = [*(config.get("instructions") or []), "/etc/opencode/AGENTS.md"]
                    rendered = build_dir / "opencode.json"
                    rendered.write_text(json.dumps(config))
                    source = str(rendered)
                mounts.append(":".join([source, destination, *mode]))
            elif target == "/root/.gitconfig":
                mounts.append(":".join([source, "/etc/gitconfig", *mode]))
            else:
                mounts.append(mount)
        mounts.append(f"{startup}:/opt/agent-start.sh:ro")
        env = {**_compose_literals(task_env), **(service.get("environment") or {}),
               "OPENCODE_CONFIG": "/etc/opencode/opencode.json", "OPENCODE_CONFIG_DIR": "/etc/opencode",
               "OPENCODE_CONFIG_CONTENT": CONTEXT_CONFIG}
        if group.stack is not None:
            for key in ("NO_PROXY", "no_proxy"):
                if key in env:
                    env[key] += "," + ",".join(group.stack.aliases_by_agent[agent.id])
        # The source main maps to this authenticated runtime, never an additional actor.
        compiled_main = _compose_literals(main)
        native_fields = {key: value for key, value in compiled_main.items() if key not in (
            "build", "image", "volumes", "environment", "networks", "command", "entrypoint", "healthcheck",
            "working_dir", "user", "depends_on")}
        network = copy.deepcopy(service["networks"])
        for name_, settings in (main.get("networks") or {}).items():
            network[name_]["aliases"] = settings.get("aliases") or []
        controlled_health = service.get("healthcheck") or {}
        original_health = main.get("healthcheck")
        if original_health is None and native.config.get("Healthcheck"):
            image_health = native.config["Healthcheck"]
            original_health = {"test": image_health.get("Test") or ["NONE"]}
            for key in ("Interval", "Timeout", "StartPeriod", "StartInterval"):
                if image_health.get(key):
                    original_health[{"StartPeriod": "start_period", "StartInterval": "start_interval"}.get(
                        key, key.lower())] = f"{image_health[key]}ns"
            if image_health.get("Retries"):
                original_health["retries"] = image_health["Retries"]
        health = copy.deepcopy(controlled_health)
        if original_health and not original_health.get("disable"):
            original_test = original_health.get("test") or ["NONE"]
            if original_test != ["NONE"]:
                if not isinstance(original_test, list) or original_test[0] not in ("CMD", "CMD-SHELL"):
                    raise TaskEnvironmentError("unsupported native main healthcheck command")
                native_check = (shlex.join(original_test[1:]) if original_test[0] == "CMD" else original_test[1])
                base_test = health.get("test") or ["CMD", "true"]
                harness_check = shlex.join(base_test[1:]) if base_test[0] == "CMD" else base_test[1]
                health.update({key: value for key, value in original_health.items() if key not in ("test", "disable")})
                health["test"] = ["CMD-SHELL", f"({harness_check}) && ({_compose_literals(native_check)})"]
        self.rendered["services"][agent.id] = {
            **{key: value for key, value in service.items() if key not in ("build", "image", "volumes", "environment")},
            **native_fields, "image": native.image, "entrypoint": ["/bin/sh", "/opt/agent-start.sh"], "command": [],
            "working_dir": native.workdir, "user": native.config.get("User") or "root", "networks": network,
            "volumes": [*mounts, *(compiled_main.get("volumes") or [])], "environment": env,
            "depends_on": {**(service.get("depends_on") or {}), **(main.get("depends_on") or {})},
            **({"healthcheck": health} if health else {}), **self.applied_resources(task),
        }
        await self._start_services(group)
        self._write_rendered()
        # Preserve a task-local render for evidence as well as the complete cleanup companion.
        override = build_dir / "compose.yaml"
        override.write_text(yaml.safe_dump(self.rendered, sort_keys=False))
        await self.command("docker", "compose", "-f", str(self.runtime_file), "up", "-d", "--no-deps",
                           "--force-recreate", "--wait", agent.id, timeout=180)
        raw_id = await self.command("docker", "compose", "-f", str(self.runtime_file), "ps", "-q", agent.id)
        container = raw_id.decode().strip()
        if not container:
            raise TaskEnvironmentError(f"{agent.id}: native agent container is missing after startup")
        group.containers[agent.id] = container
        await self._owned(container, agent.id)
        self.active[str(workspace)] = (agent.id, container, native)
        receipt = self.receipt(container, native)
        cfg = task.config.get("environment") or {}
        receipt["requested_resources"] = {k: cfg[k] for k in ("cpus", "memory_mb", "memory", "storage_mb",
                                                               "storage", "gpus") if k in cfg}
        receipt["applied_resources"] = self.applied_resources(task)
        receipt["unsupported_resources"] = {k: v for k, v in receipt["requested_resources"].items()
                                             if k in ("storage_mb", "storage") or
                                             (k in ("cpus", "memory_mb", "memory") and not self.limits_available)}
        receipt["adaptations"] = self.adaptations(task)
        receipt["task_services"] = {"scope": self.task_services, "group_id": group.key,
                                    "services": list(group.stack.services) if group.stack else [],
                                    "volumes": list(group.stack.volumes) if group.stack else []}
        receipt["task_service_containment"] = group.containment
        if self.egress is not None:
            isolation = (await self.egress.verify([agent.id]))[agent.id]
            receipt["isolation"] = isolation.to_dict()
            if not isolation.ok:
                raise TaskEnvironmentError(f"{agent.id}: native container failed isolation check: {isolation.failures}")
        return receipt

    def receipt(self, container: str, native: NativeImage) -> dict[str, Any]:
        return {"container": container, "image": native.image, "base_image_id": native.base_id,
                "workdir": native.workdir, "user": native.config.get("User") or "root",
                "environment_key": native.key, "verifier_network": self.verifier_network}

    def adaptations(self, task: Task) -> list[str]:
        env = task.config.get("environment") or {}
        reasons = ["native_image_with_opencode", "workspace_bind_mount"]
        if env.get("storage_mb") or env.get("storage"):
            reasons.append("storage_quota_unenforced")
        if env.get("allow_internet"):
            reasons.append("agent_internet_governed_by_world_policy")
        if self.verifier_network != "none":
            reasons.append("verifier_only_internet")
        if resource_options(task) and not self.limits_available:
            reasons.append("cpu_memory_limits_unenforced")
        return reasons

    def applied_resources(self, task: Task) -> dict[str, Any]:
        return resource_options(task) if self.limits_available else {}

    def _task_network(self, agent_id: str) -> str:
        network = next(iter(self.base_compose["services"][agent_id]["networks"]))
        settings = (self.base_compose.get("networks") or {}).get(network) or {}
        if settings.get("internal") is not True:
            raise TaskEnvironmentError("service verification requires an internal task network")
        return str(settings.get("name") or f"{self.project}_{network}")

    async def _capture_service_logs(self, group: TaskGroup, logs_dir: pathlib.Path) -> None:
        if group.stack is None or not group.stack.services:
            return
        logs_dir.mkdir(parents=True, exist_ok=True)
        try:
            output = await self.command("docker", "compose", "-f", str(self.runtime_file), "logs", "--no-color",
                                        "--timestamps", *group.stack.services, timeout=30, include_stderr=True)
            (logs_dir / "task-services.log").write_bytes(output)
            receipt: dict[str, Any] = {"status": "captured", "services": list(group.stack.services)}
        except Exception as exc:
            receipt = {"status": "unavailable", "services": list(group.stack.services), "error": str(exc)}
        (logs_dir / "task-services-log.json").write_text(json.dumps(receipt, indent=2))

    async def _shared_publications(self, group: TaskGroup) -> None:
        with tempfile.TemporaryDirectory(prefix="team-publications-") as temporary:
            for agent_id in group.expected:
                destination = pathlib.Path(temporary) / agent_id
                try:
                    await self.command("docker", "cp", f"{group.containers[agent_id]}:/logs/artifacts",
                                       str(destination))
                except TaskEnvironmentError as exc:
                    missing = ("could not find the file", "no such file or directory")
                    if any(text in str(exc).lower() for text in missing):
                        group.publications[agent_id] = "absent"
                        continue
                    raise
                if not stat.S_ISDIR(destination.lstat().st_mode) or any(destination.iterdir()):
                    raise TaskEnvironmentError("shared verification cannot consume any agent's published artifacts")
                group.publications[agent_id] = "empty"

    async def _measure(self, task: Task, group: TaskGroup, agent_id: str, workspace: pathlib.Path,
                       logs_dir: pathlib.Path, script: str) -> Reward:
        verifier = task.config.get("verifier") or {}
        separate = verifier.get("environment") is not None or verifier.get("environment_mode") == "separate"
        if separate:
            from mesocosm.env.task_verifier import prepare_verifier

            network = self._task_network(agent_id) if group.stack is not None else "none"
            plan = await prepare_verifier(task, group.native, group.containers[agent_id], workspace, logs_dir,
                                          self.command, network=network, service_scope=self.task_services,
                                          script=script, project=self.project)
            self.verifier_cleanup.extend(plan.cleanup)
            metadata = {**self.receipt(group.containers[agent_id], group.native), **plan.metadata,
                        "task_services": self.task_services, "task_group": group.key,
                        "quiesced_containers": dict(group.containers), "live_process_scope": "sidecars_only"}
            (logs_dir / "environment.json").write_text(json.dumps(metadata, indent=2))
            failure: BaseException | None = None
            try:
                out = await self.command(*plan.command, timeout=float(verifier.get("timeout_sec") or 600),
                                         include_stderr=True)
                (logs_dir / "verifier.log").write_bytes(out)
            except BaseException as exc:
                failure = exc
                (logs_dir / "verifier.log").write_text(str(exc))
                raise
            finally:
                cleanup_errors = []
                for command in plan.cleanup:
                    try:
                        await self._verifier_cleanup(command)
                    except TaskEnvironmentError as exc:
                        cleanup_errors.append(str(exc))
                (logs_dir / "cleanup.json").write_text(json.dumps({"confirmed": not cleanup_errors,
                                                                  "errors": cleanup_errors}, indent=2))
                if cleanup_errors:
                    message = "verifier cleanup failed: " + "; ".join(cleanup_errors)
                    if failure is not None:
                        message = f"{failure}; {message}"
                    raise TaskEnvironmentError(message) from failure
            for name in ("reward.txt", "reward.json", "checkpoints.json"):
                if (logs_dir / "verifier" / name).is_symlink():
                    raise TaskEnvironmentError("verifier reward outputs must be regular files")
            reward = read_reward(logs_dir)
            if reward.value is None or not math.isfinite(reward.value):
                raise TaskEnvironmentError(f"{task.name}: verifier produced no valid reward")
            return reward
        return await self._verify_snapshot(task, workspace, logs_dir, script)

    async def verify(self, task: Task, workspace: pathlib.Path, logs_dir: pathlib.Path,
                     script: str = "test.sh") -> Reward:
        return await self._verify(task, workspace, logs_dir, script)

    async def verify_with_artifacts(self, task: Task, workspace: pathlib.Path, logs_dir: pathlib.Path,
                                    spec: ArtifactSpec, *, on_capture: Callable[[dict[str, Any]], None]) -> Reward:
        """Capture acknowledged quiescent inputs before the unchanged original verifier starts."""
        if self.task_services == "shared":
            raise TaskEnvironmentError("rejected_unsupported: artifact exchange requires isolated verification")
        workspace = workspace.resolve()
        active = self.active.get(str(workspace))
        if active is None:
            raise TaskEnvironmentError(f"{task.name}: no native task environment was prepared")
        agent_id, container_id, native = active
        group = self.groups[self.workspace_groups[str(workspace)]]
        sources = [pathlib.PurePosixPath(native.workdir) / name for name in spec.paths]
        main = group.stack.main_by_agent[agent_id] if group.stack is not None else {}
        for mount in main.get("volumes") or []:
            target = pathlib.PurePosixPath(mount["target"])
            if any(source.is_relative_to(target) or target.is_relative_to(source) for source in sources):
                raise TaskEnvironmentError("rejected_unsupported: artifact exchange paths overlap task child mounts")
        verifier = task.config.get("verifier") or {}
        if verifier.get("environment") is not None or verifier.get("environment_mode") == "separate":
            from mesocosm.env.task_verifier import artifact_specs

            inputs = [pathlib.PurePosixPath(item.source) for item in artifact_specs(task)]
            if any(not any(source.is_relative_to(original) for original in inputs) for source in sources):
                raise TaskEnvironmentError("rejected_unsupported: artifact exchange paths must be "
                                           "original verifier inputs")

        def capture() -> None:
            snapshot = capture_artifacts(workspace, spec)
            snapshot["capture_evidence"] = {
                "boundary": "quiesced_before_original_verifier", "task": task.name, "agent_id": agent_id,
                "container_id": container_id, "quiesced_containers": dict(group.containers),
                "workspace": str(workspace), "native_workdir": native.workdir,
                "measurement_scope": "original_task_not_individual_file",
            }
            on_capture(snapshot)

        return await self._verify(task, workspace, logs_dir, "test.sh", before_measure=capture)

    async def _verify(self, task: Task, workspace: pathlib.Path, logs_dir: pathlib.Path,
                      script: str, *, before_measure: Callable[[], None] | None = None) -> Reward:
        workspace = workspace.resolve()
        active = self.active.get(str(workspace))
        if active is None:
            raise TaskEnvironmentError(f"{task.name}: no native task environment was prepared")
        agent_id = active[0]
        group = self.groups[self.workspace_groups[str(workspace)]]
        if self.task_services != "shared":
            try:
                await self._quiesce(group)
                if before_measure is not None:
                    before_measure()
                return await self._measure(task, group, agent_id, workspace, logs_dir, script)
            finally:
                await self._capture_service_logs(group, logs_dir)
                if self.memory == "reset" and (not task.has_side_tests or script == "side_test.sh"):
                    async with self.runtime_lock:
                        await self._release(group)
        if script != "test.sh" or task.has_side_tests:
            raise TaskEnvironmentError("shared task verification requires one original main verifier")
        async with self.runtime_lock:
            if agent_id in group.logs:
                raise TaskEnvironmentError("agent reached shared verification more than once")
            group.logs[agent_id] = logs_dir.resolve()
            if group.future is None:
                group.future = asyncio.get_running_loop().create_future()
            future = group.future
            if set(group.logs) == set(group.expected):
                group.grading = asyncio.create_task(self._grade_team(group, script))
        # An absent or failed participant cannot leave peers waiting indefinitely. The runner also
        # cancels all peers immediately on agent failure; shielding prevents one waiter owning the future.
        timeout = float((task.config.get("agent") or {}).get("timeout_sec") or 3600)
        timeout += float((task.config.get("verifier") or {}).get("timeout_sec") or 600)
        try:
            return copy.deepcopy(await asyncio.wait_for(asyncio.shield(future), timeout))
        except (TimeoutError, asyncio.CancelledError) as exc:
            if not future.done():
                error = TaskEnvironmentError("team verification interrupted before all participants finished")
                future.set_exception(error)
                future.exception()
            if group.grading is not None and not group.grading.done():
                group.grading.cancel()
                await asyncio.gather(group.grading, return_exceptions=True)
            if isinstance(exc, TimeoutError):
                raise TaskEnvironmentError("team verification timed out waiting for participants or verifier") from exc
            raise

    async def _grade_team(self, group: TaskGroup, script: str) -> None:
        assert group.future is not None
        try:
            if set(group.participants) != set(group.expected):
                raise TaskEnvironmentError("shared verifier is missing prepared agent participants")
            await self._quiesce(group)
            leader = group.expected[0]
            logs = group.logs[leader]
            await self._shared_publications(group)
            try:
                reward = await self._measure(group.task, group, leader, pathlib.Path(group.participants[leader]),
                                             logs, script)
            finally:
                await self._capture_service_logs(group, logs)
            metadata = json.loads((logs / "environment.json").read_text())
            metadata.update(measurement_scope="team", measurement_id=uuid.uuid4().hex,
                            participant_ids=list(group.expected), task_group=group.key,
                            quiesced_containers=dict(group.containers), published_artifacts=dict(group.publications))
            (logs / "environment.json").write_text(json.dumps(metadata, indent=2))
            for agent_id in group.expected:
                if agent_id == leader:
                    continue
                destination = group.logs[agent_id]
                destination.mkdir(parents=True, exist_ok=True)
                shutil.copytree(logs / "verifier", destination / "verifier", symlinks=True, dirs_exist_ok=True)
                for filename in ("verifier.log", "task-services.log", "task-services-log.json", "cleanup.json"):
                    if (logs / filename).is_file():
                        shutil.copyfile(logs / filename, destination / filename)
                (destination / "environment.json").write_text(json.dumps(metadata, indent=2))
            async with self.runtime_lock:
                await self._release(group)
            if not group.future.done():
                group.future.set_result(reward)
        except BaseException as exc:
            for logs in group.logs.values():
                logs.mkdir(parents=True, exist_ok=True)
                (logs / "team-verifier-error.txt").write_text(str(exc) or type(exc).__name__)
            if not group.future.done():
                group.future.set_exception(TaskEnvironmentError(f"shared original verification failed: {exc}"))
                group.future.exception()
            # Owned task resources remain registered for finish() even if grading or cleanup fails.

    async def _verify_snapshot(self, task: Task, workspace: pathlib.Path, logs_dir: pathlib.Path,
                     script: str = "test.sh") -> Reward:
        from mesocosm.adapters.harbor.task import verifier_command

        active = self.active.get(str(workspace.resolve()))
        if active is None:
            raise TaskEnvironmentError(f"{task.name}: no native task environment was prepared")
        _, container, native = active
        snapshot = f"mesocosm-task-verifier:{uuid.uuid4().hex}"
        name = f"verifier-{uuid.uuid4().hex}"
        cleanup = [("docker", "rm", "-f", "-v", name), ("docker", "image", "rm", "-f", snapshot)]
        logs_dir.mkdir(parents=True, exist_ok=True)
        # Logs are a dedicated verifier output directory, never a source tree. Keep the native user.
        logs_dir.chmod(0o777)
        (logs_dir / "verifier").mkdir(exist_ok=True)
        (logs_dir / "verifier").chmod(0o777)
        for filename in ("reward.txt", "reward.json", "checkpoints.json"):
            (logs_dir / "verifier" / filename).unlink(missing_ok=True)
        try:
            snapshot_id = (await self.command("docker", "commit", container, snapshot)).decode().strip()
            self.verifier_cleanup.extend(cleanup)
            metadata = {**self.receipt(container, native), "verifier_image_id": snapshot_id,
                        "coverage": "filesystem_snapshot_only", "adaptations": self.adaptations(task)}
            (logs_dir / "environment.json").write_text(json.dumps(metadata, indent=2))
            cmd = verifier_command(task, workspace=workspace, logs_dir=logs_dir, image=snapshot,
                                   workdir=native.workdir, container_name=name, script=script,
                                   network=self.verifier_network)
            cmd[2:2] = ["--label", f"com.docker.compose.project={self.project}",
                        "--label", f"com.docker.compose.service={name}"]
            for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy",
                        "all_proxy", "no_proxy", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENCODE_CONFIG",
                        "OPENCODE_CONFIG_DIR", "OPENCODE_CONFIG_CONTENT", "OPENCODE_SERVER_PASSWORD",
                        "OPENCODE_SERVER_USERNAME"):
                cmd[2:2] = ["-e", f"{key}="]
            for name_, value in self.applied_resources(task).items():
                cmd[2:2] = [{"cpus": "--cpus", "mem_limit": "--memory"}[name_], str(value)]
            timeout = float((task.config.get("verifier") or {}).get("timeout_sec") or 600)
            try:
                out = await self.command(*cmd, timeout=timeout, include_stderr=True)
                (logs_dir / "verifier.log").write_bytes(out)
            except TaskEnvironmentError as e:
                (logs_dir / "verifier.log").write_text(str(e))
                raise
            for filename in ("reward.txt", "reward.json", "checkpoints.json"):
                if (logs_dir / "verifier" / filename).is_symlink():
                    raise TaskEnvironmentError("verifier reward outputs must be regular files")
            reward = read_reward(logs_dir)
            if reward.value is None or not math.isfinite(reward.value):
                raise TaskEnvironmentError(f"{task.name}: verifier produced no valid reward")
            return reward
        finally:
            # A timed-out docker client can leave its container behind.
            cleanup_errors = []
            for args in cleanup:
                if args not in self.verifier_cleanup:
                    continue
                try:
                    await self._verifier_cleanup(args)
                except TaskEnvironmentError as exc:
                    cleanup_errors.append(str(exc))
            if cleanup_errors:
                raise TaskEnvironmentError("verifier cleanup failed: " + "; ".join(cleanup_errors))


def resource_options(task: Task) -> dict[str, Any]:
    env = task.config.get("environment") or {}
    out: dict[str, Any] = {}
    if env.get("cpus") is not None:
        out["cpus"] = float(env["cpus"])
    if env.get("memory_mb") is not None:
        out["mem_limit"] = f"{int(env['memory_mb'])}m"
    elif env.get("memory"):
        out["mem_limit"] = str(env["memory"])
    return out
