"""Repeat named experimental conditions without selecting runs by their behavioral outcomes.

Every trial gets a fresh manifest and run label. The supplied run_one owns the real runtime and
must start a fresh world; this module schedules sequential trials and never simulates agents.
order_seed randomizes scheduling only. It is not a model, task, or training seed.
"""

from __future__ import annotations

import json
import math
import pathlib
import random
import secrets
import tempfile
import time
from collections import Counter
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from mesocosm.core.chain import RunOne
from mesocosm.core.config import absolutize_manifest, deep_merge
from mesocosm.core.evaluation import (
    InputDriftError,
    independent_evaluation,
    input_snapshot,
    pin_task_paths,
    verify_inputs,
)
from mesocosm.core.manifest import WorldManifest, validate_manifest
from mesocosm.core.records import RunBundle
from mesocosm.core.report import bundle_report
from mesocosm.core.run import RunResult
from mesocosm.env.stack import StackCleanupError


class ExperimentalCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,40}$")
    patch: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _reserved(self) -> ExperimentalCondition:
        gateway = self.patch.get("gateway")
        if "name" in self.patch or (isinstance(gateway, dict) and "run_label" in gateway):
            raise ValueError("name and gateway.run_label identify trials and cannot be experimental conditions")
        return self


class Experiment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,40}$")
    base: str
    conditions: list[ExperimentalCondition] = Field(min_length=1)
    repetitions: int = Field(default=1, ge=1)
    order: Literal["listed", "randomized"] = "randomized"
    order_seed: int = Field(default=0, description="Scheduling seed only; does not seed models or task execution")
    baseline: bool = Field(default=False,
                           description="Require >=3 agents, materialized native tasks and model revisions")

    @model_validator(mode="after")
    def _unique(self) -> Experiment:
        names = [c.name for c in self.conditions]
        if len(names) != len(set(names)):
            raise ValueError("experimental condition names must be unique")
        return self


def load_experiment(path: str | pathlib.Path) -> Experiment:
    try:
        data = yaml.safe_load(pathlib.Path(path).read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"{path}: invalid experiment YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path}: an experiment is a YAML mapping")
    return Experiment.model_validate(data)


def _inactive_patches(condition: ExperimentalCondition, manifest: WorldManifest) -> list[str]:
    judge = condition.patch.get("judge")
    monitor_patch = judge.get("monitor") if isinstance(judge, dict) else None
    inactive = []
    for path, patch, enabled in (("rewards", condition.patch.get("rewards"), manifest.rewards.enabled),
                                 ("judge.monitor", monitor_patch, manifest.judge.monitor.enabled)):
        if not enabled and isinstance(patch, dict):
            for key in patch.keys() - {"enabled"}:
                inactive.append(f"rejected_unsupported: {condition.name} changes {path}.{key} "
                                f"while {path}.enabled=false")
    return sorted(inactive)


