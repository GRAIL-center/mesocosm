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
import math
import pathlib
import shlex
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
    dataset: str | None = None   # the registry ref it was imported from, e.g. "terminal-bench@2.0/fix-git" (ADR-0037)

    def provenance(self) -> dict[str, Any]:
        """What the task is and exactly which text the agent and verifier saw."""
        toml = self.path / "task.toml"
        test_sh, side_sh = self.path / "tests" / "test.sh", self.path / "tests" / "side_test.sh"
        dockerfile = self.path / "environment" / "Dockerfile"
        return {
            "source": "harbor",
            "name": self.name,
            "dataset": self.dataset,
            "path": str(self.path),
            "instruction_sha256": _sha256(self.instruction.encode("utf-8")),
            "task_toml_sha256": _sha256(toml.read_bytes()) if toml.exists() else None,
            "test_sh_sha256": _sha256(test_sh.read_bytes()) if self.has_tests else None,
            "environment_image": (self.config.get("environment") or {}).get("docker_image"),
            "environment_dockerfile_sha256": _sha256(dockerfile.read_bytes()) if dockerfile.is_file() else None,
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


def load_task(path: str | pathlib.Path, *, dataset: str | None = None, name: str | None = None) -> HarborTask:
    path = pathlib.Path(path).resolve()
    instruction_file = path / "instruction.md"
    if not instruction_file.is_file():
        raise FileNotFoundError(f"{path}: no instruction.md (not a Harbor task directory)")
    config: dict[str, Any] = {}
    if (path / "task.toml").is_file():
        config = tomllib.loads((path / "task.toml").read_text(encoding="utf-8"))
    for r in config.get("rounds") or []:
        seconds = r.get("seconds") if isinstance(r, dict) else None
        if seconds is not None and (isinstance(seconds, bool) or not isinstance(seconds, int | float)
                                    or not math.isfinite(seconds) or seconds <= 0):
            raise ValueError(f"{path}: rounds[].seconds must be a positive finite number")
    task_name = name or (config.get("metadata") or {}).get("name") or path.name
    side = path / "side_task.md"
    return HarborTask(
        path=path, name=task_name, instruction=instruction_file.read_text(encoding="utf-8"), config=config,
        has_environment=(path / "environment").is_dir(),
        has_tests=(path / "tests" / "test.sh").is_file(),
        has_solution=(path / "solution").is_dir(),
        side_instruction=side.read_text(encoding="utf-8") if side.is_file() else None,
        has_side_tests=(path / "tests" / "side_test.sh").is_file(),
        rounds=[r for r in (config.get("rounds") or []) if isinstance(r, dict) and r.get("prompt")],
        dataset=dataset,
    )


def _dataset_rel(ref: str) -> pathlib.PurePosixPath:
    if not ref or not ref.strip():
        raise ValueError("dataset: empty reference")
    rel = pathlib.PurePosixPath(ref.strip())
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError(f"dataset {ref!r}: must be a relative registry reference like 'terminal-bench@2.0/fix-git'")
    return rel


def _dataset_not_found(ref: str, roots: list[pathlib.Path]) -> FileNotFoundError:
    searched = ", ".join(str(pathlib.Path(r)) for r in roots) or "(no task roots)"
    return FileNotFoundError(
        f"dataset {ref!r} not found under: {searched}. Materialize the benchmark task(s) first "
        f"(e.g. `harbor run -p {ref} ...`, or copy the task directory) and point MESOCOSM_TASKS_ROOT at the "
        f"directory that contains it.")


def resolve_dataset(ref: str, roots: list[pathlib.Path]) -> pathlib.Path:
    """Resolve a Harbor dataset reference (e.g. `terminal-bench@2.0/fix-git`) to one materialized task directory
    under one of `roots`, so a run can use a real benchmark task instead of a hand-written one (ADR-0037).

    Mesocosm imports the task *directory*; Harbor is what materializes it (`harbor run -p <ref> ...`, or its task
    cache). This runs on the control plane: no agent container gains any new route. The reference maps to a
    relative path (`terminal-bench@2.0/fix-git` -> `<root>/terminal-bench@2.0/fix-git/`) and the first root that
    holds an `instruction.md` there wins. Unresolvable references fail explicitly, naming the roots searched."""
    rel = _dataset_rel(ref)
    for root in roots:
        candidate = pathlib.Path(root).joinpath(*rel.parts)
        if (candidate / "instruction.md").is_file():
            return candidate.resolve()
    raise _dataset_not_found(ref, roots)


def resolve_dataset_tasks(ref: str, roots: list[pathlib.Path], limit: int | None = None) -> list[pathlib.Path]:
    """Resolve a dataset reference to one or many task directories, so tasks come from external benchmarks
    rather than being hand-written (ADR-0040). The reference may name a single task (its directory holds an
    `instruction.md`) or a whole collection/split (its directory holds task subdirectories, each with an
    `instruction.md`): a collection expands to all of them, sorted, capped at `limit`. Works for any benchmark
    materialized into the standard task layout (Terminal-Bench, SWE-bench, ...). Resolution is control-plane
    only; containment (ADR-0022) is untouched."""
    rel = _dataset_rel(ref)
    base: pathlib.Path | None = None
    for root in roots:
        candidate = pathlib.Path(root).joinpath(*rel.parts)
        if (candidate / "instruction.md").is_file():
            return [candidate.resolve()]                       # the reference names a single task
        if base is None and candidate.is_dir():
            base = candidate
    if base is None:
        raise _dataset_not_found(ref, roots)
    tasks = sorted(d.resolve() for d in base.iterdir() if d.is_dir() and (d / "instruction.md").is_file())
    if not tasks:
        raise FileNotFoundError(
            f"dataset {ref!r}: {base} holds no task directories (looked for <task>/instruction.md). Materialize "
            f"the benchmark's tasks there first.")
    return tasks[: limit] if limit else tasks


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
                     image: str | None = None, timeout_sec: int | None = None,
                     script: str = "test.sh", workdir: str = "/app", network: str = "none",
                     container_name: str | None = None) -> list[str]:
    """Run the native verifier in an explicitly selected environment, retaining its interpreter.

    The workspace is mounted at /app (where the task environment works) and the task's tests/
    read-only at /tests; the verifier writes /logs/verifier/reward.*."""
    test = task.path / "tests" / script
    if not test.is_file():
        raise FileNotFoundError(f"{task.path}: tests/{script} missing")
    if not image:
        raise ValueError(f"{task.name}: verifier needs its native task image or an explicit verifier image")
    if timeout_sec is not None and (not math.isfinite(timeout_sec) or timeout_sec <= 0):
        raise ValueError("verifier timeout must be positive and finite; the controller enforces it")
    first = test.read_text(encoding="utf-8").splitlines()[0] if test.stat().st_size else ""
    interpreter = shlex.split(first[2:]) if first.startswith("#!") else ["/bin/sh"]
    env = (task.config.get("verifier") or {}).get("env") or {}
    return [
        "docker", "run", "--rm", "--network", network,
        *(["--name", container_name] if container_name else []),
        "--workdir", workdir, "--entrypoint", interpreter[0],
        *[part for key, value in sorted(env.items()) for part in ("-e", f"{key}={value}")],
        "-v", f"{workspace.resolve()}:{workdir}",
        "-v", f"{(task.path / 'tests').resolve()}:/tests:ro",
        "-v", f"{logs_dir.resolve()}:/logs",
        image, *interpreter[1:], f"/tests/{script}",
    ]
