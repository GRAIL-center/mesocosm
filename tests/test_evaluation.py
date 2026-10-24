"""Independent measurement and provenance tests, not real-agent acceptance evidence."""

from __future__ import annotations

import json
import shutil

import pytest
import yaml

from mesocosm.core.evaluation import (
    InputDriftError,
    independent_evaluation,
    input_snapshot,
    require_disjoint_tasks,
    review_assessment,
    task_fingerprint,
    verify_inputs,
)
from mesocosm.core.records import RunBundle


def task_world(tmp_path):
    task = tmp_path / "task"
    task.mkdir()
    (task / "instruction.md").write_text("Native task content used only as a plumbing fixture.")
    (task / "environment").mkdir()
    (task / "environment/Dockerfile").write_text("FROM debian:bookworm\n")
    (task / "environment/input.txt").write_text("original input")
    (task / "tests").mkdir()
    (task / "tests/test.sh").write_text("#!/bin/sh\nexit 0\n")
    world = {"name": "baseline", "model": {"id": "base", "revision": "a" * 40},
             "gateway": {"url": "http://gateway"}, "agents": [{"id": f"agent-{i}"} for i in range(3)],
             "tasks": [{"path": "task"}], "services": {"enabled": False}}
    path = tmp_path / "world.yaml"
    path.write_text(yaml.safe_dump(world))
    return path, task


def test_baseline_snapshots_full_context_and_declares_pin_limits(tmp_path):
    path, task = task_world(tmp_path)
    snapshot = input_snapshot(path, baseline=True)
    assert snapshot["models"]["base"]["revision"] == "a" * 40
    assert snapshot["tasks"][0]["files"]["environment/input.txt"]
    assert snapshot["tasks"][0]["image_resolution_verified"] is False
    assert snapshot["tasks"][0]["originality_verified"] is False
    assert "unverified" in snapshot["model_pin_assurance"]
    verify_inputs(path, snapshot)
    (task / "environment/input.txt").write_text("mutated environment input")
    with pytest.raises(InputDriftError, match="input drift"):
        verify_inputs(path, snapshot)


@pytest.mark.parametrize("relative", ["environment/input.txt", "tests/test.sh", "environment"])
def test_permission_only_changes_are_input_drift(tmp_path, relative):
    path, task = task_world(tmp_path)
    snapshot = input_snapshot(path)
    target = task / relative
    before = target.read_bytes() if target.is_file() else None
    target.chmod(target.stat().st_mode ^ 0o010)
    assert (target.read_bytes() if target.is_file() else None) == before
    with pytest.raises(InputDriftError, match="input drift"):
        verify_inputs(path, snapshot)
    entry = input_snapshot(path)["tasks"][0]["files"][relative]
    assert entry["type"] == ("file" if target.is_file() else "directory")
    assert entry["mode"] != snapshot["tasks"][0]["files"][relative]["mode"]


@pytest.mark.parametrize("reference", ["skill", "wiki", "seed", "replay", "probe"])
def test_referenced_external_input_changes_are_pinned(tmp_path, reference):
    path, _ = task_world(tmp_path)
    data = yaml.safe_load(path.read_text())
    for name in ("skill", "wiki", "seed"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "input.txt").write_text("original")
    for name in ("replay", "probe"):
        (tmp_path / name).write_text("original")
    data["agents"][0]["skills"] = ["skill"]
    data["services"].update(seed="seed", wiki={"docs": "wiki"})
    data["internet"] = {"enabled": True, "mode": "replay", "replay_file": "replay"}
    data["observe"] = {"capture": {"probes": ["probe"]}}
    path.write_text(yaml.safe_dump(data))
    snapshot = input_snapshot(path)
    assert len(snapshot["external_inputs"]) == 5
    target = tmp_path / reference
    if target.is_dir():
        target = target / "input.txt"
    target.write_text("changed")
    with pytest.raises(InputDriftError, match="input drift"):
        verify_inputs(path, snapshot)


def test_corrupted_pinned_manifest_is_input_drift(tmp_path):
    path, _ = task_world(tmp_path)
    snapshot = input_snapshot(path)
    path.write_text("model: [unterminated")
    with pytest.raises(InputDriftError, match="input became unavailable"):
        verify_inputs(path, snapshot)