def experiment_manifests(experiment: Experiment, experiment_path: pathlib.Path,
                         out_dir: pathlib.Path) -> list[dict[str, Any]]:
    """Validate every variant before writing the fresh output directory; return the ordered trial plan.

    Patch mappings merge, lists replace, and null removes a key. All relative paths, including those
    in patches, are resolved against the base manifest. Existing output directories are refused.
    """
    base_path = (experiment_path.resolve().parent / experiment.base).resolve()
    base = yaml.safe_load(base_path.read_text(encoding="utf-8"))
    if not isinstance(base, dict):
        raise ValueError(f"{base_path}: a base manifest is a YAML mapping")
    out_dir = out_dir.resolve()
    if out_dir.exists():
        raise FileExistsError(f"experiment output already exists; choose a new directory: {out_dir}")
    invocation = secrets.token_hex(6)
    trials: list[dict[str, Any]] = []
    rendered: list[str] = []
    errors: list[str] = []
    with tempfile.TemporaryDirectory(prefix="mesocosm-experiment-") as temp:
        for condition in experiment.conditions:
            for repetition in range(1, experiment.repetitions + 1):
                index = len(trials) + 1
                label = f"{experiment.name[:20]}-{invocation}-t{index:04d}"
                manifest = deep_merge(base, condition.patch)
                try:
                    model = WorldManifest.model_validate(manifest)
                except ValidationError as exc:
                    errors.append(f"{condition.name} repetition {repetition}: {exc}")
                    continue
                errors.extend(_inactive_patches(condition, model))
                manifest["name"] = label
                manifest.setdefault("gateway", {})["run_label"] = label
                absolutize_manifest(manifest, base_path.parent)
                pin_task_paths(manifest, base_path.parent, required=experiment.baseline)
                filename = f"trial-{index:04d}-{condition.name}-r{repetition}.yaml"
                text = yaml.safe_dump(manifest, sort_keys=False)
                check_path = pathlib.Path(temp) / filename
                check_path.write_text(text, encoding="utf-8")
                report = validate_manifest(check_path)
                errors.extend(f"{condition.name} repetition {repetition}: {error}" for error in report.errors)
                try:
                    inputs = input_snapshot(check_path, baseline=experiment.baseline)
                except (OSError, ValueError) as exc:
                    errors.append(f"{condition.name} repetition {repetition}: {exc}")
                    continue
                trials.append({"index": index, "condition": condition.name, "repetition": repetition,
                               "manifest": str(out_dir / filename), "run_label": label, "digest": report.digest,
                               "inputs": inputs, "input_integrity": "not_checked_at_execution",
                               "warnings": report.warnings,
                               "monitor_enabled": bool(((manifest.get("judge") or {}).get("monitor") or {})
                                                       .get("enabled")),
                               "status": "not_run"})
                rendered.append(text)
    if errors:
        raise ValueError("invalid experiment conditions:\n" + "\n".join(errors))
    out_dir.mkdir(parents=True, exist_ok=False)
    for trial, text in zip(trials, rendered, strict=True):
        pathlib.Path(trial["manifest"]).write_text(text, encoding="utf-8")
        pathlib.Path(trial["manifest"]).with_suffix(".inputs.json").write_text(
            json.dumps(trial["inputs"], indent=1), encoding="utf-8")
    if experiment.order == "randomized":
        random.Random(experiment.order_seed).shuffle(trials)
    for order, trial in enumerate(trials, start=1):
        trial["order"] = order
    return trials


def _facts(result: RunResult) -> dict[str, Any]:
    facts: dict[str, Any] = {"run_id": result.run_id, "status": result.status, "bundle": str(result.bundle_path),
                             "error": result.error, "verifier_rewards": result.rewards,
                             "task_outcomes": None, "learning_rewards": None, "monitor": None,
                             "learning": None, "flags": None, "record_errors": []}
    try:
        bundle = RunBundle.open(result.bundle_path)
        summary_path = bundle.root / "summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
        if not isinstance(summary, dict):
            raise ValueError("run summary must be a JSON object")
        report = bundle_report(bundle.root)
        facts["independent_evaluation"] = independent_evaluation(bundle.root)
        metrics = ("model_calls", "prompt_tokens", "completion_tokens", "wallclock_seconds", "agent_hours")
        facts["recorded_metrics"] = {key: report[key] for key in metrics}
        facts.update(dict.fromkeys(metrics))
        # On failure, gateway records may not have been collected at all. The bundle's event counts
        # are retained as recorded evidence, but are not estimates of total calls or usage.
        calls = summary.get("model_calls")
        calls_known = isinstance(calls, int) and not isinstance(calls, bool) and calls >= 0
        model_events = [event for event in bundle.events() if event.kind == "gateway.chat_completion"]
        capture_complete = calls_known and len(model_events) == calls
        facts["metric_coverage"] = {"gateway_records_complete": capture_complete,
                                     "model_calls_recorded": len(model_events), "usage_records": {}}
        if calls_known:
            facts["model_calls"] = calls
        for key in ("prompt_tokens", "completion_tokens"):
            values = [(event.data.get("usage") or {}).get(key) for event in model_events]
            observed = [value for value in values if isinstance(value, int) and not isinstance(value, bool)
                        and value >= 0]
            facts["metric_coverage"]["usage_records"][key] = len(observed)
            if capture_complete and len(observed) == calls:
                facts[key] = sum(observed)
        if isinstance(summary.get("agent_seconds"), dict):
            facts["agent_hours"] = report["agent_hours"]
        if "events" in summary:
            facts["wallclock_seconds"] = report["wallclock_seconds"]
        # Failure summaries can precede detection; only an explicit flags result establishes zero flags.
        if isinstance(summary.get("flags"), dict):
            facts["flags"] = report["flags"]
        for key in ("task_outcomes", "task_measurements", "learning_rewards", "learning", "monitor"):
            facts[key] = summary.get(key)
        if not summary:
            facts["record_errors"].append("run summary is missing")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        facts["record_errors"].append(f"{type(exc).__name__}: {exc}")
    return facts


