"""Portable transfer contract tests; these do not exercise remote agents or Docker."""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import stat
import zipfile

import pytest
import yaml

from mesocosm.core.experiment import experiment_manifests, load_experiment
from mesocosm.execution import package


def world_file(root):
    root.mkdir(parents=True, exist_ok=True)
    task = root / "original-task"
    (task / "environment").mkdir(parents=True)
    (task / "tests").mkdir()
    (task / "instruction.md").write_bytes(b"Original task\r\nwith exact bytes.\n")
    (task / "environment/Dockerfile").write_text("FROM debian:bookworm\n")
    (task / "tests/test.sh").write_text("#!/bin/sh\nexit 0\n")
    (task / "tests/test.sh").chmod(0o751)
    (task / "environment").chmod(0o750)
    world = {"name": "portable", "model": {"id": "model"},
             "gateway": {"url": "http://secret-host:8080", "api_key": "private-key",
                         "upstream_key": "upstream-secret", "run_label": "old-label"},
             "agents": [{"id": f"agent-{i}", "url": f"http://host-{i}:4096"} for i in range(3)],
             "tasks": [{"path": "original-task"}], "services": {"enabled": False}}
    path = root / "world.yaml"
    path.write_text(yaml.safe_dump(world))
    return path, task


def mutate(archive, target, transform):
    with zipfile.ZipFile(archive) as source:
        rows = [(item, source.read(item)) for item in source.infolist()]
    metadata = json.loads(rows[0][1])
    transform(metadata, rows)
    metadata["digest"] = hashlib.sha256(package._json({k: v for k, v in metadata.items() if k != "digest"})).hexdigest()
    rows[0] = (rows[0][0], package._json(metadata))
    with zipfile.ZipFile(target, "w") as output:
        for item, content in rows:
            output.writestr(item, content)


def test_world_roundtrip_preserves_task_bytes_modes_and_strips_only_control_fields(tmp_path):
    path, task = world_file(tmp_path / "source")
    raw = path.read_bytes()
    first = package.pack_submission(path, tmp_path / "one.zip")
    second = package.pack_submission(path, tmp_path / "two.zip")
    assert first["digest"] == second["digest"]
    assert "gateway.api_key" in first["stripped_fields"]
    assert "agents.agent-0.url" in first["stripped_fields"]
    unpacked = package.unpack_submission(tmp_path / "one.zip", tmp_path / "unpacked")
    assert unpacked == first
    world = yaml.safe_load((tmp_path / "unpacked/world.yaml").read_text())
    assert world["gateway"]["url"] == "http://gateway.invalid"
    assert "api_key" not in world["gateway"] and "upstream_key" not in world["gateway"]
    assert all("url" not in agent for agent in world["agents"])
    copied = tmp_path / "unpacked" / world["tasks"][0]["path"]
    assert world["tasks"][0]["name"] == "original-task"
    for original in task.rglob("*"):
        target = copied / original.relative_to(task)
        assert stat.S_IMODE(target.stat().st_mode) == stat.S_IMODE(original.stat().st_mode)
        if original.is_file():
            assert original.read_bytes() == target.read_bytes()
    assert path.read_bytes() == raw
    assert stat.S_IMODE((tmp_path / "unpacked").stat().st_mode) == 0o700
    assert b"private-key" not in (tmp_path / "one.zip").read_bytes()


def test_only_explicit_outside_roots_are_copied_and_skills_keep_names(tmp_path):
    path, _ = world_file(tmp_path / "source")
    outside = tmp_path / "external" / "a-skill"
    outside.mkdir(parents=True)
    (outside / "SKILL.md").write_text("---\nname: a-skill\ndescription: a test skill\n---\n")
    (tmp_path / "external/secret.txt").write_text("NOT REFERENCED")
    data = yaml.safe_load(path.read_text())
    data["agents"][0]["skills"] = [str(outside)]
    path.write_text(yaml.safe_dump(data))
    result = package.pack_submission(path, tmp_path / "package.zip")
    assert any(name.endswith("a-skill/SKILL.md") for name in result["files"])
    assert not any(name.endswith("secret.txt") for name in result["files"])


@pytest.mark.parametrize("invalid", ["symlink", "hardlink", "fifo", "privileged"])
def test_reject_unsafe_source_types_and_cleanup(tmp_path, invalid):
    path, task = world_file(tmp_path / "source")
    target = task / "bad"
    if invalid == "symlink":
        target.symlink_to(task / "instruction.md")
    elif invalid == "hardlink":
        os.link(task / "instruction.md", target)
    elif invalid == "fifo":
        os.mkfifo(target)
    else:
        target.write_text("data")
        target.chmod(0o4755)
    with pytest.raises(ValueError):
        package.pack_submission(path, tmp_path / "package.zip")
    assert not (tmp_path / "package.zip").exists()


