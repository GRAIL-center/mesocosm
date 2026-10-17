"""Harbor task adapter: load a task directory, record provenance, run and read its verifier.

Task layout (Harbor docs, tasks/overview): instruction.md, task.toml, environment/, tests/test.sh
(writes /logs/verifier/reward.txt or reward.json), optional solution/.

Running the verifier needs Docker; building the command and reading the reward do not.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import tomllib
from dataclasses import dataclass, field
from typing import Any


@dataclass
class HarborTask:
    path: pathlib.Path
    name: str
    instruction: str
    config: dict[str, Any]
    has_environment: bool
    has_tests: bool
    has_solution: bool

    def provenance(self) -> dict[str, Any]:
        """What the task is and exactly which text the agent and verifier saw."""
        toml = self.path / "task.toml"
        test_sh = self.path / "tests" / "test.sh"
        return {
            "source": "harbor",
            "name": self.name,
            "path": str(self.path),
            "instruction_sha256": _sha256(self.instruction.encode("utf-8")),
            "task_toml_sha256": _sha256(toml.read_bytes()) if toml.exists() else None,
            "test_sh_sha256": _sha256(test_sh.read_bytes()) if self.has_tests else None,
        }


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_task(path: str | pathlib.Path) -> HarborTask:
    path = pathlib.Path(path).resolve()
    instruction_file = path / "instruction.md"
    if not instruction_file.is_file():
        raise FileNotFoundError(f"{path}: no instruction.md (not a Harbor task directory)")
    config: dict[str, Any] = {}
    if (path / "task.toml").is_file():
        config = tomllib.loads((path / "task.toml").read_text(encoding="utf-8"))
    name = (config.get("metadata") or {}).get("name") or path.name
    return HarborTask(
        path=path, name=name, instruction=instruction_file.read_text(encoding="utf-8"), config=config,
        has_environment=(path / "environment").is_dir(),
        has_tests=(path / "tests" / "test.sh").is_file(),
        has_solution=(path / "solution").is_dir(),
    )


def adaptation(*, mode: str, memory: str, budget: dict[str, Any] | None,
               shared_workspace: bool = False) -> dict[str, Any]:
    """A task run under shared workspaces, carried memory, altered budgets or persistent operation
    is an *adapted* task: its score must not be presented as an official benchmark result."""
    reasons = []
    if mode == "persistent":
        reasons.append("persistent_mode")
    if memory == "carry":
        reasons.append("memory_carried")
    if budget and any(v is not None for v in budget.values()):
        reasons.append("budget_limited")
    if shared_workspace:
        reasons.append("shared_workspace")
    return {"adapted": bool(reasons), "reasons": reasons}


@dataclass
class Reward:
    value: float | None
    source: str | None            # reward.txt | reward.json | None
    raw: Any = None
    labels: dict[str, Any] = field(default_factory=dict)


def read_reward(logs_dir: str | pathlib.Path) -> Reward:
    """Read Harbor's verifier output from <logs_dir>/verifier/reward.{txt,json}."""
    vdir = pathlib.Path(logs_dir) / "verifier"
    txt, js = vdir / "reward.txt", vdir / "reward.json"
    if txt.is_file():
        raw = txt.read_text(encoding="utf-8").strip()
        try:
            return Reward(value=float(raw), source="reward.txt", raw=raw)
        except ValueError:
            return Reward(value=None, source="reward.txt", raw=raw)
    if js.is_file():
        data = json.loads(js.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            scalar = data.get("reward", data.get("score"))
            labels = {k: v for k, v in data.items() if k not in ("reward", "score")}
            value = float(scalar) if isinstance(scalar, int | float) else None
            return Reward(value=value, source="reward.json", raw=data, labels=labels)
        if isinstance(data, int | float):
            return Reward(value=float(data), source="reward.json", raw=data)
        return Reward(value=None, source="reward.json", raw=data)
    return Reward(value=None, source=None)


def verifier_command(task: HarborTask, *, workspace: pathlib.Path, logs_dir: pathlib.Path,
                     image: str = "debian:bookworm-slim", timeout_sec: int | None = None) -> list[str]:
    """`docker run` that executes tests/test.sh against the agent's workspace with no network.

    The workspace is mounted at /app (where the task environment works) and the task's tests/
    read-only at /tests; the verifier writes /logs/verifier/reward.*."""
    if not task.has_tests:
        raise FileNotFoundError(f"{task.path}: tests/test.sh missing")
    timeout = timeout_sec or int(((task.config.get("verifier") or {}).get("timeout_sec")) or 600)
    return [
        "docker", "run", "--rm", "--network", "none",
        "-v", f"{workspace.resolve()}:/app",
        "-v", f"{(task.path / 'tests').resolve()}:/tests:ro",
        "-v", f"{logs_dir.resolve()}:/logs",
        image, "timeout", str(timeout), "sh", "/tests/test.sh",
    ]
