"""Egress verification: a run may start only if every agent container fails to reach the outside.

The check itself is `spike/07_egress_check.sh`. It is not part of the agent image: the verifier pipes
the script into each container over `docker compose exec` with the facts it needs (gateway URL,
peers, services, channels) as variables of that one process, so nothing about the platform is left
on the agent's filesystem or in its environment (ADR-0019). A WARN line (external DNS resolves) does
not block the run but is recorded; a FAIL line does.
"""

from __future__ import annotations

import asyncio
import pathlib
from collections.abc import Mapping
from dataclasses import dataclass, field

SCRIPT = pathlib.Path(__file__).resolve().parents[3] / "spike" / "07_egress_check.sh"


@dataclass
class EgressResult:
    ok: bool
    exit_code: int | None
    lines: list[tuple[str, str]] = field(default_factory=list)   # (status, message)

    @property
    def failures(self) -> list[str]:
        return [m for s, m in self.lines if s == "FAIL"]

    @property
    def warnings(self) -> list[str]:
        return [m for s, m in self.lines if s == "WARN"]

    def to_dict(self) -> dict:
        return {"ok": self.ok, "exit_code": self.exit_code, "lines": [list(x) for x in self.lines],
                "failures": self.failures, "warnings": self.warnings}


def parse_egress_output(text: str, exit_code: int | None = 0) -> EgressResult:
    lines: list[tuple[str, str]] = []
    for raw in text.splitlines():
        parts = raw.split(None, 1)
        if len(parts) == 2 and parts[0] in ("OK", "FAIL", "WARN", "PASS"):
            lines.append((parts[0], parts[1].strip()))
    ok = exit_code == 0 and not any(s == "FAIL" for s, _ in lines) and any(s == "OK" for s, _ in lines)
    return EgressResult(ok=ok, exit_code=exit_code, lines=lines)


class DockerEgressVerifier:
    """Runs the check inside each agent's container via `docker compose exec -T <agent> sh -s < script`,
    with `env_for[agent_id]` passed as `-e` variables of that process only."""

    def __init__(self, compose_file: str | pathlib.Path, env_for: Mapping[str, Mapping[str, str]] | None = None,
                 script: pathlib.Path = SCRIPT):
        self.compose_file = str(compose_file)
        self.env_for = env_for or {}
        self.script = script

    async def verify(self, agent_ids: list[str]) -> dict[str, EgressResult]:
        """All agents are checked at once; the check is a few seconds per container."""
        results = await asyncio.gather(*(self._check(agent_id) for agent_id in agent_ids))
        return dict(zip(agent_ids, results, strict=True))

    async def _check(self, agent_id: str) -> EgressResult:
        env_args = [arg for k, v in sorted(self.env_for.get(agent_id, {}).items()) for arg in ("-e", f"{k}={v}")]
        proc = await asyncio.create_subprocess_exec(
            "docker", "compose", "-f", self.compose_file, "exec", "-T", *env_args, agent_id, "sh", "-s",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await proc.communicate(self.script.read_bytes())
        return parse_egress_output(out.decode("utf-8", "replace"), proc.returncode)