def _numbers(values: list[Any]) -> dict[str, Any]:
    observed = [float(v) for v in values if isinstance(v, int | float) and not isinstance(v, bool)
                and math.isfinite(v)]
    return {"observed": len(observed), "missing": len(values) - len(observed),
            "sum": sum(observed) if observed else None,
            "mean": sum(observed) / len(observed) if observed else None}


def _mapping_metric(trials: list[dict[str, Any]], key: str) -> dict[str, Any]:
    mappings = [t[key] for t in trials if isinstance(t.get(key), dict)]
    names = sorted({name for mapping in mappings for name in mapping})
    return {"observed_trials": len(mappings), "missing_trials": len(trials) - len(mappings),
            "by_key": {name: _numbers([t.get(key, {}).get(name) if isinstance(t.get(key), dict) else None
                                       for t in trials]) for name in names}}


def _outcome_metric(trials: list[dict[str, Any]], key: str, reward_key: str) -> dict[str, Any]:
    lists = [t[key] for t in trials if isinstance(t.get(key), list)]
    records = [record for values in lists for record in values if isinstance(record, dict)]
    result = {"observed_trials": len(lists), "missing_trials": len(trials) - len(lists),
              "records": len(records), "rewards": _numbers([record.get(reward_key) for record in records])}
    if key == "task_outcomes":
        result["statuses"] = dict(Counter(record.get("status", "unknown") for record in records))
        measured = []
        for values in lists:
            team: dict[str, Any] = {}
            for record in values:
                if not isinstance(record, dict):
                    continue
                if record.get("measurement_scope") != "team":
                    measured.append(record.get(reward_key))
                elif not record.get("measurement_id"):
                    measured.append(None)
                else:
                    identity, reward = record["measurement_id"], record.get(reward_key)
                    if identity in team and team[identity] != reward:
                        team[identity] = None  # Conflicting copies do not establish a measurement.
                    else:
                        team[identity] = reward
            measured.extend(team.values())
        result["rewards"] = _numbers(measured)
        result["reward_scope"] = "one score per team measurement ID within a trial; otherwise per-agent outcome"
    else:
        result["sources"] = dict(Counter(record.get("source", "unknown") for record in records))
    return result


def _monitor_metric(trials: list[dict[str, Any]]) -> dict[str, Any]:
    enabled = [t for t in trials if t.get("monitor_enabled")]
    observed = [t["monitor"] for t in enabled if isinstance(t.get("monitor"), dict)]
    keys = ("scored", "flagged", "calls", "unknown", "skipped", "truncated", "windows", "allowed", "blocked")
    return {"enabled_trials": len(enabled), "disabled_trials": len(trials) - len(enabled),
            "observed_trials": len(observed), "missing_trials": len(enabled) - len(observed),
            "metrics": {key: _numbers([t["monitor"].get(key) if isinstance(t.get("monitor"), dict) else None
                                       for t in enabled]) for key in keys}}


