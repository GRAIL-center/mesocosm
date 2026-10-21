"""Bring the Compose sandbox up and down from the control plane (scenario stages change the services'
configuration, so each stage reconciles the stack before its run)."""

from __future__ import annotations

import asyncio
import os
import pathlib
import signal

TEARDOWN_TIMEOUT_SECONDS = 120.0
REAP_TIMEOUT_SECONDS = 5.0


class StackCleanupError(RuntimeError):
    """The previous world may still exist; another world must not be started."""

    def __init__(self, compose_file: str, message: str):
        self.compose_file = compose_file
        super().__init__(message)


class DockerStack:
    def __init__(self, compose_file: str | pathlib.Path):
        self.compose_file = str(compose_file)
        self.cleanup_error: StackCleanupError | None = None

    async def _compose(self, *args: str, timeout: float | None = None) -> str:
        proc = await asyncio.create_subprocess_exec("docker", "compose", "-f", self.compose_file, *args,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                                                    start_new_session=True)
        communication = asyncio.create_task(proc.communicate())
        try:
            out, _ = await asyncio.wait_for(asyncio.shield(communication), timeout)
        except (TimeoutError, asyncio.CancelledError):
            # Only the client process group created above, including its compose plugin. Container
            # and host PIDs are never inspected or killed. Docker's world state remains uncertain.
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
            raise RuntimeError(f"docker compose {' '.join(args)} failed ({proc.returncode}): {text[-2000:]}")
        return text

    async def up(self) -> str:
        """Create or recreate what changed and wait until every service with a healthcheck is healthy."""
        return await self._compose("up", "-d", "--build", "--wait")

    async def down(self, *, timeout: float = TEARDOWN_TIMEOUT_SECONDS) -> str:
        """Remove the project's containers, networks and volumes: the next run starts from a fresh world."""
        if self.cleanup_error is not None:
            raise self.cleanup_error
        try:
            return await self._compose("down", "-v", "--timeout", "10", timeout=timeout)
        except asyncio.CancelledError:
            self.cleanup_error = StackCleanupError(self.compose_file, "teardown cancelled; world state is unknown")
            raise
        except Exception as exc:
            message = (f"teardown timed out after {timeout:g}s; world state is unknown" if isinstance(exc, TimeoutError)
                       else f"teardown failed; world state is unknown: {exc}")
            self.cleanup_error = StackCleanupError(self.compose_file, message)
            raise self.cleanup_error from exc
