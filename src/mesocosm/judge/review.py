"""The human review queue: a verdict per flag, appended to the bundle (verdicts.jsonl). The latest
verdict for a flag counts; nothing is ever rewritten."""

from __future__ import annotations

import pathlib
import time
from typing import Any

from mesocosm.core.records import RunBundle

VERDICTS = ("confirmed", "rejected", "unclear")


def flags_with_verdicts(bundle_root: str | pathlib.Path) -> list[dict[str, Any]]:
    bundle = RunBundle.open(bundle_root)
    latest: dict[str, dict[str, Any]] = {}
    for v in bundle.verdicts():
        latest[v["flag_id"]] = v
    return [{**f, "verdict": latest.get(f["flag_id"])} for f in bundle.flags()]


def review(bundle_root: str | pathlib.Path, flag_id: str, verdict: str, reviewer: str,
           note: str | None = None) -> dict[str, Any]:
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {VERDICTS}")
    bundle = RunBundle.open(bundle_root)
    flag = next((f for f in bundle.flags() if f["flag_id"] == flag_id), None)
    if flag is None:
        raise KeyError(f"no flag {flag_id} in {bundle.root}")
    record = {"flag_id": flag_id, "verdict": verdict, "reviewer": reviewer, "note": note, "ts": time.time(),
              "kind": flag["kind"], "detector_version": flag.get("detector_version"), "catalog_version": None}
    bundle.record_verdict(record)
    return record
