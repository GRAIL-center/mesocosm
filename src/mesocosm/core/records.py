"""Run bundle: the append-only record of one run (ARCHITECTURE.md, record store).

Layout under <runs_root>/<run_id>/:
    manifest.json     the manifest as run, plus its digest
    events.jsonl      normalized events, append-only
    receipts.jsonl    condition receipts, append-only
    native/           raw harness/gateway artifacts, written once, never rewritten
    summary.json      written at the end

New events have stable IDs and explicit evidence links. Absent links or identity fields
remain unknown; sharing a task, agent or time window does not establish causality.
"""

from __future__ import annotations

import json
import os
import pathlib
import secrets
import time
from typing import IO, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def new_run_id() -> str:
    """Time-ordered, URL-safe id: run_<unix-ms>_<6 hex>."""
    return f"run_{int(time.time() * 1000):013d}_{secrets.token_hex(3)}"


class EventIdentity(BaseModel):
    """Separate execution identity from the display name and model alias.

    Identifiers are supplied by their authoritative controller, never inferred from
    a transcript. A null field means its identity was not recorded.
    """
    model_config = ConfigDict(extra="forbid", frozen=True)
    agent_instance_id: str | None = Field(default=None, min_length=1)
    operator_id: str | None = Field(default=None, min_length=1)
    session_id: str | None = Field(default=None, min_length=1)
    policy_id: str | None = Field(default=None, min_length=1)


class EventLink(BaseModel):
    """A source-backed relation to another event, not an inferred causal edge."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    event_id: str = Field(min_length=1)
    relation: Literal["responds_to", "observed", "derived_from", "authorized_by", "measured", "rewarded"]
    evidence: str = Field(min_length=1)


LinkCoverage = Literal["unknown", "partial", "complete"]


class Event(BaseModel):
    ts: float = Field(default_factory=time.time)
    run_id: str
    kind: str
    agent_id: str | None = None
    step: int | None = None
    request_id: str | None = None
    # Legacy readers must not fabricate IDs each time an old bundle is opened.
    event_id: str | None = None
    identity: EventIdentity = Field(default_factory=EventIdentity)
    links: list[EventLink] = Field(default_factory=list)
    link_coverage: LinkCoverage = "unknown"
    data: dict[str, Any] = Field(default_factory=dict)


class RunBundle:
    def __init__(self, root: pathlib.Path, run_id: str):
        self.root = root
        self.run_id = run_id
        self._steps: dict[str, int] = {}
        self._identities: dict[str, EventIdentity] = {}

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

    def write_manifest(self, manifest: dict[str, Any], digest: str, path: str | pathlib.Path | None = None) -> None:
        target = self.root / "manifest.json"
        if target.exists():
            raise FileExistsError("manifest.json is written once")
        target.write_text(json.dumps({"digest": digest, "manifest": manifest, "path": str(path) if path else None},
                                     indent=1, ensure_ascii=False))

    def write_lineage(self, lineage: dict[str, Any]) -> None:
        """A forked run's parentage: the snapshot it was restored from and the patch applied (written once)."""
        target = self.root / "lineage.json"
        if target.exists():
            raise FileExistsError("lineage.json is written once")
        target.write_text(json.dumps(lineage, indent=1, ensure_ascii=False))

    def next_step(self, agent_id: str) -> int:
        self._steps[agent_id] = self._steps.get(agent_id, 0) + 1
        return self._steps[agent_id]

    def bind_identity(self, agent_id: str, identity: EventIdentity) -> None:
        """Set known identity for subsequent writes through this writer only.

        Historical imports must supply their original identity explicitly; current
        session or policy bindings must never be used to attribute earlier records.
        """
        self._identities[agent_id] = identity

    def record(self, kind: str, *, agent_id: str | None = None, step: int | None = None,
               request_id: str | None = None, ts: float | None = None, identity: EventIdentity | None = None,
               links: list[EventLink] | None = None, link_coverage: LinkCoverage = "unknown", **data: Any) -> Event:
        """Append one event. `ts` is for copies of records made elsewhere (gateway, services), so the
        event keeps the time the thing happened, not the time it was copied."""
        ev = Event(run_id=self.run_id, kind=kind, agent_id=agent_id, step=step,
                   request_id=request_id, event_id=f"evt_{secrets.token_hex(16)}",
                   identity=identity or self._identities.get(agent_id or "") or EventIdentity(),
                   links=links or [], link_coverage=link_coverage,
                   data=data, **({"ts": ts} if ts is not None else {}))
        self._append("events.jsonl", ev.model_dump(mode="json"))
        return ev

    def record_receipt(self, receipt: dict[str, Any]) -> None:
        self._append("receipts.jsonl", receipt)

    def record_flag(self, flag: dict[str, Any]) -> None:
        """A candidate incident from the judge's detectors (flags.jsonl)."""
        self._append("flags.jsonl", flag)

    def record_verdict(self, verdict: dict[str, Any]) -> None:
        """A human verdict on a flag (verdicts.jsonl); append-only, the latest verdict per flag counts."""
        self._append("verdicts.jsonl", verdict)

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

    def open_native(self, relpath: str) -> IO[str]:
        """A raw artifact written incrementally (e.g. native/gateway/records.jsonl), once."""
        target = self.root / "native" / relpath
        if target.exists():
            raise FileExistsError(f"native artifact already written: {relpath}")
        target.parent.mkdir(parents=True, exist_ok=True)
        return target.open("w", encoding="utf-8")

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

    def flags(self) -> list[dict[str, Any]]:
        return self._read_jsonl("flags.jsonl")

    def verdicts(self) -> list[dict[str, Any]]:
        return self._read_jsonl("verdicts.jsonl")

    def _read_jsonl(self, name: str) -> list[dict[str, Any]]:
        path = self.root / name
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    def manifest(self) -> dict[str, Any]:
        return json.loads((self.root / "manifest.json").read_text(encoding="utf-8"))
