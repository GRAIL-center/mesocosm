"""Pinned experimental inputs and independent measurements, with unknown coverage preserved."""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import platform
import stat
from collections import Counter
from typing import Any

import yaml

from mesocosm.adapters.harbor.task import load_task, resolve_dataset_tasks
from mesocosm.core.manifest import WorldManifest
from mesocosm.core.records import Event, RunBundle
from mesocosm.core.research import validate_research_tasks
from mesocosm.core.rewards import outcome_records
from mesocosm.env.task_runtime import environment_source


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def pin_task_paths(data: dict[str, Any], base: pathlib.Path, *, required: bool = False) -> None:
    """Resolve once, preserving the registry reference while fixing the imported task paths."""
    roots = [pathlib.Path(p).expanduser() for p in os.environ.get("MESOCOSM_TASKS_ROOT", "").split(os.pathsep) if p]
    tasks = []
    for spec in data.get("tasks") or []:
        if spec.get("path") or not spec.get("dataset"):
            tasks.append(spec)
            continue
        try:
            resolved = resolve_dataset_tasks(spec["dataset"], [*roots, base], spec.get("limit"))
        except FileNotFoundError:
            if required:
                raise
            tasks.append(spec)
            continue
        tasks.extend({**spec, "path": str(path), "name": spec.get("name") if len(resolved) == 1 else path.name}
                     for path in resolved)
    data["tasks"] = tasks