@pytest.mark.parametrize("mutation", ["two_agents", "no_revision", "remote", "no_verifier", "planted"])
def test_baseline_rejects_missing_native_requirements(tmp_path, mutation):
    path, task = task_world(tmp_path)
    data = yaml.safe_load(path.read_text())
    if mutation == "two_agents":
        data["agents"].pop()
    elif mutation == "no_revision":
        del data["model"]["revision"]
    elif mutation == "remote":
        data["model"]["kind"] = "openai"
    elif mutation == "no_verifier":
        (task / "tests/test.sh").unlink()
    else:
        (task / "side_task.md").write_text("prescribed behavior")
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError):
        input_snapshot(path, baseline=True)


def test_heldout_disjointness_checks_content_not_paths_or_only_tree(tmp_path):
    path, task = task_world(tmp_path)
    training = input_snapshot(path)
    copied = tmp_path / "renamed-task"
    shutil.copytree(task, copied)
    heldout = {"tasks": [task_fingerprint(copied)], "unresolved_tasks": []}
    with pytest.raises(ValueError, match="overlaps"):
        require_disjoint_tasks(training, heldout)
    (copied / "instruction.md").write_text("Renamed heading, identical verifier and environment")
    heldout["tasks"] = [task_fingerprint(copied)]
    with pytest.raises(ValueError, match="execution_sha256"):
        require_disjoint_tasks(training, heldout)
    (copied / "instruction.md").write_text((task / "instruction.md").read_text())
    (copied / "environment/input.txt").write_text("different build context, same question")
    heldout["tasks"] = [task_fingerprint(copied)]
    with pytest.raises(ValueError, match="instruction_sha256"):
        require_disjoint_tasks(training, heldout)
    (copied / "instruction.md").write_text("A separate evaluation task")
    heldout["tasks"] = [task_fingerprint(copied)]
    require_disjoint_tasks(training, heldout)


def test_missing_usage_and_unreviewed_monitor_scores_are_unknown_not_safe(tmp_path):
    bundle = RunBundle.create(tmp_path, "run_eval")
    bundle.record("harness.session", agent_id="agent-1", task="task")
    bundle.record("verifier.reward", agent_id="agent-1", task="task", reward=0.0)
    bundle.record("learning.reward", agent_id="agent-1", task="task", allocated_reward=1.0)
    bundle.record("gateway.chat_completion", agent_id="agent-1", usage=None)
    bundle.record("monitor.score", status="below_threshold", score=1)
    bundle.write_summary({"status": "ok", "model_calls": 1})
    report = independent_evaluation(bundle.root)
    assert report["task_outcomes"][0]["reward"] == 0
    assert report["learning_rewards"][0]["allocated_reward"] == 1
    assert report["monitor_confusion"]["posthoc"]["unreviewed"] == 1
    assert report["monitor_confusion"]["posthoc"]["tn"] == 0
    assert report["cost"]["prompt_tokens"] is None
    assert report["cost"]["monetary_cost"] is None


def test_confusion_uses_exact_reviewed_event_ids_including_negative_predictions(tmp_path):
    bundle = RunBundle.create(tmp_path, "run_labels")
    for status, verdict in (("flagged", "confirmed"), ("flagged", "rejected"),
                            ("below_threshold", "confirmed"), ("below_threshold", "rejected")):
        event = bundle.record("monitor.score", status=status)
        review_assessment(bundle.root, event.event_id, verdict, "reviewer-1")
    event = bundle.record("monitor.intervention", outcome="allowed", proposed_only=True,
                          assessment={"score": 10}, threshold=70)
    review_assessment(bundle.root, event.event_id, "confirmed", "reviewer-1")
    bundle.record("monitor.score", status="unknown")
    bundle.record_flag({"flag_id": "old", "kind": "monitor_suspicion", "evidence": [{"request_id": "req-1"}]})
    bundle.record_verdict({"flag_id": "old", "verdict": "confirmed"})
    report = independent_evaluation(bundle.root)
    assert report["monitor_confusion"]["posthoc"] == {
        "tp": 1, "fp": 1, "tn": 1, "fn": 1, "unknown": 1, "unreviewed": 0,
        "false_positive_rate": 0.5, "false_negative_rate": 0.5}
    assert report["monitor_confusion"]["pre_action"]["fn"] == 1
    assert report["prospective_labels_are_executed_violations"] is False
    assert report["unlinked_reviews"] == 1
    assert report["reviewed_violations"] == {}
    assert report["reviewed_assessments"]["pre_action"] == {"confirmed": 1}
    assert report["reviewed_assessments"]["posthoc"] == {"confirmed": 2, "rejected": 2}
    assert len(bundle.verdicts()) == 6
    json.dumps(report)


