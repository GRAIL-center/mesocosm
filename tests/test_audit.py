"""Anchors establish retained bytes against a supplied trust root, never execution truth."""

from __future__ import annotations

import json
import pathlib
import shutil

import pytest

from mesocosm.core.audit import BundleAnchor, event_graph, verify_anchor, write_anchor
from mesocosm.core.records import Event, EventIdentity, EventLink, RunBundle


def bundle_with_anchor(tmp_path):
    bundle = RunBundle.create(tmp_path / "runs", "run_audit")
    bundle.write_manifest({"name": "audit"}, digest="manifest-digest")
    bundle.record("task.loaded", task="original-task")
    bundle.write_native("agent-1/messages.json", '[{"text":"actual response"}]')
    bundle.write_summary({"status": "ok"})
    retained = tmp_path / "retained-anchor.json"
    anchor = write_anchor(bundle.root, retained)
    return bundle, retained, anchor


def test_detached_anchor_verifies_exact_tree_but_declares_limits(tmp_path):
    bundle, retained, anchor = bundle_with_anchor(tmp_path)
    assert set(anchor.files) == {"manifest.json", "events.jsonl", "native/agent-1/messages.json", "summary.json"}
    report = verify_anchor(bundle.root, retained)
    assert report["integrity"] == "verified"
    assert report["tree_sha256"] == anchor.tree_sha256
    assert report["trusted_anchor_required"] is True
    assert report["independent_custody_verified"] is False
    assert report["execution_truth_verified"] is False
    assert report["capture_completeness_verified"] is False
    with pytest.raises(FileExistsError):
        write_anchor(bundle.root, retained)


def test_anchor_detects_changes_additions_and_deletions(tmp_path):
    bundle, retained, _ = bundle_with_anchor(tmp_path)
    (bundle.root / "native/agent-1/messages.json").write_text("[]")
    (bundle.root / "summary.json").unlink()
    (bundle.root / "invented.json").write_text("{}")
    report = verify_anchor(bundle.root, retained)
    assert report["integrity"] == "failed"
    assert report["modified"] == ["native/agent-1/messages.json"]
    assert report["missing"] == ["summary.json"]
    assert report["added"] == ["invented.json"]


def test_rewriting_bundle_and_generating_new_anchor_cannot_match_retained_anchor(tmp_path):
    bundle, retained, original = bundle_with_anchor(tmp_path)
    (bundle.root / "events.jsonl").write_text('{"kind":"fabricated"}\n')
    replacement = tmp_path / "replacement-anchor.json"
    rewritten = write_anchor(bundle.root, replacement)
    assert rewritten.tree_sha256 != original.tree_sha256
    assert verify_anchor(bundle.root, replacement)["integrity"] == "verified"
    assert verify_anchor(bundle.root, retained)["integrity"] == "failed"


def test_copy_can_be_verified_at_different_location(tmp_path):
    bundle, retained, _ = bundle_with_anchor(tmp_path)
    copy = tmp_path / "copy"
    shutil.copytree(bundle.root, copy)
    assert verify_anchor(copy, retained)["integrity"] == "verified"


@pytest.mark.parametrize("filename", ["anchor.json", "native/anchor.json"])
def test_anchor_cannot_be_retained_inside_bundle(tmp_path, filename):
    bundle, _, _ = bundle_with_anchor(tmp_path)
    with pytest.raises(ValueError, match="outside"):
        write_anchor(bundle.root, bundle.root / filename)


def test_anchor_path_through_symlink_cannot_escape_outside_requirement(tmp_path):
    bundle, _, _ = bundle_with_anchor(tmp_path)
    alias = tmp_path / "alias"
    alias.symlink_to(bundle.root, target_is_directory=True)
    with pytest.raises(ValueError, match="outside"):
        write_anchor(bundle.root, alias / "anchor.json")


@pytest.mark.parametrize("directory", [False, True])
def test_audit_rejects_symlinked_content(tmp_path, directory):
    bundle, retained, _ = bundle_with_anchor(tmp_path)
    target = tmp_path / "outside"
    if directory:
        target.mkdir()
    else:
        target.write_text("outside")
    (bundle.root / "link").symlink_to(target, target_is_directory=directory)
    with pytest.raises(ValueError, match="symlink"):
        verify_anchor(bundle.root, retained)


def test_anchor_inventory_digest_is_validated(tmp_path):
    _, retained, _ = bundle_with_anchor(tmp_path)
    data = json.loads(retained.read_text())
    data["files"]["events.jsonl"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="tree digest"):
        BundleAnchor.model_validate(data)


def test_mutating_bundle_during_hashing_is_rejected(tmp_path, monkeypatch):
    bundle, _, _ = bundle_with_anchor(tmp_path)
    from mesocosm.core import audit

    original = audit._inventory
    count = 0

    def inventory(path: pathlib.Path):
        nonlocal count
        count += 1
        if count == 2:
            bundle.record("late.event")
        return original(path)

    monkeypatch.setattr(audit, "_inventory", inventory)
    with pytest.raises(ValueError, match="changed while hashing"):
        write_anchor(bundle.root, tmp_path / "unstable-anchor.json")
    assert not (tmp_path / "unstable-anchor.json").exists()


def test_explicit_graph_reports_unknowns_without_creating_time_window_edges():
    events = [Event(run_id="r", kind="message", agent_id="a", ts=1),
              Event(run_id="r", kind="tool", agent_id="a", ts=1.1, event_id="tool"),
              Event(run_id="r", kind="result", agent_id="a", ts=1.2, event_id="result",
                    identity=EventIdentity(agent_instance_id="a-instance", session_id="session"),
                    links=[EventLink(event_id="tool", relation="responds_to", evidence="exact tool call ID")],
                    link_coverage="partial")]
    graph = event_graph(events)
    assert graph["graph_valid"] is True and graph["links"] == 1
    assert graph["events_without_id"] == 1
    assert graph["declared_coverage"] == {"unknown": 2, "partial": 1}
    assert graph["missing_agent_identity"] == {"agent_instance_id": 2, "operator_id": 3,
                                              "session_id": 2, "policy_id": 3}
    assert graph["causality_verified"] is False
    assert graph["capture_completeness_verified"] is False


def test_invalid_graph_reports_duplicate_dangling_and_cyclic_links():
    events = [Event(run_id="r", kind="x", event_id="a",
                    links=[EventLink(event_id="b", relation="derived_from", evidence="source")]),
              Event(run_id="r", kind="x", event_id="b",
                    links=[EventLink(event_id="a", relation="derived_from", evidence="source"),
                           EventLink(event_id="missing", relation="observed", evidence="missing record")])]
    report = event_graph(events + [events[0]])
    assert report["graph_valid"] is False and report["acyclic"] is False
    assert report["duplicate_event_ids"] == ["a"]
    assert report["dangling_links"] == [{"event_id": "b", "target_event_id": "missing", "relation": "observed"}]