def _path_fingerprint(path: pathlib.Path) -> dict[str, Any]:
    """Capture file bytes and modes, plus directory modes and entry types."""
    files: dict[str, dict[str, Any]] = {}
    for item in [path, *sorted(path.rglob("*"))] if path.is_dir() else [path]:
        info = item.lstat()
        regular, directory = stat.S_ISREG(info.st_mode), stat.S_ISDIR(info.st_mode)
        if not (regular or directory):
            raise ValueError(f"rejected_unsupported: input contains symlink or special file: {item}")
        entry: dict[str, Any] = {"type": "file" if regular else "directory", "mode": stat.S_IMODE(info.st_mode)}
        if regular:
            with item.open("rb") as stream:
                entry["sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
            after = item.lstat()
            if (info.st_mode, info.st_size, info.st_mtime_ns) != (after.st_mode, after.st_size, after.st_mtime_ns):
                raise ValueError(f"input changed while hashing: {item}")
        files[item.relative_to(path).as_posix()] = entry
    return {"path": str(path.resolve()), "tree_sha256": _digest(files), "files": files}


def task_fingerprint(path: pathlib.Path) -> dict[str, Any]:
    """Hash the complete task/build/verifier tree, independent of its directory name."""
    task = load_task(path)
    fingerprint = _path_fingerprint(path)
    files = fingerprint["files"]
    execution_files = {name: digest for name, digest in files.items()
                       if name in ("tests", "environment") or name.startswith(("tests/", "environment/"))}
    return {**fingerprint,
            "execution_sha256": _digest(execution_files) if "tests/test.sh" in files else None,
            "instruction_sha256": task.provenance()["instruction_sha256"],
            "environment": task.config.get("environment") or {},
            "verifier": task.config.get("verifier") or {},
            "originality_verified": False, "image_resolution_verified": False}


def _external_inputs(world: WorldManifest, base: pathlib.Path) -> list[dict[str, Any]]:
    references = [(f"agents.{agent.id}.skills.{index}", path)
                  for agent in world.agents for index, path in enumerate(agent.skills)]
    references.extend((role, path) for role, path in (("services.wiki.docs", world.services.wiki.docs),
                                                      ("services.seed", world.services.seed),
                                                      ("internet.replay_file", world.internet.replay_file)) if path)
    references.extend((f"observe.capture.probes.{index}", path) for index, path in
                      enumerate(world.observe.capture.probes))
    return [{"role": role, **_path_fingerprint((base / path).resolve())} for role, path in references]


def input_snapshot(manifest_path: pathlib.Path, *, baseline: bool = False) -> dict[str, Any]:
    raw = manifest_path.read_bytes()
    data = yaml.safe_load(raw)
    world = WorldManifest.model_validate(data)
    if baseline and world.profile != "research":
        raise ValueError("baseline requires profile: research")
    if baseline and (len(world.agents) < 3 or world.stream.dir or not world.tasks):
        raise ValueError("rejected_unsupported: baseline requires >=3 agents and a fixed task collection")
    pin_task_paths(data, manifest_path.parent, required=baseline)
    tasks, unresolved = [], []
    for spec in data["tasks"]:
        if not spec.get("path"):
            unresolved.append(spec)
            continue
        path = (manifest_path.parent / spec["path"]).resolve()
        task = load_task(path)
        validate_research_tasks(world, [task])
        if baseline:
            environment_source(task)
            if not task.has_tests:
                raise ValueError("baseline requires the task's original tests/test.sh verifier")
            if any((path / name).exists() for name in ("side_task.md", "agents", "instances", "protected")):
                raise ValueError("baseline refuses appended side tasks and planted fixture content")
        tasks.append({"dataset": spec.get("dataset"), **task_fingerprint(path)})
    models = {world.model_for(agent).id: world.model_for(agent).model_dump(mode="json") for agent in world.agents}
    if baseline and any(not model.get("revision") or model["kind"] != "vllm" for model in models.values()):
        raise ValueError("baseline requires declared immutable revisions on local vLLM models; "
                         "remote serving identity cannot be pinned by an alias")
    return {"schema_version": "mesocosm.inputs.v1", "manifest_sha256": hashlib.sha256(raw).hexdigest(),
            "manifest_digest": world.digest(), "tasks": tasks, "unresolved_tasks": unresolved,
            "external_inputs": _external_inputs(world, manifest_path.parent),
            "models": models, "model_pin_assurance": "declared_revision; serving_identity_unverified",
            "runtime": {"python": platform.python_version(), "system": platform.system(),
                        "machine": platform.machine(), "native_profile": "native-task-with-declared-services",
                        "task_services": world.task_services,
                        "container_image_identity": "recorded by native runtime, not resolved by this snapshot"},
            "sampling_determinism_verified": False, "baseline": baseline}


class InputDriftError(ValueError):
    """A pinned experimental input changed or is no longer readable."""


def verify_inputs(manifest_path: pathlib.Path, expected: dict[str, Any]) -> None:
    try:
        current = input_snapshot(manifest_path, baseline=expected["baseline"])
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise InputDriftError(f"experimental input became unavailable: {exc}") from exc
    if current != expected:
        raise InputDriftError("experimental input drift: manifest, model metadata, task tree or runtime changed")


def require_disjoint_tasks(training: dict[str, Any], evaluation: dict[str, Any]) -> None:
    """Conservatively reject copied tasks and identical instructions even under new paths."""
    if training["unresolved_tasks"] or evaluation["unresolved_tasks"]:
        raise ValueError("held-out evaluation requires materialized task contents for both worlds")
    if not training["tasks"] or not evaluation["tasks"]:
        raise ValueError("held-out evaluation requires nonempty training and evaluation task sets")
    for key in ("tree_sha256", "instruction_sha256", "execution_sha256"):
        overlap = {task[key] for task in training["tasks"] if task.get(key)} & {
            task[key] for task in evaluation["tasks"] if task.get(key)}
        if overlap:
            raise ValueError(f"held-out evaluation overlaps training task content ({key})")


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _prediction(event: Event, configured_threshold: Any = None) -> bool | None:
    if event.kind == "monitor.score":
        status = event.data.get("status")
        return True if status == "flagged" else False if status == "below_threshold" else None
    score = _mapping(event.data.get("assessment")).get("score")
    threshold = event.data.get("threshold", configured_threshold)
    return score >= threshold if type(score) is int and type(threshold) is int and 0 <= score <= 100 else None


def _monitor_phase(event: Event) -> str:
    return ("pre_action" if event.kind == "monitor.intervention" or event.data.get("phase") == "pre_action"
            else "posthoc")


def _score(event: Event) -> int | None:
    score = (event.data.get("score") if event.kind == "monitor.score" else
             _mapping(event.data.get("assessment")).get("score"))
    return score if type(score) is int and 0 <= score <= 100 else None


def review_assessment(bundle_path: str | pathlib.Path, event_id: str, verdict: str,
                      reviewer: str, note: str | None = None) -> dict[str, Any]:
    """Review any assessment, including a negative prediction, using its exact event ID."""
    from mesocosm.judge.review import VERDICTS, review

    if verdict not in VERDICTS or not reviewer.strip():
        raise ValueError("an explicit reviewer and confirmed/rejected/unclear verdict are required")
    bundle = RunBundle.open(bundle_path)
    found = [event for event in bundle.events() if event.event_id == event_id
             and event.kind in ("monitor.score", "monitor.intervention")]
    if len(found) != 1:
        raise ValueError("assessment event ID must identify exactly one recorded monitor assessment")
    event = found[0]
    flag_id = f"assessment_{event_id}"
    if not any(flag["flag_id"] == flag_id for flag in bundle.flags()):
        bundle.record_flag({"flag_id": flag_id, "run_id": bundle.run_id, "agent_id": event.agent_id,
                            "kind": "assessment_review", "detector": "human_selected_assessment",
                            "detector_version": "1", "summary": "Independent review of a recorded assessment",
                            "evidence": [{"event_id": event_id, "kind": event.kind}]})
    return review(bundle.root, flag_id, verdict, reviewer, note)


def independent_evaluation(bundle_path: str | pathlib.Path) -> dict[str, Any]:
    bundle = RunBundle.open(bundle_path)
    events = bundle.events()
    summary_path = bundle.root / "summary.json"
    summary = json.loads(summary_path.read_text()) if summary_path.is_file() else {}
    manifest = bundle.manifest()["manifest"] if (bundle.root / "manifest.json").is_file() else {}
    configured_threshold = ((manifest.get("judge") or {}).get("monitor") or {}).get("threshold")
    flags, verdicts = bundle.flags(), {v["flag_id"]: v for v in bundle.verdicts()}
    assessments = [event for event in events if event.kind in ("monitor.score", "monitor.intervention")]
    unavailable = [event for event in events if event.kind in ("monitor.error", "monitor.skipped")]
    assessment_ids = {event.event_id: event for event in assessments if event.event_id}
    labels: dict[str, set[bool]] = {}
    unlinked_reviews = 0
    reviewed: Counter[str] = Counter()
    reviewed_assessments: dict[str, Counter[str]] = {phase: Counter() for phase in ("posthoc", "pre_action", "unknown")}
    for flag in flags:
        verdict = verdicts.get(flag["flag_id"], {}).get("verdict", "unreviewed")
        refs = [ref["event_id"] for ref in flag.get("evidence", []) if ref.get("event_id")]
        targets = [assessment_ids[event_id] for event_id in refs if event_id in assessment_ids]
        if targets or flag.get("kind") in ("monitor_suspicion", "assessment_review"):
            phases = {"posthoc" if event.kind == "monitor.score" else "pre_action" for event in targets}
            for phase in phases or {"unknown"}:
                reviewed_assessments[phase][verdict] += 1
            if not targets and verdict in ("confirmed", "rejected"):
                unlinked_reviews += 1
            if verdict in ("confirmed", "rejected"):
                for event in targets:
                    labels.setdefault(event.event_id or "", set()).add(verdict == "confirmed")
        else:
            reviewed[verdict] += 1
    confusion: dict[str, Any] = {}
    coverage: dict[str, Any] = {}
    for phase, kinds in (("posthoc", {"monitor.score"}), ("pre_action", {"monitor.intervention"})):
        counts: Counter[str] = Counter({key: 0 for key in ("tp", "fp", "tn", "fn", "unknown", "unreviewed")})
        for event in assessments:
            if event.kind not in kinds:
                continue
            predicted = _prediction(event, configured_threshold)
            label = labels.get(event.event_id or "", set())
            if predicted is None or len(label) > 1:
                counts["unknown"] += 1
            elif not label:
                counts["unreviewed"] += 1
            else:
                truth = next(iter(label))
                counts["tp" if predicted and truth else "fp" if predicted else "fn" if truth else "tn"] += 1
        failures = [event for event in unavailable if _monitor_phase(event) == phase]
        counts["unknown"] += len(failures)
        phase_assessments = [event for event in assessments if event.kind in kinds]
        errors = sum(event.kind == "monitor.error" for event in failures)
        skipped = sum(event.kind == "monitor.skipped" for event in failures)
        coverage[phase] = {"attempted": len(phase_assessments) + errors, "errors": errors, "skipped": skipped,
                           "assessment_records": len(phase_assessments),
                           "scored": sum(_score(event) is not None for event in phase_assessments),
                           "unknown": counts["unknown"],
                           "attempt_scope": "recorded assessment attempts; not a count of billed model calls"}
        confusion[phase] = {**counts, "false_positive_rate": counts["fp"] / (counts["fp"] + counts["tn"])
                            if counts["fp"] + counts["tn"] else None,
                            "false_negative_rate": counts["fn"] / (counts["fn"] + counts["tp"])
                            if counts["fn"] + counts["tp"] else None}
    model_events = [event for event in events if event.kind == "gateway.chat_completion"]
    declared_calls = summary.get("model_calls")
    complete = type(declared_calls) is int and declared_calls == len(model_events)
    cost: dict[str, Any] = {"model_calls": declared_calls if complete else None,
                            "recorded_model_calls": len(model_events), "gateway_capture_complete": complete,
                            "monetary_cost": None}
    for key in ("prompt_tokens", "completion_tokens"):
        values = [_mapping(event.data.get("usage")).get(key) for event in model_events]
        known = [value for value in values if type(value) is int and value >= 0]
        cost[key] = sum(known) if complete and len(known) == len(values) else None
        cost[f"recorded_{key}"] = sum(known) if known else None
        monitor_values = [_mapping(event.data.get("usage")).get(key) if event.kind == "monitor.score" else
                          _mapping(_mapping(event.data.get("assessment")).get("usage")).get(key)
                          for event in assessments]
        monitor_known = [value for value in monitor_values if type(value) is int and value >= 0]
        cost[f"recorded_monitor_{key}"] = sum(monitor_known) if monitor_known else None
    return {"run_id": bundle.run_id, "status": summary.get("status", "unknown"),
            **outcome_records(events), "reviewed_violations": dict(reviewed),
            "reviewed_violation_scope": "non-monitor candidate flags; counts are flags, not unique incidents",
            "reviewed_assessments": {phase: dict(counts) for phase, counts in reviewed_assessments.items()},
            "monitor_enforcement": dict(Counter(event.data.get("outcome", "unknown") for event in assessments
                                                 if event.kind == "monitor.intervention")),
            "monitor_confusion": confusion, "monitor_coverage": coverage, "unlinked_reviews": unlinked_reviews,
            "monitor_label_scope": "explicitly reviewed assessment events only; not population prevalence",
            "prospective_labels_are_executed_violations": False, "cost": cost}
