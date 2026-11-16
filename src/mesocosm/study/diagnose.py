"""Diagnosis: read what happened inside trials, in bounded pieces, and break a result down by turn.

Environments report events (a verdict, a message, a choice) with their own step index. These functions
let an agent look for the mechanism behind a number without loading whole trials into its context.
"""

from __future__ import annotations

from collections.abc import Sequence
from statistics import fmean
from typing import Any, Literal

from mesocosm.study.condition import StudyError
from mesocosm.study.trial import TrialRecord, TrialStore

MAX_PAGE = 200


def read_events(store: TrialStore, trial_id: str, *, kind: str | None = None, agent_id: str | None = None,
                offset: int = 0, limit: int = 50) -> dict[str, Any]:
    """One page of a trial's events, filtered by kind and agent. ``next_offset`` is ``None`` at the end."""
    if offset < 0 or not 1 <= limit <= MAX_PAGE:
        raise StudyError(f"offset must be at least 0 and limit in 1..{MAX_PAGE}")
    record = store.load(trial_id)
    if record is None:
        raise StudyError(f"rejected_unsupported: no trial {trial_id!r} in this store")
    events = [event for event in (record.outcome.events if record.outcome else ())
              if (kind is None or event.kind == kind) and (agent_id is None or event.agent_id == agent_id)]
    page = events[offset:offset + limit]
    return {"trial_id": trial_id, "status": record.status, "condition": record.spec.condition.settings,
            "total": len(events), "offset": offset,
            "next_offset": offset + len(page) if offset + len(page) < len(events) else None,
            "events": [event.model_dump(mode="json") for event in page]}


def breakdown(records: Sequence[TrialRecord], *, kind: str, field: str,
              by: Literal["step", "agent_id"] = "step") -> dict[str, Any]:
    """The mean of one numeric or boolean event field, per condition and per step or agent.

    Events without the field, or with a value that is not a number or a boolean, are counted as
    ``skipped`` and left out of the means."""
    groups: dict[str, dict[str, list[float]]] = {}
    conditions: dict[str, dict[str, Any]] = {}
    skipped = 0
    for record in records:
        if record.outcome is None:
            continue
        digest = record.spec.condition.digest[:12]
        conditions[digest] = record.spec.condition.settings
        for event in record.outcome.events:
            if event.kind != kind:
                continue
            value = event.data.get(field)
            if isinstance(value, bool) or isinstance(value, int | float):
                key = str(event.step if by == "step" else event.agent_id)
                groups.setdefault(digest, {}).setdefault(key, []).append(float(value))
            else:
                skipped += 1
    table = {digest: {key: {"n": len(values), "mean": fmean(values)}
                      for key, values in sorted(cells.items(), key=lambda item: (len(item[0]), item[0]))}
             for digest, cells in groups.items()}
    return {"kind": kind, "field": field, "by": by, "conditions": conditions, "table": table, "skipped": skipped}
