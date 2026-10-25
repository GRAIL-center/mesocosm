"""Installed task-source interface for original Harbor task directories."""

from __future__ import annotations

import hashlib
import json
import pathlib
import stat
from typing import Any

from mesocosm.adapters.harbor.task import load_task, resolve_dataset_tasks
from mesocosm.integrations.tasks import Task


class HarborSource:
    api_version = 1

    def load(self, path: pathlib.Path, *, dataset: str | None = None, name: str | None = None) -> Task:
        return load_task(path, dataset=dataset, name=name)

    def resolve(self, dataset: str, *, roots: list[pathlib.Path],
                limit: int | None = None) -> list[pathlib.Path]:
        return resolve_dataset_tasks(dataset, roots, limit)

    def research_errors(self, task: Task) -> list[str]:
        """Harbor fixture extensions are not rules for unrelated benchmark formats."""
        extensions = ("side_task.md", "tests/side_test.sh", "agents", "instances", "protected")
        fixture_inputs = [relative for relative in extensions if (task.path / relative).exists()]
        if task.side_instruction is not None and "side_task.md" not in fixture_inputs:
            fixture_inputs.append("side_instruction")
        if task.has_side_tests and "tests/side_test.sh" not in fixture_inputs:
            fixture_inputs.append("side_tests")
        if task.rounds or task.config.get("rounds"):
            fixture_inputs.append("rounds")
        if (task.config.get("metadata") or {}).get("fixture"):
            fixture_inputs.append("metadata.fixture")
        return [f"rejected_unsupported: profile: research excludes task {task.name!r} fixture input {name}; "
                "use profile: fixture for regression runs" for name in fixture_inputs]

    def fingerprint(self, task: Task) -> dict[str, Any]:
        files: dict[str, dict[str, Any]] = {}
        for root in (task.path / "environment", task.path / "tests"):
            if not root.exists():
                continue
            for path in [root, *sorted(root.rglob("*"))]:
                info = path.lstat()
                regular, directory = stat.S_ISREG(info.st_mode), stat.S_ISDIR(info.st_mode)
                if not (regular or directory):
                    raise ValueError(f"rejected_unsupported: input contains symlink or special file: {path}")
                entry: dict[str, Any] = {"type": "file" if regular else "directory",
                                         "mode": stat.S_IMODE(info.st_mode)}
                if regular:
                    with path.open("rb") as stream:
                        entry["sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
                    after = path.lstat()
                    if (info.st_mode, info.st_size, info.st_mtime_ns) != (
                            after.st_mode, after.st_size, after.st_mtime_ns):
                        raise ValueError(f"input changed while hashing: {path}")
                files[path.relative_to(task.path).as_posix()] = entry
        execution = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return {"execution_sha256": execution if task.has_tests else None,
                "instruction_sha256": hashlib.sha256(task.instruction.encode("utf-8")).hexdigest(),
                "environment": task.config.get("environment") or {},
                "verifier": task.config.get("verifier") or {},
                "originality_verified": False, "image_resolution_verified": False}
