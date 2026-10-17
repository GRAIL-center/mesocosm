"""Egress verification: a run may start only if every agent container fails to reach the outside.

The check itself is `spike/07_egress_check.sh` (installed as `egress-check` in the agent image);
this module runs it and parses its output. A WARN line (external DNS resolves) does not block the
run but is recorded; a FAIL line does.
"""

from __future__ import annotations

import asyncio
import pathlib
from dataclasses import dataclass, field


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
    """Runs `egress-check` inside each agent's container via `docker compose exec`."""

    def __init__(self, compose_file: str | pathlib.Path):
        self.compose_file = str(compose_file)

    async def verify(self, agent_ids: list[str]) -> dict[str, EgressResult]:
        results: dict[str, EgressResult] = {}
        for agent_id in agent_ids:
            proc = await asyncio.create_subprocess_exec(
                "docker", "compose", "-f", self.compose_file, "exec", "-T", agent_id, "egress-check",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            out, _ = await proc.communicate()
            results[agent_id] = parse_egress_output(out.decode("utf-8", "replace"), proc.returncode)
        return results