def experiment_summary(trials: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Independent outcome families; absent observations remain missing rather than becoming zero."""
    rows = []
    for condition in dict.fromkeys(t["condition"] for t in sorted(trials, key=lambda t: t["index"])):
        group = [t for t in trials if t["condition"] == condition]
        observed_flags = [t["flags"] for t in group if isinstance(t.get("flags"), dict)]
        flags: Counter[str] = Counter()
        for counts in observed_flags:
            flags.update(counts)
        rows.append({"condition": condition, "trials": len(group),
                     "statuses": dict(Counter(t["status"] for t in group)),
                     "verifier_rewards": _mapping_metric(group, "verifier_rewards"),
                     "task_outcomes": _outcome_metric(group, "task_outcomes", "reward"),
                     "learning_rewards": _outcome_metric(group, "learning_rewards", "allocated_reward"),
                     "monitor": _monitor_metric(group),
                     "flags": {"counts": dict(sorted(flags.items())), "observed_trials": len(observed_flags),
                               "missing_trials": len(group) - len(observed_flags),
                               "trials_with_flags": sum(bool(counts) for counts in observed_flags)},
                     "record_errors": sum(bool(t.get("record_errors")) for t in group),
                     **{key: _numbers([t.get(key) for t in group])
                        for key in ("model_calls", "prompt_tokens", "completion_tokens", "agent_hours",
                                    "wallclock_seconds")}})
    return rows


def _save(out_dir: pathlib.Path, result: dict[str, Any]) -> None:
    trials = result["trials"]
    result.update({"conditions": experiment_summary(trials),
                   "ok": sum(t["status"] == "ok" for t in trials),
                   "stopped": sum(t["status"] == "stopped" for t in trials),
                   "failed": sum(t["status"].startswith("failed") for t in trials),
                   "not_run": sum(t["status"] == "not_run" for t in trials),
                   "incomplete_records": sum(bool(t.get("record_errors")) for t in trials)})
    temporary = out_dir / "experiment.json.tmp"
    temporary.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    temporary.replace(out_dir / "experiment.json")


def _cleanup_failure(result: dict[str, Any], error: StackCleanupError, phase: str) -> None:
    failures = result.setdefault("lifecycle_errors", [])
    record = {"phase": phase, "compose_file": error.compose_file, "error": str(error)}
    if not any(item["compose_file"] == error.compose_file and item["error"] == str(error) for item in failures):
        failures.append(record)
    if result["status"] != "interrupted":
        result["status"] = "failed_cleanup"


def record_cleanup_failure(out_dir: pathlib.Path, error: StackCleanupError, phase: str) -> dict[str, Any]:
    """Attach teardown failure without replacing completed trial outcomes or their bundle references."""
    result = json.loads((out_dir / "experiment.json").read_text(encoding="utf-8"))
    _cleanup_failure(result, error, phase)
    _save(out_dir, result)
    return result


async def run_experiment(experiment: Experiment, experiment_path: pathlib.Path, out_dir: pathlib.Path,
                         run_one: RunOne) -> dict[str, Any]:
    """Run every prevalidated trial once, retaining task failures, exceptions and incomplete measurements.

    No outcome-dependent stopping or retries: replacement runs would bias condition comparisons.
    Cancellation saves completed outcomes and the remaining trial plan, then propagates to the caller.
    """
    out_dir = out_dir.resolve()
    trials = experiment_manifests(experiment, experiment_path, out_dir)
    result: dict[str, Any] = {"name": experiment.name, "spec": experiment.model_dump(mode="json"),
                             "experiment": str(experiment_path.resolve()), "status": "running",
                             "order_seed_scope": "trial scheduling only", "started": time.time(), "trials": trials,
                             "lifecycle_errors": []}
    _save(out_dir, result)
    for trial in trials:
        trial.update(status="running", started=time.time())
        _save(out_dir, result)
        try:
            verify_inputs(pathlib.Path(trial["manifest"]), trial["inputs"])
            trial["input_integrity"] = "verified_before"
            trial.update(_facts(await run_one(pathlib.Path(trial["manifest"]))))
            verify_inputs(pathlib.Path(trial["manifest"]), trial["inputs"])
            trial["input_integrity"] = "verified_before_and_after"
        except InputDriftError as exc:
            if trial["status"] == "running":
                trial["status"] = "not_run"
            trial.update(input_integrity="failed", input_error=str(exc))
            result["status"] = "failed_input_drift"
        except StackCleanupError as exc:
            trial.update(status="not_run", error=str(exc), reason="previous_world_cleanup_failed")
            _cleanup_failure(result, exc, "before_trial")
        except Exception as exc:
            trial.update(status="failed_exception", error=f"{type(exc).__name__}: {exc}")
        except BaseException as exc:
            trial.update(status="interrupted", error=type(exc).__name__, finished=time.time())
            result.update(status="interrupted", finished=time.time())
            _save(out_dir, result)
            raise
        trial["finished"] = time.time()
        with (out_dir / "runs.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(trial, ensure_ascii=False) + "\n")
        _save(out_dir, result)
        if result["status"] in ("failed_cleanup", "failed_input_drift"):
            break
    if result["status"] == "running":
        result["status"] = "completed"
    result["finished"] = time.time()
    _save(out_dir, result)
    return result


def experiment_table(out_dir: pathlib.Path) -> dict[str, Any]:
    """Read a completed or interrupted experiment without discarding missing or failed trials."""
    result = json.loads((out_dir / "experiment.json").read_text(encoding="utf-8"))
    return {**{key: result[key] for key in ("name", "status", "order_seed_scope", "ok", "stopped", "failed",
                                           "not_run", "incomplete_records", "conditions")},
            "lifecycle_errors": result.get("lifecycle_errors", [])}
