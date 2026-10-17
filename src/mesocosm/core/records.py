"""Run bundle: the append-only record of one run (ARCHITECTURE.md, record store).

Layout under <runs_root>/<run_id>/:
    manifest.json     the manifest as run, plus its digest
    events.jsonl      normalized events, append-only
    receipts.jsonl    condition receipts, append-only
    native/           raw harness/gateway artifacts, written once, never rewritten
    summary.json      written at the end

Every event carries run_id, agent_id, step and (for model calls) request_id so that gateway
records, harness events and verifier results join without heuristics.
"""

from __future__ import annotations

import json
import os
import pathlib
import secrets
import time
from typing import Any

from pydantic import BaseModel, Field


def new_run_id() -> str:
    """Time-ordered, URL-safe id: run_<unix-ms>_<6 hex>."""
    return f"run_{int(time.time() * 1000):013d}_{secrets.token_hex(3)}"


class Event(BaseModel):
    ts: float = Field(default_factory=time.time)
    run_id: str
    kind: str
    agent_id: str | None = None
    step: int | None = None
    request_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class RunBundle:
    def __init__(self, root: pathlib.Path, run_id: str):
        self.root = root
        self.run_id = run_id
        self._steps: dict[str, int] = {}

    @classmethod
    def create(cls, runs_root: str | pathlib.Path, run_id: str | None = None) -> RunBundle:
        run_id = run_id or new_run_id()
        root = pathlib.Path(runs_root) / run_id
        if root.exists():
            raise FileExistsError(f"run bundle already exists: {root}")
        (root / "native").mkdir(parents=True)
        return cls(root, run_id)

    @classmethod
    def open(cls, path: str | pathlib.Path) -> RunBundle:
        root = pathlib.Path(path)
        if not (root / "events.jsonl").exists() and not (root / "manifest.json").exists():
            raise FileNotFoundError(f"not a run bundle: {root}")
        return cls(root, root.name)

    # -- writes (append-only) -------------------------------------------------------------

    def write_manifest(self, manifest: dict[str, Any], digest: str) -> None:
        target = self.root / "manifest.json"
        if target.exists():
            raise FileExistsError("manifest.json is written once")
        target.write_text(json.dumps({"digest": digest, "manifest": manifest}, indent=1, ensure_ascii=False))

    def next_step(self, agent_id: str) -> int:
        self._steps[agent_id] = self._steps.get(agent_id, 0) + 1
        return self._steps[agent_id]

    def record(self, kind: str, *, agent_id: str | None = None, step: int | None = None,
               request_id: str | None = None, **data: Any) -> Event:
        ev = Event(run_id=self.run_id, kind=kind, agent_id=agent_id, step=step,
                   request_id=request_id, data=data)
        self._append("events.jsonl", ev.model_dump(mode="json"))
        return ev

    def record_receipt(self, receipt: dict[str, Any]) -> None:
        self._append("receipts.jsonl", receipt)

    def write_native(self, relpath: str, content: bytes | str) -> pathlib.Path:
        """Store a raw artifact exactly once (e.g. native/agent-1/messages.json)."""
        target = self.root / "native" / relpath
        if target.exists():
            raise FileExistsError(f"native artifact already written: {relpath}")
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            content = content.encode("utf-8")
        target.write_bytes(content)
        return target

    def write_summary(self, summary: dict[str, Any]) -> None:
        target = self.root / "summary.json"
        if target.exists():
            raise FileExistsError("summary.json is written once")
        target.write_text(json.dumps(summary, indent=1, ensure_ascii=False))

    def _append(self, name: str, record: dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False) + "\n"
        fd = os.open(self.root / name, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)

    # -- reads ----------------------------------------------------------------------------

    def events(self) -> list[Event]:
        path = self.root / "events.jsonl"
        if not path.exists():
            return []
        return [Event.model_validate_json(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    def receipts(self) -> list[dict[str, Any]]:
        path = self.root / "receipts.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    def manifest(self) -> dict[str, Any]:
        return json.loads((self.root / "manifest.json").read_text(encoding="utf-8"))
