"""Workspace manager: the enforcement point for the memory policy (ARCHITECTURE.md, principle 3).

An agent's workspace is a host directory bind-mounted into its container. `reset` empties it before
every task (the directory itself stays, it is the mount point); `carry` leaves it alone. The run
manager turns the result into a receipt. Protected files are copied in from the task's `protected/`
directory and hashed, so the judge can tell afterwards whether the agent changed them.
"""

from __future__ import annotations

import hashlib
import pathlib
import shutil
from typing import Any


def reset_workspace(path: pathlib.Path) -> dict[str, Any]:
    """Remove everything inside the workspace. Returns what was done and whether it is now empty."""
    path.mkdir(parents=True, exist_ok=True)
    removed = 0
    for entry in path.iterdir():
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()
        removed += 1
    return {"removed": removed, "empty": not any(path.iterdir())}


def workspace_listing(path: pathlib.Path) -> dict[str, Any]:
    path.mkdir(parents=True, exist_ok=True)
    entries = sorted(p.name for p in path.iterdir())
    return {"entries": len(entries), "names": entries[:50]}


def plant_protected(task_dir: pathlib.Path, workspace: pathlib.Path) -> dict[str, str]:
    """Copy <task>/protected/** into the workspace; return {relative path: sha256} of what was planted."""
    source = task_dir / "protected"
    planted: dict[str, str] = {}
    if not source.is_dir():
        return planted
    for file in sorted(p for p in source.rglob("*") if p.is_file()):
        rel = file.relative_to(source)
        target = workspace / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(file, target)
        planted[str(rel)] = _sha256(target)
    return planted


def protected_state(workspace: pathlib.Path, planted: dict[str, str]) -> list[dict[str, Any]]:
    """For every planted file: the hash it had, the hash it has now (None if gone), and whether it changed."""
    out = []
    for rel, expected in planted.items():
        target = workspace / rel
        actual = _sha256(target) if target.is_file() else None
        out.append({"path": rel, "expected": expected, "actual": actual, "modified": actual != expected})
    return out


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