@pytest.mark.parametrize("change", [
    {"profile": "fixture"}, {"containment": "adapted"}, {"mode": "persistent"},
    {"agents": [{"id": "one"}]}, {"tasks": [{"dataset": "missing@1"}]},
    {"model": {"id": "m", "upstream": "https://key:secret@example.com"}},
    {"model": {"id": "m", "upstream": "https://example.com?token=secret"}},
])
def test_reject_unsupported_worlds_and_missing_references(tmp_path, change):
    path, _ = world_file(tmp_path / "source")
    data = yaml.safe_load(path.read_text()) | change
    path.write_text(yaml.safe_dump(data))
    with pytest.raises((ValueError, FileNotFoundError)):
        package.pack_submission(path, tmp_path / "package.zip")


def test_experiment_preserves_patch_semantics_paths_null_and_inactive_checks(tmp_path):
    path, _ = world_file(tmp_path / "source")
    alternate = path.parent / "second-task"
    alternate.mkdir()
    (alternate / "instruction.md").write_text("Second original instruction")
    experiment = {"name": "compare", "base": path.name, "repetitions": 2,
                  "conditions": [{"name": "default"}, {"name": "second", "patch": {
                      "tasks": [{"path": "second-task"}], "agents": [{"id": f"agent-{i}"} for i in range(3)],
                      "memory": "carry", "conditions": None}}]}
    source = path.parent / "experiment.yaml"
    source.write_text(yaml.safe_dump(experiment))
    package.pack_submission(source, tmp_path / "experiment.zip", kind="experiment")
    destination = tmp_path / "unpacked"
    package.unpack_submission(tmp_path / "experiment.zip", destination)
    spec = load_experiment(destination / "experiment.yaml")
    assert spec.conditions[0].patch == {}
    assert "rewards" not in spec.conditions[1].patch
    assert "judge" not in spec.conditions[1].patch
    assert spec.conditions[1].patch["conditions"] is None
    trials = experiment_manifests(spec, destination / "experiment.yaml", tmp_path / "trials")
    assert len(trials) == 4
    for trial in trials:
        data = yaml.safe_load(pathlib.Path(trial["manifest"]).read_text())
        expected = "second-task" if trial["condition"] == "second" else "original-task"
        assert data["tasks"][0]["name"] == expected
    experiment["conditions"] = [{"name": "bad", "patch": {"judge": {"monitor": {"scope": "joint"}}}}]
    source.write_text(yaml.safe_dump(experiment))
    with pytest.raises(ValueError, match="enabled=false"):
        package.pack_submission(source, tmp_path / "bad.zip", kind="experiment")


def test_tampered_payload_and_duplicate_entries_refused_without_partial_directory(tmp_path):
    path, _ = world_file(tmp_path / "source")
    package.pack_submission(path, tmp_path / "good.zip")
    def change(metadata, rows):
        index = next(i for i, (item, _) in enumerate(rows) if item.filename.endswith("instruction.md"))
        rows[index] = (rows[index][0], b"X" * len(rows[index][1]))
    mutate(tmp_path / "good.zip", tmp_path / "bad.zip", change)
    with pytest.raises(ValueError, match="hash"):
        package.unpack_submission(tmp_path / "bad.zip", tmp_path / "output")
    assert not (tmp_path / "output").exists()
    with zipfile.ZipFile(tmp_path / "bad.zip", "a") as archive, pytest.warns(UserWarning):
        archive.writestr("package.json", "{}")
    with pytest.raises(ValueError, match="duplicate"):
        package.unpack_submission(tmp_path / "bad.zip", tmp_path / "output")


def test_recomputed_index_does_not_authorize_manifest_escape(tmp_path):
    path, _ = world_file(tmp_path / "source")
    package.pack_submission(path, tmp_path / "good.zip")
    def change(metadata, rows):
        index = next(i for i, (item, _) in enumerate(rows) if item.filename == "world.yaml")
        raw = yaml.safe_load(rows[index][1])
        raw["tasks"][0]["path"] = "../source/original-task"
        content = yaml.safe_dump(raw).encode()
        rows[index] = (rows[index][0], content)
        metadata["files"]["world.yaml"].update(size=len(content), sha256=hashlib.sha256(content).hexdigest())
    mutate(tmp_path / "good.zip", tmp_path / "bad.zip", change)
    with pytest.raises(ValueError, match="unsafe package path"):
        package.unpack_submission(tmp_path / "bad.zip", tmp_path / "output")
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("limit", ["MAX_FILES", "MAX_TOTAL_BYTES", "MAX_ARCHIVE_BYTES"])
def test_package_limits(tmp_path, monkeypatch, limit):
    path, _ = world_file(tmp_path / "source")
    monkeypatch.setattr(package, limit, 1)
    with pytest.raises(ValueError, match="limit"):
        package.pack_submission(path, tmp_path / "package.zip")
    assert not (tmp_path / "package.zip").exists()


