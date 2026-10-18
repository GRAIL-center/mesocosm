"""State check for the `shared_dir` channel: what is in /shared at the end of a run."""

from __future__ import annotations

import asyncio
import pathlib


class DockerSharedDir:
    """Lists the shared volume through the control container, which mounts it read-only."""

    def __init__(self, compose_file: str | pathlib.Path):
        self.compose_file = str(compose_file)

    async def list_files(self) -> list[str] | None:
        proc = await asyncio.create_subprocess_exec(
            "docker", "compose", "-f", self.compose_file, "exec", "-T", "control", "find", "/shared", "-type", "f",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await proc.communicate()
        if proc.returncode != 0:
            return None
        return sorted(line[len("/shared/"):] for line in out.decode("utf-8", "replace").splitlines()
                      if line.startswith("/shared/"))
