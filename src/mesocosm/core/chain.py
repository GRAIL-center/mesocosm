"""Run chaining and recovery (M3): several runs in sequence from a plan. Infrastructure failures are
retried, contradictions between manifest and world are not (they are deterministic), and a chain log
lets an interrupted chain resume where it stopped.

    name: nightly
    runs:
      - {manifest: examples/world-demo.yaml, repeat: 3}
      - {manifest: examples/world-channels.yaml}
    retries: 1
    on_failure: continue
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import secrets
import time
from collections.abc import Awaitable, Callable
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from mesocosm.core.run import RunResult

RETRYABLE = {"failed", "failed_isolation"}                                      # infrastructure: a retry can succeed
TERMINAL = {"ok", "failed_channel_verification", "failed_exposure_verification"}  # the manifest contradicts the world

RunOne = Callable[[pathlib.Path], Awaitable[RunResult]]
LogFn = Callable[[dict[str, Any]], None]


class ChainItem(BaseModel):
    manifest: str
    repeat: int = Field(default=1, ge=1)
    label: str | None = None


class ChainPlan(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    runs: list[ChainItem] = Field(min_length=1)
    retries: int = Field(default=1, ge=0)
    retry_delay_seconds: float = Field(default=5.0, ge=0)
    on_failure: Literal["continue", "stop"] = "continue"


def load_plan(path: str | pathlib.Path) -> ChainPlan:
    data = yaml.safe_load(pathlib.Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: a chain plan is a YAML mapping")
    return ChainPlan.model_validate(data)


def expand(plan: ChainPlan, plan_path: pathlib.Path) -> list[dict[str, Any]]:
    """One entry per run, numbered from 1, manifests resolved relative to the plan file."""
    entries = []
    for item in plan.runs:
        manifest = (plan_path.parent / item.manifest).resolve()
        for _ in range(item.repeat):
            entries.append({"index": len(entries) + 1, "manifest": str(manifest), "label": item.label})
    return entries


async def run_with_retries(run_one: RunOne, manifest: pathlib.Path, retries: int, delay: float, log: LogFn,
                           index: int) -> tuple[RunResult, int]:
    """Run once, then again after `delay` seconds for every retryable failure, at most `retries` times."""
    attempt = 0
    while True:
        attempt += 1
        result = await run_one(manifest)
        log({"index": index, "manifest": str(manifest), "attempt": attempt, "run_id": result.run_id,
             "status": result.status, "error": result.error, "ts": time.time()})
        if result.status not in RETRYABLE or attempt > retries:
            return result, attempt
        await asyncio.sleep(delay)


def _append(path: pathlib.Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def new_chain_id() -> str:
    return f"chain_{int(time.time() * 1000):013d}_{secrets.token_hex(3)}"


async def run_chain(plan: ChainPlan, plan_path: pathlib.Path, run_one: RunOne, chains_root: pathlib.Path,
                    chain_id: str | None = None, resume: bool = False) -> dict[str, Any]:
    chain_id = chain_id or new_chain_id()
    log_path = chains_root / f"{chain_id}.jsonl"
    done: dict[int, dict[str, Any]] = {}
    if resume and log_path.exists():
        for line in log_path.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            if rec.get("status") in TERMINAL:
                done[rec["index"]] = rec
    runs: list[dict[str, Any]] = []
    stopped = False
    for entry in expand(plan, plan_path):
        if entry["index"] in done:
            prev = done[entry["index"]]
            runs.append({**entry, "status": prev["status"], "run_id": prev["run_id"], "attempts": 0, "skipped": True})
            continue
        if stopped:
            runs.append({**entry, "status": "not_run", "attempts": 0})
            continue
        result, attempts = await run_with_retries(run_one, pathlib.Path(entry["manifest"]), plan.retries,
                                                  plan.retry_delay_seconds, lambda r: _append(log_path, r),
                                                  entry["index"])
        runs.append({**entry, "status": result.status, "run_id": result.run_id, "bundle": str(result.bundle_path),
                     "rewards": result.rewards, "attempts": attempts, "error": result.error})
        if result.status != "ok" and plan.on_failure == "stop":
            stopped = True
    summary = {"chain_id": chain_id, "name": plan.name, "plan": str(plan_path), "resumed": resume, "runs": runs,
               "ok": sum(1 for r in runs if r["status"] == "ok"),
               "failed": sum(1 for r in runs if r["status"] not in ("ok", "not_run")),
               "not_run": sum(1 for r in runs if r["status"] == "not_run"), "log": str(log_path)}
    chains_root.mkdir(parents=True, exist_ok=True)
    (chains_root / f"{chain_id}.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False))
    return summary
