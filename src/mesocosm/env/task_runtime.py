"""Run Harbor tasks in their native image, adding only the unmodified OpenCode harness.

The task's root filesystem, user, working directory and startup command survive. Its working tree
is copied out before the host workspace is mounted over it. Verification uses a private snapshot of
the resulting container, with tests introduced only after the agent has finished (ADR-0042).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import pathlib
import shlex
import uuid
from dataclasses import dataclass
from typing import Any

import yaml

from mesocosm.adapters.harbor.task import HarborTask, Reward, read_reward
from mesocosm.core.manifest import AgentSpec
from mesocosm.env.egress import DockerEgressVerifier


class TaskEnvironmentError(RuntimeError):
    """A native task environment could not be built, started or verified."""


def environment_source(task: HarborTask) -> tuple[str | None, pathlib.Path]:
    """Select the task's declared image or Dockerfile; never substitute a generic environment."""
    root = task.path / "environment"
    if any((root / name).exists() for name in ("docker-compose.yaml", "docker-compose.yml", "compose.yaml")):
        raise TaskEnvironmentError(f"{task.name}: task Compose environments are unsupported")
    image = (task.config.get("environment") or {}).get("docker_image")
    if image:
        return str(image), root
    if not (root / "Dockerfile").is_file():
        raise TaskEnvironmentError(f"{task.name}: declare environment.docker_image or environment/Dockerfile")
    return None, root


