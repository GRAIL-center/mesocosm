"""Harbor task adapter: load a task directory, record provenance, run and read its verifier.

Task layout (Harbor docs, tasks/overview): instruction.md, task.toml, environment/, tests/test.sh
(writes /logs/verifier/reward.txt or reward.json), optional solution/. A verifier may also write
/logs/verifier/checkpoints.json, {name: true | false | number}: partial credit (ADR-0028), the reward itself
(their mean) when no reward file exists. The run manager puts the world's state at /logs/world.json first.

A task may carry a side task (ADR-0030, after SHADE-Arena): side_task.md is appended to the instruction of
the agents the manifest names (all by default) and tests/side_test.sh, run like test.sh into its own logs
directory, says whether it was accomplished.

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
    side_instruction: str | None = None
    has_side_tests: bool = False
    rounds: list[dict[str, Any]] = field(default_factory=list)   # [[rounds]] in task.toml (ADR-0035)

    def provenance(self) -> dict[str, Any]:
        """What the task is and exactly which text the agent and verifier saw."""
        toml = self.path / "task.toml"
        test_sh, side_sh = self.path / "tests" / "test.sh", self.path / "tests" / "side_test.sh"
        return {
            "source": "harbor",
            "name": self.name,
            "path": str(self.path),
            "instruction_sha256": _sha256(self.instruction.encode("utf-8")),
            "task_toml_sha256": _sha256(toml.read_bytes()) if toml.exists() else None,
            "test_sh_sha256": _sha256(test_sh.read_bytes()) if self.has_tests else None,
            "side_task_sha256": _sha256(self.side_instruction.encode("utf-8")) if self.side_instruction else None,
            "side_test_sh_sha256": _sha256(side_sh.read_bytes()) if self.has_side_tests else None,
        }

    def instruction_for(self, agent_id: str, side_task_agents: list[str] | None) -> str:
        """The instruction an agent gets: the task, plus the side task when the agent is one of its recipients
        (`side_task_agents`; None = every agent)."""
        if self.side_instruction and (side_task_agents is None or agent_id in side_task_agents):
            return self.instruction.rstrip("\n") + "\n\n" + self.side_instruction.strip() + "\n"
        return self.instruction

    def round_prompts(self, agent_id: str, side_task_agents: list[str] | None) -> list[dict[str, Any]]:
        """The sequence of prompts to send for this task (ADR-0035). With no `[[rounds]]` it is one prompt (the
        instruction, plus the side task for its recipients). With rounds it is one prompt per round, each with
        its announced time budget prepended; the side task rides on the first round."""
        give_side = bool(self.side_instruction) and (side_task_agents is None or agent_id in side_task_agents)
        side = ("\n\n" + self.side_instruction.strip() + "\n") if give_side else ""
        if not self.rounds:
            base = self.instruction.rstrip("\n") + side if side else self.instruction
            return [{"prompt": base, "seconds": None, "index": 1}]
        out = []
        for i, r in enumerate(self.rounds):
            prompt = str(r.get("prompt") or "")
            if r.get("seconds") is not None:
                prompt = f"You have {int(r['seconds'])} seconds to answer this question.\n\n" + prompt
            if i == 0 and side:
                prompt = prompt.rstrip("\n") + side
            out.append({"prompt": prompt, "seconds": r.get("seconds"), "index": i + 1})
        return out


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
    side = path / "side_task.md"
    return HarborTask(
        path=path, name=name, instruction=instruction_file.read_text(encoding="utf-8"), config=config,
        has_environment=(path / "environment").is_dir(),
        has_tests=(path / "tests" / "test.sh").is_file(),
        has_solution=(path / "solution").is_dir(),
        side_instruction=side.read_text(encoding="utf-8") if side.is_file() else None,
        has_side_tests=(path / "tests" / "side_test.sh").is_file(),
        rounds=[r for r in (config.get("rounds") or []) if isinstance(r, dict) and r.get("prompt")],
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
    source: str | None            # reward.txt | reward.json | checkpoints.json | None
    raw: Any = None
    labels: dict[str, Any] = field(default_factory=dict)
    checkpoints: dict[str, float] = field(default_factory=dict)     # name -> 0..1, from checkpoints.json


def read_checkpoints(vdir: pathlib.Path) -> dict[str, float]:
    """checkpoints.json as {name: score}: booleans become 0/1, anything that is not a number is dropped."""
    path = vdir / "checkpoints.json"
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}
    return {str(k): float(v) for k, v in data.items() if isinstance(v, bool | int | float)}


def read_reward(logs_dir: str | pathlib.Path) -> Reward:
    """Harbor's verifier output from <logs_dir>/verifier/: reward.txt or reward.json, with checkpoints.json
    attached; with no reward file the checkpoints' mean is the reward (partial credit, ADR-0028)."""
    vdir = pathlib.Path(logs_dir) / "verifier"
    checkpoints = read_checkpoints(vdir)
    reward = _reward_file(vdir)
    if reward is None:
        if checkpoints:
            return Reward(value=round(sum(checkpoints.values()) / len(checkpoints), 4), source="checkpoints.json",
                          raw=checkpoints, checkpoints=checkpoints)
        return Reward(value=None, source=None)
    reward.checkpoints = checkpoints
    return reward


def _reward_file(vdir: pathlib.Path) -> Reward | None:
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
    return None


def verifier_command(task: HarborTask, *, workspace: pathlib.Path, logs_dir: pathlib.Path,
                     image: str = "debian:bookworm-slim", timeout_sec: int | None = None,
                     script: str = "test.sh") -> list[str]:
    """`docker run` that executes tests/<script> against the agent's workspace with no network.

    The workspace is mounted at /app (where the task environment works) and the task's tests/
    read-only at /tests; the verifier writes /logs/verifier/reward.*."""
    if not (task.path / "tests" / script).is_file():
        raise FileNotFoundError(f"{task.path}: tests/{script} missing")
    timeout = timeout_sec or int(((task.config.get("verifier") or {}).get("timeout_sec")) or 600)
    return [
        "docker", "run", "--rm", "--network", "none",
        "-v", f"{workspace.resolve()}:/app",
        "-v", f"{(task.path / 'tests').resolve()}:/tests:ro",
        "-v", f"{logs_dir.resolve()}:/logs",
        image, "timeout", str(timeout), "sh", f"/tests/{script}",
    ]
