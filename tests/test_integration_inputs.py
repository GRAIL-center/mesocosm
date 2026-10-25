"""Portable provider selection and input pins; no real benchmark or agent acceptance."""

from __future__ import annotations

import hashlib
import json

import pytest
import yaml
from test_execution_package import mutate, world_file

from mesocosm.core import evaluation
from mesocosm.core.config import deep_merge
from mesocosm.core.experiment import load_experiment
from mesocosm.core.manifest import WorldManifest
from mesocosm.execution import package
from mesocosm.integrations import registry
from mesocosm.integrations.tasks import Task
from mesocosm.learning.config import TrainingSpec
from mesocosm.learning.controller import training_world


@pytest.fixture
def alternate_source(monkeypatch):
    """Substitute the installed-provider boundary, using an incompatible on-disk format."""
    identity = {"id": "json-fixture", "distribution": "test-json-source", "version": "1.0",
                "api_version": 1, "target": "test_json_source:Source", "code_sha256": "a" * 64}
    calls = []

    class Source:
        api_version = 1

        def load(self, path, *, dataset=None, name=None):
            calls.append(("load", path))
            data = json.loads((path / "problem.json").read_text())
            return Task(path=path, name=name or data["id"], instruction=data["question"],
                        config={}, has_environment=False, has_tests=False, has_solution=False,
                        dataset=dataset, execution_kind=data.get("execution_kind", "harbor"))

        def resolve(self, dataset, *, roots, limit=None):
            calls.append(("resolve", dataset))
            for root in roots:
                directory = root / dataset
                if directory.is_dir():
                    return sorted(item.parent for item in directory.glob("*/problem.json"))[:limit]
            raise FileNotFoundError(dataset)

        def research_errors(self, task):
            return []

        def fingerprint(self, task):
            return {"instruction_sha256": hashlib.sha256(task.instruction.encode()).hexdigest(),
                    "execution_sha256": None, "source_metadata": "original-json",
                    "originality_verified": False, "image_resolution_verified": False,
                    "tree_sha256": "provider-cannot-replace-independent-hash"}

    provider = Source()
    original_get = registry.get_task_source
    original_identity = registry.task_source_identity

    def get(source):
        return provider if source == "json-fixture" else original_get(source)

    def selected_identity(source):
        return dict(identity) if source == "json-fixture" else original_identity(source)

    monkeypatch.setattr(registry, "get_task_source", get)
    monkeypatch.setattr(registry, "task_source_identity", selected_identity)
    monkeypatch.setattr(evaluation, "get_task_source", get)
    return identity, calls


def json_world(root):
    path, _ = world_file(root)
    task = root / "collection" / "entry"
    task.mkdir(parents=True)
    (task / "problem.json").write_text(json.dumps({"id": "original-id", "question": "Original JSON task"}))
    raw = yaml.safe_load(path.read_text())
    raw["tasks"] = [{"source": "json-fixture", "dataset": "collection"}]
    path.write_text(yaml.safe_dump(raw))
    return path, task


def test_source_dispatches_resolution_loading_and_fingerprinting(tmp_path, alternate_source):
    path, task = json_world(tmp_path)
    snapshot = evaluation.input_snapshot(path)
    recorded = snapshot["tasks"][0]
    assert recorded["source"] == "json-fixture"
    assert recorded["source_metadata"] == "original-json"
    assert recorded["tree_sha256"] != "provider-cannot-replace-independent-hash"
    assert "problem.json" in recorded["files"] and "instruction.md" not in recorded["files"]
    assert recorded["path"] == str(task)
    assert ("resolve", "collection") in alternate_source[1]
    assert snapshot["integrations"]["task_source:json-fixture"] == alternate_source[0]
    evaluation.verify_inputs(path, snapshot)


def test_collection_pinning_preserves_provider_instance_names(tmp_path, alternate_source):
    path, first = json_world(tmp_path)
    second = first.parent / "another-directory-name"
    second.mkdir()
    (second / "problem.json").write_text(json.dumps({"id": "second-native-id", "question": "Second question"}))
    raw = yaml.safe_load(path.read_text())
    evaluation.pin_task_paths(raw, path.parent, required=True)
    from mesocosm.integrations.tasks import load_task

    tasks = [load_task(spec["source"], spec["path"], name=spec["name"]) for spec in raw["tasks"]]
    assert {task.name for task in tasks} == {"original-id", "second-native-id"}


def test_source_cannot_override_independent_instruction_hash(tmp_path, alternate_source, monkeypatch):
    _, task = json_world(tmp_path)
    source = registry.get_task_source("json-fixture")
    monkeypatch.setattr(source, "fingerprint", lambda _: {"instruction_sha256": "invented"})
    result = evaluation.task_fingerprint(task, source="json-fixture")
    assert result["instruction_sha256"] == hashlib.sha256(b"Original JSON task").hexdigest()
    assert result["execution_sha256"] is None


def test_source_identity_drift_is_detected_without_task_changes(tmp_path, alternate_source):
    path, _ = json_world(tmp_path)
    snapshot = evaluation.input_snapshot(path)
    alternate_source[0]["version"] = "2.0"
    with pytest.raises(evaluation.InputDriftError, match="integration"):
        evaluation.verify_inputs(path, snapshot)


