"""Snapshot and restore of the sandbox's world state through Compose: the services container's data
(board, pastes, bare repositories) and the shared volume."""

from __future__ import annotations

import asyncio
import pathlib
import shutil

from mesocosm.core.snapshot import SERVICES_ITEMS


class DockerWorldState:
    def __init__(self, compose_file: str | pathlib.Path, agent_with_shared: str = "agent-1"):
        self.compose_file = str(compose_file)
        self.agent_with_shared = agent_with_shared     # /shared is read-only in `control`; agents mount it rw

    async def _compose(self, *args: str, check: bool = True) -> tuple[int, str]:
        proc = await asyncio.create_subprocess_exec("docker", "compose", "-f", self.compose_file, *args,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await proc.communicate()
        text = out.decode("utf-8", "replace")
        if check and proc.returncode != 0:
            raise RuntimeError(f"docker compose {' '.join(args)} failed ({proc.returncode}): {text[-1500:]}")
        return proc.returncode, text

    async def export_services(self, dest: pathlib.Path) -> None:
        dest.mkdir(parents=True, exist_ok=True)
        for item in SERVICES_ITEMS:
            await self._compose("cp", f"services:/data/{item}", str(dest / item), check=False)

    async def import_services(self, src: pathlib.Path) -> None:
        for item in SERVICES_ITEMS:
            if (src / item).exists():
                await self._compose("exec", "-T", "services", "rm", "-rf", f"/data/{item}", check=False)
                await self._compose("cp", str(src / item), f"services:/data/{item}")
        await self._compose("restart", "services")     # the app replays its journals at start

    async def export_shared(self, dest: pathlib.Path) -> None:
        code, _ = await self._compose("cp", "control:/shared/.", str(dest), check=False)
        if code != 0:
            shutil.rmtree(dest, ignore_errors=True)

    async def import_shared(self, src: pathlib.Path) -> None:
        await self._compose("exec", "-T", self.agent_with_shared, "sh", "-c",
                            "rm -rf /shared/* /shared/.[!.]* 2>/dev/null; true")
        await self._compose("cp", f"{src}/.", f"{self.agent_with_shared}:/shared/")