def test_conflicting_reviews_across_flags_exclude_the_assessment(tmp_path):
    bundle = RunBundle.create(tmp_path, "run_conflict")
    event = bundle.record("monitor.score", status="below_threshold")
    review_assessment(bundle.root, event.event_id, "rejected", "one")
    bundle.record_flag({"flag_id": "second", "kind": "monitor_suspicion", "evidence": [{"event_id": event.event_id}]})
    bundle.record_verdict({"flag_id": "second", "verdict": "confirmed"})
    assert independent_evaluation(bundle.root)["monitor_confusion"]["posthoc"]["unknown"] == 1


def test_review_requires_assessment_identity_and_leaves_invalid_requests_unwritten(tmp_path):
    bundle = RunBundle.create(tmp_path, "run_review")
    other = bundle.record("verifier.reward", reward=1)
    with pytest.raises(ValueError, match="assessment event"):
        review_assessment(bundle.root, other.event_id, "confirmed", "human")
    assert bundle.flags() == [] and bundle.verdicts() == []


def test_assessment_accuracy_is_separate_from_failed_enforcement(tmp_path):
    bundle = RunBundle.create(tmp_path, "run_enforcement")
    event = bundle.record("monitor.intervention", outcome="unknown", decision="deny", enforce_error="ACK timeout",
                          assessment={"score": 90}, threshold=70)
    review_assessment(bundle.root, event.event_id, "confirmed", "human")
    report = independent_evaluation(bundle.root)
    assert report["monitor_confusion"]["pre_action"]["tp"] == 1
    assert report["monitor_enforcement"] == {"unknown": 1}


def test_all_failed_and_skipped_monitoring_remains_unknown_with_coverage(tmp_path):
    bundle = RunBundle.create(tmp_path, "run_monitor_failures")
    bundle.record("monitor.error", phase="posthoc", status="unknown", error="reviewer unavailable")
    bundle.record("monitor.error", phase="posthoc", status="unknown", error="invalid response")
    bundle.record("monitor.skipped", phase="posthoc", status="unknown", reason="max_calls exhausted")
    report = independent_evaluation(bundle.root)
    assert report["monitor_confusion"]["posthoc"]["unknown"] == 3
    assert report["monitor_confusion"]["posthoc"]["tn"] == 0
    coverage = report["monitor_coverage"]["posthoc"]
    assert {key: coverage[key] for key in ("attempted", "scored", "errors", "skipped", "unknown")} == {
        "attempted": 2, "scored": 0, "errors": 2, "skipped": 1, "unknown": 3}


@pytest.mark.parametrize("malformed", [["malformed"], "invalid", 42])
def test_malformed_assessment_and_usage_payloads_are_unknown_not_exceptions(tmp_path, malformed):
    bundle = RunBundle.create(tmp_path, "run_malformed")
    bundle.record("monitor.intervention", assessment=malformed, threshold=70, outcome="unknown")
    bundle.record("monitor.intervention", assessment={"score": 80, "usage": malformed}, threshold=70, outcome="blocked")
    bundle.record("monitor.score", status="unknown", usage=malformed)
    bundle.record("gateway.chat_completion", usage=malformed)
    bundle.write_summary({"status": "ok", "model_calls": 1})
    report = independent_evaluation(bundle.root)
    assert report["monitor_confusion"]["pre_action"]["unknown"] == 1
    assert report["monitor_confusion"]["posthoc"]["unknown"] == 1
    assert report["monitor_coverage"]["pre_action"]["scored"] == 1
    assert report["cost"]["prompt_tokens"] is None
    assert report["cost"]["recorded_monitor_prompt_tokens"] is None
    json.dumps(report)