def test_portable_custom_source_retains_original_files_and_identity(tmp_path, alternate_source):
    path, task = json_world(tmp_path / "source")
    package.pack_submission(path, tmp_path / "package.zip")
    package.unpack_submission(tmp_path / "package.zip", tmp_path / "output")
    data = yaml.safe_load((tmp_path / "output/world.yaml").read_text())
    selected = data["tasks"][0]
    assert selected["source"] == "json-fixture" and selected["dataset"] == "collection"
    assert selected["name"] == "original-id"
    assert data["integration_lock"]["task_source:json-fixture"] == alternate_source[0]
    assert (tmp_path / "output" / selected["path"] / "problem.json").read_bytes() == (
        task / "problem.json").read_bytes()


def test_worker_rejects_different_source_installation_before_admission(tmp_path, alternate_source):
    path, _ = json_world(tmp_path / "source")
    package.pack_submission(path, tmp_path / "package.zip")
    alternate_source[0]["code_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="integration"):
        package.unpack_submission(tmp_path / "package.zip", tmp_path / "output")
    assert not (tmp_path / "output").exists()


def test_remote_payload_cannot_omit_integration_lock(tmp_path):
    path, _ = world_file(tmp_path / "source")
    package.pack_submission(path, tmp_path / "good.zip")

    def remove_lock(metadata, rows):
        index = next(i for i, (item, _) in enumerate(rows) if item.filename == "world.yaml")
        raw = yaml.safe_load(rows[index][1])
        raw.pop("integration_lock")
        content = yaml.safe_dump(raw).encode()
        rows[index] = (rows[index][0], content)
        metadata["files"]["world.yaml"].update(size=len(content), sha256=hashlib.sha256(content).hexdigest())

    mutate(tmp_path / "good.zip", tmp_path / "bad.zip", remove_lock)
    with pytest.raises(ValueError, match="pinned integration"):
        package.unpack_submission(tmp_path / "bad.zip", tmp_path / "output")
    assert not (tmp_path / "output").exists()


def test_experiment_source_switch_pins_every_effective_world(tmp_path, alternate_source):
    path, _ = json_world(tmp_path / "source")
    base = yaml.safe_load(path.read_text())
    base["tasks"] = [{"path": "original-task"}]
    path.write_text(yaml.safe_dump(base))
    experiment = path.parent / "experiment.yaml"
    experiment.write_text(yaml.safe_dump({"name": "sources", "base": path.name, "conditions": [
        {"name": "original"}, {"name": "json", "patch": {
            "tasks": [{"source": "json-fixture", "dataset": "collection"}]}}]}))
    package.pack_submission(experiment, tmp_path / "package.zip", kind="experiment")
    package.unpack_submission(tmp_path / "package.zip", tmp_path / "output")
    spec = load_experiment(tmp_path / "output/experiment.yaml")
    base = yaml.safe_load((tmp_path / "output/world.yaml").read_text())
    assert spec.conditions[0].patch == {}
    changed = deep_merge(base, spec.conditions[1].patch)
    assert "task_source:harbor" not in changed["integration_lock"]
    assert changed["integration_lock"]["task_source:json-fixture"] == alternate_source[0]
    assert (tmp_path / "output" / changed["tasks"][0]["path"] / "problem.json").is_file()


def test_explicit_condition_unlock_is_replaced_with_a_fresh_portable_lock(tmp_path):
    path, _ = world_file(tmp_path / "source")
    base = yaml.safe_load(path.read_text())
    base["integration_lock"] = registry.integration_snapshot(WorldManifest.model_validate(base))
    path.write_text(yaml.safe_dump(base))
    experiment = path.parent / "experiment.yaml"
    experiment.write_text(yaml.safe_dump({"name": "unlock", "base": path.name, "conditions": [
        {"name": "fresh", "patch": {"integration_lock": None}}]}))
    package.pack_submission(experiment, tmp_path / "package.zip", kind="experiment")
    package.unpack_submission(tmp_path / "package.zip", tmp_path / "output")
    spec = load_experiment(tmp_path / "output/experiment.yaml")
    resolved_base = yaml.safe_load((tmp_path / "output/world.yaml").read_text())
    assert deep_merge(resolved_base, spec.conditions[0].patch)["integration_lock"] == base["integration_lock"]


@pytest.mark.parametrize("execution_kind", ["harbor", "foreign-runtime"])
def test_training_source_support_depends_on_native_execution_contract(tmp_path, alternate_source, execution_kind):
    path, task = json_world(tmp_path)
    data = yaml.safe_load(path.read_text())
    data["rewards"] = {"enabled": True}
    path.write_text(yaml.safe_dump(data))
    original = json.loads((task / "problem.json").read_text())
    (task / "problem.json").write_text(json.dumps({**original, "execution_kind": execution_kind}))
    spec = TrainingSpec(name="source-training", world=path.name, base_model="test/model",
                        base_revision="a" * 40, initial_model="model", server_url="http://vllm")
    if execution_kind != "harbor":
        with pytest.raises(ValueError, match="Harbor-compatible"):
            training_world(spec, tmp_path / "training.yaml")
    else:
        loaded = training_world(spec, tmp_path / "training.yaml")
        assert loaded.tasks[0].source == "json-fixture"
        assert loaded.tasks[0].path == str(task)
