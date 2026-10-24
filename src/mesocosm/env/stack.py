"""Bring the Compose sandbox up and down from the control plane (scenario stages change the services'
configuration, so each stage reconciles the stack before its run)."""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import re
import signal
import time
from typing import Any

import yaml

from mesocosm.env.lifecycle import CONTAINER_ID, container_evidence, isolation_evidence

TEARDOWN_TIMEOUT_SECONDS = 120.0
REAP_TIMEOUT_SECONDS = 5.0
WORKLOAD_TIMEOUT_SECONDS = 30.0


class StackCleanupError(RuntimeError):
    """The previous world may still exist; another world must not be started."""

    def __init__(self, compose_file: str, message: str):
        self.compose_file = compose_file
        super().__init__(message)


class DockerStack:
    def __init__(self, compose_file: str | pathlib.Path):
        self.compose_file = str(compose_file)
        self.cleanup_error: StackCleanupError | None = None
        self.cleanup_evidence: dict[str, Any] | None = None
        self._isolation: dict[str, dict[str, Any]] = {}

    async def _docker(self, *args: str, timeout: float | None = None) -> str:
        proc = await asyncio.create_subprocess_exec("docker", *args,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                                                    start_new_session=True)
        communication = asyncio.create_task(proc.communicate())
        try:
            out, _ = await asyncio.wait_for(asyncio.shield(communication), timeout)
        except (TimeoutError, asyncio.CancelledError):
            # Only the client process group created above. Workload termination uses Docker's
            # exact container ID API below; this cleanup must never signal an inspected host PID.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(asyncio.shield(communication), REAP_TIMEOUT_SECONDS)
            except TimeoutError:
                communication.cancel()
                await asyncio.gather(communication, return_exceptions=True)
            raise
        text = out.decode("utf-8", "replace")
        if proc.returncode != 0:
            raise RuntimeError(f"docker {' '.join(args)} failed ({proc.returncode}): {text[-2000:]}")
        return text

    async def _compose(self, *args: str, timeout: float | None = None) -> str:
        return await self._docker("compose", "-f", self.compose_file, *args, timeout=timeout)

    def _project(self, agent_id: str) -> str:
        config = yaml.safe_load(pathlib.Path(self.compose_file).read_text())
        project = config.get("name")
        if not isinstance(project, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", project):
            raise ValueError("workload control requires an explicit, static Compose project name")
        if agent_id not in config.get("services", {}) or agent_id.startswith("-"):
            raise ValueError("requested workload is not a declared Compose service")
        return project

    async def _container(self, agent_id: str, project: str) -> dict[str, Any]:
        ids = (await self._compose("ps", "--all", "--quiet", agent_id)).split()
        if len(ids) != 1 or not CONTAINER_ID.fullmatch(ids[0]):
            raise ValueError("expected exactly one owned container for the requested workload")
        return await self._inspect(ids[0], project, agent_id)

    async def _inspect(self, container_id: str, project: str, agent_id: str) -> dict[str, Any]:
        raw = json.loads(await self._docker("container", "inspect", container_id))
        if not isinstance(raw, list) or len(raw) != 1 or not isinstance(raw[0], dict):
            raise ValueError("Docker returned an invalid container inspection")
        return container_evidence(raw[0], container_id, project, agent_id)

    async def _probe_isolation(self, container: dict[str, Any]) -> dict[str, Any]:
        cached = self._isolation.get(container["container_id"])
        if cached and cached["started_at"] == container["started_at"]:
            return cached
        probe, error = None, None
        host_boot, host_ns, init_ns = None, None, None
        try:
            host_boot = pathlib.Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            host_ns = os.readlink("/proc/self/ns/pid")
            init_ns = os.readlink(f"/proc/{container['pid']}/ns/pid")
            if container["running"]:
                probe = await self._docker("container", "exec", container["container_id"], "/bin/sh", "-c",
                                           "readlink /proc/self/ns/pid; readlink /proc/1/ns/pid; "
                                           "cat /proc/sys/kernel/random/boot_id")
        except (OSError, RuntimeError) as exc:
            error = repr(exc)
        evidence = isolation_evidence(container, probe, host_boot_id=host_boot,
                                      host_pid_namespace=host_ns, init_pid_namespace=init_ns)
        if error:
            evidence["error"] = error
        self._isolation[container["container_id"]] = evidence
        return evidence

    async def isolation(self, agent_ids: list[str], *, timeout: float = WORKLOAD_TIMEOUT_SECONDS
                        ) -> dict[str, dict[str, Any]]:
        """Probe before tools execute; remote-daemon/Colab uncertainty is not a containment guarantee."""
        async def one(agent_id: str) -> tuple[str, dict[str, Any]]:
            try:
                async with asyncio.timeout(timeout):
                    container = await self._container(agent_id, self._project(agent_id))
                    return agent_id, {"agent_id": agent_id, **await self._probe_isolation(container)}
            except Exception as exc:
                return agent_id, {"agent_id": agent_id, "status": "unverified", "private_pid_namespace": None,
                                  "error": repr(exc)}
        return dict(await asyncio.gather(*(one(agent_id) for agent_id in dict.fromkeys(agent_ids))))

    async def terminate(self, agent_ids: list[str], *, timeout: float = WORKLOAD_TIMEOUT_SECONDS
                        ) -> dict[str, dict[str, Any]]:
        """Kill exact owned containers and inspect afterward; preserve files for failure analysis.

        A Docker acknowledgment only establishes an attempted signal. Confirmed workload termination
        additionally requires stopped container state, disabled restarts and private PID evidence.
        Model admission and harness session abort are separate controller operations.
        """
        async def one(agent_id: str) -> tuple[str, dict[str, Any]]:
            result: dict[str, Any] = {"agent_id": agent_id, "requested_at": time.time(), "status": "unknown",
                                      "confirmed": False, "container_stopped": None, "workload_terminated": None,
                                      "signal_acknowledged": False, "method": "docker_kill_and_inspect"}
            try:
                async with asyncio.timeout(timeout):
                    project = self._project(agent_id)
                    before = await self._container(agent_id, project)
                    result["before"] = before
                    container_id = before["container_id"]
                    cached = self._isolation.get(container_id)
                    result["isolation"] = (cached if cached and cached["started_at"] == before["started_at"] else
                                           {"status": "unverified", "reason": "no matching pre-action isolation probe"})
                    if before["running"] or before["restarting"]:
                        try:
                            await self._docker("container", "kill", "--signal", "KILL", container_id)
                            result["signal_acknowledged"] = True
                        except RuntimeError as exc:
                            # Natural exit can race the kill; only independent inspection resolves it.
                            result["signal_error"] = repr(exc)
                    while True:
                        after = await self._inspect(container_id, project, agent_id)
                        result["after"] = after
                        stopped = not after["running"] and not after["restarting"] and after["pid"] == 0
                        result["container_stopped"] = stopped
                        same_execution = before["started_at"] == after["started_at"]
                        if stopped or not same_execution:
                            break
                        await asyncio.sleep(0.1)
                    confirmed = (stopped and same_execution and result["isolation"]["status"] == "supported"
                                 and after["restart_policy"] in ("", "no"))
                    result.update(confirmed=confirmed, workload_terminated=True if confirmed else None,
                                  status="terminated" if confirmed else "unknown")
            except Exception as exc:
                result["error"] = repr(exc)
            result["observed_at"] = time.time()
            return agent_id, result
        return dict(await asyncio.gather(*(one(agent_id) for agent_id in dict.fromkeys(agent_ids))))

    async def up(self) -> str:
        """Create or recreate what changed and wait until every service with a healthcheck is healthy."""
        return await self._compose("up", "-d", "--build", "--wait")

    async def down(self, *, timeout: float = TEARDOWN_TIMEOUT_SECONDS) -> str:
        """Remove the project's containers, networks and volumes: the next run starts from a fresh world."""
        if self.cleanup_error is not None:
            raise self.cleanup_error
        self.cleanup_evidence = None
        try:
            async with asyncio.timeout(timeout):
                output = await self._compose("down", "-v", "--timeout", "10")
                remaining = (await self._compose("ps", "--all", "--quiet")).split()
                self.cleanup_evidence = {"method": "compose_down_and_inventory", "remaining_containers": remaining,
                                         "containers_removed": not remaining, "observed_at": time.time()}
                if remaining:
                    raise RuntimeError("project containers remain after Compose down")
                return output
        except asyncio.CancelledError:
            self.cleanup_error = StackCleanupError(self.compose_file, "teardown cancelled; world state is unknown")
            raise
        except Exception as exc:
            message = (f"teardown timed out after {timeout:g}s; world state is unknown" if isinstance(exc, TimeoutError)
                       else f"teardown failed; world state is unknown: {exc}")
            self.cleanup_error = StackCleanupError(self.compose_file, message)
            raise self.cleanup_error from exc
