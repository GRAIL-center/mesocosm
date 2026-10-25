"""Task-source extension contract, independent of a benchmark's storage format.

Providers are installed control-plane code. They retain original input files and return a
portable task root; execution_kind declares which execution provider can interpret config.
"""

from __future__ import annotations

import hashlib
import pathlib
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class Task:
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

    source: str = "harbor"
    execution_kind: str = "harbor"

    def provenance(self) -> dict[str, Any]:
        """Source-neutral identity; providers can extend this with original-format evidence."""
        return {"source": self.source, "execution_kind": self.execution_kind,
                "name": self.name, "dataset": self.dataset, "path": str(self.path),
                "instruction_sha256": hashlib.sha256(self.instruction.encode("utf-8")).hexdigest()}

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
        side = ""
        if self.side_instruction and (side_task_agents is None or agent_id in side_task_agents):
            side = "\n\n" + self.side_instruction.strip() + "\n"
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


@dataclass
class Reward:
    value: float | None
    source: str | None            # reward.txt | reward.json | checkpoints.json | None
    raw: Any = None
    labels: dict[str, Any] = field(default_factory=dict)
    checkpoints: dict[str, float] = field(default_factory=dict)     # name -> 0..1, from checkpoints.json

class TaskSource(Protocol):
    api_version: int

    def load(self, path: pathlib.Path, *, dataset: str | None = None, name: str | None = None) -> Task: ...
    def resolve(self, dataset: str, *, roots: list[pathlib.Path],
                limit: int | None = None) -> list[pathlib.Path]: ...
    def research_errors(self, task: Task) -> list[str]: ...
    def fingerprint(self, task: Task) -> dict[str, Any]: ...


def load_task(source: str, path: str | pathlib.Path, *, dataset: str | None = None,
              name: str | None = None) -> Task:
    """Load through an installed source and bind provenance to the selected provider."""
    from mesocosm.integrations.registry import get_task_source

    resolved = pathlib.Path(path).resolve()
    task = get_task_source(source).load(resolved, dataset=dataset, name=name)
    if not isinstance(task, Task):
        raise ValueError(f"task source {source!r} must return a Task")
    if not isinstance(task.path, pathlib.Path) or task.path.resolve() != resolved:
        raise ValueError(f"task source {source!r} returned a different input root")
    if (not isinstance(task.name, str) or not task.name.strip() or task.name in (".", "..")
            or any(character in task.name for character in ("/", "\\", "\x00"))):
        raise ValueError(f"task source {source!r} returned an unsafe task name; use a single path component")
    if not isinstance(task.execution_kind, str) or not task.execution_kind.strip():
        raise ValueError(f"task source {source!r} returned an empty execution kind")
    task.path = resolved
    task.source = source
    return task


def resolve_task_paths(source: str, dataset: str, *, roots: list[pathlib.Path],
                       limit: int | None = None) -> list[pathlib.Path]:
    """Check a provider's materialized collection before it becomes executable input."""
    from mesocosm.integrations.registry import get_task_source

    paths = get_task_source(source).resolve(dataset, roots=roots, limit=limit)
    if not isinstance(paths, list) or any(not isinstance(path, pathlib.Path) for path in paths):
        raise ValueError(f"task source {source!r} resolve() must return a list of Paths")
    if not paths:
        raise FileNotFoundError(f"task source {source!r} found no tasks for dataset {dataset!r}")
    if limit is not None and len(paths) > limit:
        raise ValueError(f"task source {source!r} returned more tasks than the requested limit")
    resolved = [path.resolve() for path in paths]
    if len(set(resolved)) != len(resolved):
        raise ValueError(f"task source {source!r} returned duplicate task paths")
    return resolved