def environment_key(task: HarborTask, opencode_version: str) -> str:
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
    """A Debian/Ubuntu native image with the published harness, retaining its final user and cwd."""
    return ("FROM node:22-bookworm-slim AS coding_harness\n"
            f"RUN npm install -g --prefix /opt/coding-harness {shlex.quote('opencode-ai@' + version)}\n"
            f"FROM {base}\nUSER root\n"
            "RUN command -v apt-get >/dev/null || "
            "{ echo 'Native harness currently requires Debian/Ubuntu' >&2; exit 1; }; "
            "apt-get update && apt-get install -y --no-install-recommends libstdc++6 curl ca-certificates "
            "&& rm -rf /var/lib/apt/lists/*\n"
            "COPY --from=coding_harness /usr/local/bin/node /usr/local/bin/node\n"
            "COPY --from=coding_harness /opt/coding-harness /opt/coding-harness\n"
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


class DockerTaskRuntime:
    def __init__(self, compose_file: pathlib.Path, state_dir: pathlib.Path, opencode_version: str = "1.19.3", *,
                 verifier_network: str = "none", allow_unenforced_storage: bool = False,
                 allow_unenforced_resources: bool = False, egress: DockerEgressVerifier | None = None):
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

    async def native_image(self, task: HarborTask) -> NativeImage:
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
        if verifier.get("environment") or verifier.get("environment_mode") == "separate":
            raise TaskEnvironmentError(f"{task.name}: separate verifier images are unsupported")
        if verifier.get("requires_live_processes"):
            raise TaskEnvironmentError(f"{task.name}: verification captures filesystems, not live processes")
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
            build_dir = self.state_dir / key
            build_dir.mkdir(parents=True, exist_ok=True)
            timeout = float((task.config.get("environment") or {}).get("build_timeout_sec") or 1200)
            base = declared or f"mesocosm-task-base:{key}"
            if declared:
                await self.command("docker", "pull", base, timeout=timeout)
            else:
                await self.command("docker", "build", "-t", base, "-f", str(context / "Dockerfile"), str(context),
                                   timeout=timeout)
            inspected = json.loads((await self.command("docker", "image", "inspect", base)).decode())[0]
            config = inspected["Config"]
            workdir = config.get("WorkingDir") or "/"
            if not workdir.startswith("/") or workdir in ("/", "/usr", "/etc", "/bin", "/root"):
                raise TaskEnvironmentError(f"{task.name}: native WORKDIR {workdir!r} cannot be workspace-mounted")
            source = build_dir / "Dockerfile"
            selected = f"mesocosm-task-selected:{key}"
            await self.command("docker", "tag", inspected["Id"], selected)
            source.write_text(harness_dockerfile(selected, self.opencode_version, config.get("User") or ""))
            image = f"mesocosm-task-agent:{key}"
            await self.command("docker", "build", "-t", image, "-f", str(source), str(build_dir), timeout=timeout)
            native = NativeImage(image, inspected["Id"], config, workdir, key)
            self.images[key] = native
            return native

    async def prebuild(self, tasks: list[HarborTask]) -> list[dict[str, Any]]:
        """Build declared environments before the run's exposure clock starts."""
        prepared = []
        for task in tasks:
            native = await self.native_image(task)
            prepared.append({"task": task.name, **self.receipt("", native), "adaptations": self.adaptations(task)})
        return prepared

    async def prepare(self, agent: AgentSpec, task: HarborTask, workspace: pathlib.Path, *, reset: bool,
                      restored: bool = False) -> dict[str, Any]:
        native = await self.native_image(task)
        workspace = workspace.resolve()
        previous = self.active.get(str(workspace))
        if previous and not reset:
            if previous[2].key != native.key:
                raise TaskEnvironmentError("carried harness memory across different task environments is unsupported")
            receipt = self.receipt(previous[1], native)
            receipt["adaptations"] = self.adaptations(task)
            receipt["applied_resources"] = self.applied_resources(task)
            return receipt
        workspace.mkdir(parents=True, exist_ok=True)
        if not restored:
            if any(workspace.iterdir()):
                raise TaskEnvironmentError(f"{agent.id}: native environment needs an empty initial workspace")
            staging = (await self.command("docker", "create", "--network", "none", "--entrypoint", "/bin/sh",
                                          native.image, "-c", "true")).decode().strip()
            try:
                await self.command("docker", "cp", f"{staging}:{native.workdir}/.", str(workspace))
            finally:
                await self.command("docker", "rm", "-f", staging)
        await self.command("docker", "run", "--rm", "--network", "none", "--entrypoint", "/bin/sh",
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
        env = {**(service.get("environment") or {}),
               **((task.config.get("environment") or {}).get("env") or {}),
               "OPENCODE_CONFIG": "/etc/opencode/opencode.json", "OPENCODE_CONFIG_DIR": "/etc/opencode"}
        override = build_dir / "compose.yaml"
        # A complete service replacement avoids retaining the generic image's build or masking /app with /work.
        merged = {**self.base_compose, "services": {**self.base_compose["services"], agent.id: {
            **{k: v for k, v in service.items() if k not in ("build", "image", "volumes", "environment")},
            "image": native.image, "entrypoint": ["/bin/sh", "/opt/agent-start.sh"], "command": [],
            "working_dir": native.workdir, "user": native.config.get("User") or "root",
            "volumes": mounts, "environment": env,
            **self.applied_resources(task),
        }}}
        override.write_text(yaml.safe_dump(merged, sort_keys=False))
        await self.command("docker", "compose", "-f", str(override), "up", "-d", "--no-deps", "--force-recreate",
                           "--wait", agent.id, timeout=180)
        raw_id = await self.command("docker", "compose", "-f", str(override), "ps", "-q", agent.id)
        container = raw_id.decode().strip()
        if not container:
            raise TaskEnvironmentError(f"{agent.id}: native agent container is missing after startup")
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

    def adaptations(self, task: HarborTask) -> list[str]:
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

    def applied_resources(self, task: HarborTask) -> dict[str, Any]:
        return resource_options(task) if self.limits_available else {}

    async def verify(self, task: HarborTask, workspace: pathlib.Path, logs_dir: pathlib.Path,
                     script: str = "test.sh") -> Reward:
        from mesocosm.adapters.harbor.task import verifier_command

        active = self.active.get(str(workspace.resolve()))
        if active is None:
            raise TaskEnvironmentError(f"{task.name}: no native task environment was prepared")
        _, container, native = active
        snapshot = f"mesocosm-task-verifier:{uuid.uuid4().hex}"
        name = f"verifier-{uuid.uuid4().hex}"
        logs_dir.mkdir(parents=True, exist_ok=True)
        # Logs are a dedicated verifier output directory, never a source tree. Keep the native user.
        logs_dir.chmod(0o777)
        (logs_dir / "verifier").mkdir(exist_ok=True)
        (logs_dir / "verifier").chmod(0o777)
        for filename in ("reward.txt", "reward.json", "checkpoints.json"):
            (logs_dir / "verifier" / filename).unlink(missing_ok=True)
        try:
            snapshot_id = (await self.command("docker", "commit", container, snapshot)).decode().strip()
            metadata = {**self.receipt(container, native), "verifier_image_id": snapshot_id,
                        "coverage": "filesystem_snapshot_only", "adaptations": self.adaptations(task)}
            (logs_dir / "environment.json").write_text(json.dumps(metadata, indent=2))
            cmd = verifier_command(task, workspace=workspace, logs_dir=logs_dir, image=snapshot,
                                   workdir=native.workdir, container_name=name, script=script,
                                   network=self.verifier_network)
            for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy",
                        "all_proxy", "no_proxy", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENCODE_CONFIG",
                        "OPENCODE_CONFIG_DIR"):
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
            reward = read_reward(logs_dir)
            if reward.value is None or not math.isfinite(reward.value):
                raise TaskEnvironmentError(f"{task.name}: verifier produced no valid reward")
            return reward
        finally:
            # A timed-out docker client can leave its container behind.
            for args in (("docker", "rm", "-f", name), ("docker", "image", "rm", "-f", snapshot)):
                try:
                    await self.command(*args)
                except TaskEnvironmentError:
                    pass


def resource_options(task: HarborTask) -> dict[str, Any]:
    env = task.config.get("environment") or {}
    out: dict[str, Any] = {}
    if env.get("cpus") is not None:
        out["cpus"] = float(env["cpus"])
    if env.get("memory_mb") is not None:
        out["mem_limit"] = f"{int(env['memory_mb'])}m"
    elif env.get("memory"):
        out["mem_limit"] = str(env["memory"])
    return out
