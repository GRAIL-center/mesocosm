"""Bring the Compose sandbox up and down from the control plane (scenario stages change the services'
configuration, so each stage reconciles the stack before its run)."""

from __future__ import annotations

import asyncio
import pathlib


class DockerStack:
    def __init__(self, compose_file: str | pathlib.Path):
        self.compose_file = str(compose_file)

    async def _compose(self, *args: str) -> str:
        proc = await asyncio.create_subprocess_exec("docker", "compose", "-f", self.compose_file, *args,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await proc.communicate()
        text = out.decode("utf-8", "replace")
        if proc.returncode != 0:
            raise RuntimeError(f"docker compose {' '.join(args)} failed ({proc.returncode}): {text[-2000:]}")
        return text

    async def up(self) -> str:
        """Create or recreate what changed and wait until every service with a healthcheck is healthy."""
        return await self._compose("up", "-d", "--build", "--wait")

    async def down(self) -> str:
        """Remove the project's containers, networks and volumes: the next run starts from a fresh world."""
        return await self._compose("down", "-v")