def test_export_only_original_bundles_and_known_job_metadata(tmp_path):
    job = tmp_path / "job"
    bundle = job / "runs/run_actual"
    bundle.mkdir(parents=True)
    (bundle / "manifest.json").write_text('{"manifest":{"gateway":{"api_key":"ephemeral"}}}')
    (bundle / "events.jsonl").write_text('{"kind":"run.started"}\n')
    (bundle / "native").mkdir()
    (bundle / "native/transcript.json").write_text('{"messages":[]}')
    private = job / "runs/world-label/agents/agent-0"
    private.mkdir(parents=True)
    (private / "opencode.json").write_text("DO NOT EXPORT")
    (job / "result.json").write_text('{"status":"completed"}')
    (job / "job.json").write_text('{"job_id":"job-1"}')
    (job / "experiment").mkdir()
    (job / "experiment/experiment.json").write_text('{"trials":[]}')
    (job / "experiment/world.compose.yaml").write_text("DO NOT EXPORT")
    metadata = package.export_results(job, tmp_path / "result.zip")
    assert metadata["sensitive"] and not metadata["redacted"]
    assert "experiment/experiment.json" in metadata["files"]
    assert not any("opencode" in name or "compose" in name for name in metadata["files"])
    package.extract_results(tmp_path / "result.zip", tmp_path / "download")
    assert (tmp_path / "download/runs/run_actual/manifest.json").read_bytes() == (bundle / "manifest.json").read_bytes()
    with pytest.raises(FileExistsError):
        package.extract_results(tmp_path / "result.zip", tmp_path / "download")
    with pytest.raises(ValueError, match="kind"):
        package.unpack_submission(tmp_path / "result.zip", tmp_path / "wrong")


def test_archive_cannot_include_unindexed_extra_file(tmp_path):
    path, _ = world_file(tmp_path / "source")
    package.pack_submission(path, tmp_path / "package.zip")
    with zipfile.ZipFile(tmp_path / "package.zip", "a") as archive:
        archive.writestr("../escape", "bad")
    with pytest.raises(ValueError, match="complete package index"):
        package.unpack_submission(tmp_path / "package.zip", tmp_path / "output")
    assert not (tmp_path / "escape").exists()


def test_dataset_expands_once_and_preserves_reference_and_names(tmp_path, monkeypatch):
    path, task = world_file(tmp_path / "registry" / "benchmark@1")
    data = yaml.safe_load(path.read_text())
    data["tasks"] = [{"dataset": "benchmark@1", "limit": 1}]
    path.write_text(yaml.safe_dump(data))
    monkeypatch.setenv("MESOCOSM_TASKS_ROOT", str(path.parent.parent))
    package.pack_submission(path, tmp_path / "package.zip")
    monkeypatch.delenv("MESOCOSM_TASKS_ROOT")
    package.unpack_submission(tmp_path / "package.zip", tmp_path / "output")
    world = yaml.safe_load((tmp_path / "output/world.yaml").read_text())
    assert len(world["tasks"]) == 1
    assert world["tasks"][0]["dataset"] == "benchmark@1"
    assert world["tasks"][0]["name"] == task.name


def test_explicit_agent_credentials_remain_sensitive_input_not_control_settings(tmp_path):
    path, _ = world_file(tmp_path / "source")
    data = yaml.safe_load(path.read_text())
    data["agents"][0]["mcp"] = {"internal": {"type": "remote", "url": "http://mcp/",
                                             "headers": {"Authorization": "authorized-task-token"}}}
    path.write_text(yaml.safe_dump(data))
    package.pack_submission(path, tmp_path / "package.zip")
    package.unpack_submission(tmp_path / "package.zip", tmp_path / "output")
    world = yaml.safe_load((tmp_path / "output/world.yaml").read_text())
    assert world["agents"][0]["mcp"]["internal"]["headers"]["Authorization"] == "authorized-task-token"


@pytest.mark.parametrize("content", [b"not a zip", b"PK\x03\x04invalid"])
def test_malformed_archive_is_value_error(tmp_path, content):
    archive = tmp_path / "broken.zip"
    archive.write_bytes(content)
    with pytest.raises(ValueError, match="malformed"):
        package.unpack_submission(archive, tmp_path / "output")
    assert not (tmp_path / "output").exists()


def test_indexed_traversal_and_symlink_modes_rejected(tmp_path):
    path, _ = world_file(tmp_path / "source")
    package.pack_submission(path, tmp_path / "good.zip")
    def escape(metadata, rows):
        metadata["files"]["../escape"] = metadata["files"].pop("world.yaml")
        index = next(i for i, (item, _) in enumerate(rows) if item.filename == "world.yaml")
        rows[index][0].filename = "../escape"
    mutate(tmp_path / "good.zip", tmp_path / "escape.zip", escape)
    with pytest.raises(ValueError, match="unsafe"):
        package.unpack_submission(tmp_path / "escape.zip", tmp_path / "output")
    def symlink(metadata, rows):
        next(item for item, _ in rows if item.filename == "world.yaml").external_attr = (stat.S_IFLNK | 0o600) << 16
    mutate(tmp_path / "good.zip", tmp_path / "symlink.zip", symlink)
    with pytest.raises(ValueError, match="archive type"):
        package.unpack_submission(tmp_path / "symlink.zip", tmp_path / "output")
    assert not (tmp_path / "output").exists()
