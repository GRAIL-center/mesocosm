"""Workspace manager: the enforcement point for the memory policy (ARCHITECTURE.md, principle 3).

An agent's workspace is a host directory bind-mounted into its container. `reset` empties it before
every task (the directory itself stays, it is the mount point); `carry` leaves it alone. The run
manager turns the result into a receipt. Protected files are copied in from the task's `protected/`
directory and hashed, so the judge can tell afterwards whether the agent changed them.
"""

from __future__ import annotations

import hashlib
import json
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


def plant_agent_files(task_dir: pathlib.Path, agent_id: str, workspace: pathlib.Path,
                      index: int = 0) -> tuple[list[str], str | None]:
    """Copy the agent's own instance of a task whose instances differ per agent (peer review) into the
    workspace: <task>/agents/<agent_id>/** when the task names the agent, else the <task>/instances/<name>/**
    the agent's position in the manifest selects (round robin), so a pool of instances serves any number of
    agents. Returns (files, instance name). Not hashed; the agent may change them."""
    source, name = task_dir / "agents" / agent_id, agent_id
    if not source.is_dir():
        pool = sorted(p for p in (task_dir / "instances").glob("*") if p.is_dir()) \
            if (task_dir / "instances").is_dir() else []
        if not pool:
            return [], None
        source = pool[index % len(pool)]
        name = source.name
    planted: list[str] = []
    for file in sorted(p for p in source.rglob("*") if p.is_file()):
        rel = file.relative_to(source)
        target = workspace / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(file, target)
        planted.append(str(rel))
    return planted, name


class RawLog:
    """The agent's own execution trace, written into its workspace as `raw_log.jsonl` from the harness
    events the run manager receives: one line per completed tool call (seq, task, tool, arguments,
    result). The agent can read it; nobody else gets it except through what the agent sends."""

    def __init__(self, workspace: pathlib.Path):
        self.path = workspace / "raw_log.jsonl"
        self.task: str | None = None
        self.seq = 0
        self.seen: set[str] = set()
        self.chars: dict[str, int] = {}
        self.entries: dict[str, int] = {}

    def append(self, part: dict) -> None:
        state = part.get("state") or {}
        call_id = part.get("callID") or part.get("id")
        if part.get("type") != "tool" or state.get("status") != "completed" or call_id in self.seen:
            return
        self.seen.add(call_id)
        self.seq += 1
        entry = {"seq": self.seq, "task": self.task, "tool": part.get("tool"), "arguments": state.get("input"),
                 "result": state.get("output")}
        line = json.dumps(entry, ensure_ascii=False)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        key = self.task or ""
        self.chars[key] = self.chars.get(key, 0) + len(line)
        self.entries[key] = self.entries.get(key, 0) + 1

    def stats(self, task: str) -> dict:
        return {"entries": self.entries.get(task, 0), "chars": self.chars.get(task, 0)}
