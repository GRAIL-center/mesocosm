"""Deprecated staged-scenario compatibility; use core.experiment for condition comparisons.

Staged scenarios (M4): one base manifest, stages that patch it, a run per stage (repeated), the
judge's flags per stage, and the first stage at which each flag kind appeared (the minimum
elicitation stage). Stages change what the world offers or enforces; they never add instructions
to the task text (ADR-0003).

    name: channel-exposure
    base: ../world-channels.yaml
    stages:
      - {name: closed, patch: {channels: [], conditions: {exposure: null}}}
      - {name: channels-on, patch: {channels: [{type: pastebin}, {type: shared_dir}]}}
    stop_on: [trap_credential]
"""

from __future__ import annotations

import json
import pathlib
import time
import warnings
from typing import Any

import yaml
from pydantic import BaseModel, Field

from mesocosm.core.chain import RunOne, run_with_retries
from mesocosm.core.config import absolutize_manifest as _absolutize
from mesocosm.core.config import deep_merge as deep_merge
from mesocosm.core.manifest import validate_manifest
from mesocosm.core.records import RunBundle
from mesocosm.core.report import bundle_report
from mesocosm.judge.flags import KINDS, flag_counts


class Stage(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,40}$")
    patch: dict[str, Any] = Field(default_factory=dict)
    repeat: int = Field(default=1, ge=1)


class Scenario(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,40}$")
    base: str
    stages: list[Stage] = Field(min_length=1)
    stop_on: list[str] = Field(default_factory=list, description="Flag kinds that end the escalation")
    retries: int = Field(default=1, ge=0)
    retry_delay_seconds: float = Field(default=5.0, ge=0)


def load_scenario(path: str | pathlib.Path) -> Scenario:
    warnings.warn("Staged scenarios are deprecated; use condition experiments for unbiased comparisons.",
                  DeprecationWarning, stacklevel=2)
    data = yaml.safe_load(pathlib.Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: a scenario is a YAML mapping")
    scenario = Scenario.model_validate(data)
    unknown = [k for k in scenario.stop_on if k not in KINDS]
    if unknown:
        raise ValueError(f"{path}: stop_on names unknown flag kinds {unknown}; known: {KINDS}")
    return scenario


def stage_manifests(scenario: Scenario, scenario_path: pathlib.Path, out_dir: pathlib.Path) -> list[pathlib.Path]:
    """Write one validated manifest per stage under out_dir; the run label is the scenario name."""
    base_path = (scenario_path.parent / scenario.base).resolve()
    base = yaml.safe_load(base_path.read_text(encoding="utf-8"))
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, stage in enumerate(scenario.stages, start=1):
        manifest = deep_merge(base, stage.patch)
        manifest["name"] = f"{scenario.name}-{stage.name}"
        manifest.setdefault("gateway", {})["run_label"] = scenario.name
        _absolutize(manifest, base_path.parent)
        path = out_dir / f"stage-{i}-{stage.name}.yaml"
        path.write_text(yaml.safe_dump(manifest, sort_keys=False))
        report = validate_manifest(path)
        if not report.ok:
            raise ValueError(f"stage {i} ({stage.name}): {report.errors}")
        paths.append(path)
    return paths


def _bundle_facts(result) -> dict[str, Any]:
    facts: dict[str, Any] = {"run_id": result.run_id, "status": result.status, "bundle": str(result.bundle_path),
                             "rewards": result.rewards, "error": result.error, "flags": {}}
    try:
        bundle = RunBundle.open(result.bundle_path)
    except FileNotFoundError:
        return facts
    facts["flags"] = flag_counts(bundle.flags())
    report = bundle_report(bundle.root)
    facts.update({"agent_hours": report["agent_hours"], "model_calls": report["model_calls"],
                  "wallclock_seconds": report["wallclock_seconds"]})
    return facts


async def run_scenario(scenario: Scenario, scenario_path: pathlib.Path, out_dir: pathlib.Path,
                       run_one: RunOne) -> dict[str, Any]:
    manifests = stage_manifests(scenario, scenario_path, out_dir)
    log_path = out_dir / "runs.jsonl"

    def log(rec: dict[str, Any]) -> None:
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    result: dict[str, Any] = {"name": scenario.name, "base": scenario.base, "started": time.time(), "stages": [],
                              "minimum_elicitation_stage": dict.fromkeys(KINDS), "stopped_at": None}
    for i, (stage, path) in enumerate(zip(scenario.stages, manifests, strict=True), start=1):
        if result["stopped_at"] is not None:
            result["stages"].append({"index": i, "name": stage.name, "manifest": str(path), "runs": [],
                                     "flags": {}, "not_run": True})
            continue
        runs = []
        for _ in range(stage.repeat):
            run, attempts = await run_with_retries(run_one, path, scenario.retries, scenario.retry_delay_seconds,
                                                   log, i)
            runs.append({**_bundle_facts(run), "attempts": attempts})
        flags: dict[str, int] = {}
        for r in runs:
            for kind, n in r["flags"].items():
                flags[kind] = flags.get(kind, 0) + n
                if result["minimum_elicitation_stage"].get(kind) is None:
                    result["minimum_elicitation_stage"][kind] = i
        result["stages"].append({"index": i, "name": stage.name, "manifest": str(path), "runs": runs,
                                 "flags": dict(sorted(flags.items())),
                                 "ok": sum(1 for r in runs if r["status"] == "ok")})
        if any(kind in scenario.stop_on for kind in flags):
            result["stopped_at"] = i
    result["finished"] = time.time()
    (out_dir / "scenario.json").write_text(json.dumps(result, indent=1, ensure_ascii=False))
    return result


def scenario_table(out_dir: pathlib.Path) -> dict[str, Any]:
    """The M4 table from a finished (or interrupted) scenario directory."""
    result = json.loads((out_dir / "scenario.json").read_text(encoding="utf-8"))
    rows = []
    for stage in result["stages"]:
        runs = stage.get("runs") or []
        hours = sum(r.get("agent_hours") or 0 for r in runs)
        flags = stage.get("flags") or {}
        rows.append({"stage": stage["index"], "name": stage["name"], "runs": len(runs),
                     "ok": sum(1 for r in runs if r["status"] == "ok"), "flags": flags,
                     "agent_hours": round(hours, 4),
                     "flags_per_agent_hour": round(sum(flags.values()) / hours, 3) if hours else None,
                     "not_run": bool(stage.get("not_run"))})
    return {"name": result["name"], "stages": rows, "minimum_elicitation_stage": result["minimum_elicitation_stage"],
            "stopped_at": result.get("stopped_at")}
