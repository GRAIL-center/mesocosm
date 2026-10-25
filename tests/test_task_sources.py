"""Task-format dispatch tests; these do not establish benchmark or live-agent acceptance."""

from __future__ import annotations

import hashlib
import json
import pathlib

import pytest
import yaml

from mesocosm.adapters.harbor.source import HarborSource
from mesocosm.core.manifest import TaskSpec, WorldManifest, validate_manifest
from mesocosm.core.records import RunBundle
from mesocosm.core.research import research_task_errors
from mesocosm.core.run import TaskQueue, _load_spec_tasks, _require_harbor_execution, _stream_tasks, _task_spec
from mesocosm.integrations import registry
from mesocosm.integrations.tasks import Task, load_task, resolve_task_paths


class JsonSource:
    api_version = 1

    def load(self, path, *, dataset=None, name=None):
        data = json.loads((path / "problem.json").read_text())
        return Task(path=path, name=name or path.name, instruction=data["question"], config=data,
                    has_environment=False, has_tests=False, has_solution=False, dataset=dataset,
                    execution_kind=data.get("execution_kind", "json"))

    def resolve(self, dataset, *, roots, limit=None):
        for root in roots:
            collection = root / dataset
            if collection.is_dir():
                return sorted(p for p in collection.iterdir() if (p / "problem.json").is_file())[:limit]
        raise FileNotFoundError(dataset)

    def research_errors(self, task):
        return ["source declares an authored fixture"] if task.config.get("fixture") else []

    def fingerprint(self, task):
        return {"instruction_sha256": hashlib.sha256(task.instruction.encode()).hexdigest()}


@pytest.fixture
def json_source(monkeypatch):
    original = registry.get_task_source
    provider = JsonSource()

    def get_source(name):
        return provider if name == "json" else original(name)

    monkeypatch.setattr(registry, "get_task_source", get_source)
    return provider


def task_dir(root, name="question", **changes):
    path = root / name
    path.mkdir(parents=True)
    (path / "problem.json").write_text(json.dumps({"question": "Original question", **changes}))
    return path


def world(**changes):
    return {"name": "source-contract", "model": {"id": "base"}, "gateway": {"url": "http://gateway"},
            "agents": [{"id": "agent-1"}], "tasks": [{"source": "json", "path": "question"}], **changes}


def test_load_dispatch_preserves_original_format_and_binds_selected_source(tmp_path, json_source):
    directory = task_dir(tmp_path)
    original = (directory / "problem.json").read_bytes()
    loaded = load_task("json", directory, dataset="questions@1", name="selected")
    assert loaded.source == "json" and loaded.execution_kind == "json"
    assert loaded.name == "selected" and loaded.dataset == "questions@1"
    assert loaded.provenance()["source"] == "json"
    assert (directory / "problem.json").read_bytes() == original
    assert not (directory / "instruction.md").exists()


def test_collection_and_spec_matching_are_source_aware(tmp_path, json_source):
    task_dir(tmp_path / "collection", "b")
    task_dir(tmp_path / "collection", "a")
    tasks = _load_spec_tasks(TaskSpec(source="json", dataset="collection", limit=1), tmp_path / "world.yaml")
    assert [task.name for task in tasks] == ["a"]
    manifest = WorldManifest.model_validate(world(tasks=[
        {"source": "harbor", "dataset": "collection"},
        {"source": "json", "dataset": "collection", "stagger_seconds": 2},
    ]))
    assert _task_spec(manifest, tasks[0]).source == "json"


def test_research_checks_are_owned_by_the_source(tmp_path, json_source):
    directory = task_dir(tmp_path)
    (directory / "agents").mkdir()
    (directory / "agents" / "original.json").write_text("{}")
    loaded = load_task("json", directory)
    assert research_task_errors(loaded) == []
    loaded.config["fixture"] = True
    assert research_task_errors(loaded) == ["source declares an authored fixture"]


def test_harbor_execution_retains_fixture_checks_for_external_sources(tmp_path, json_source):
    directory = task_dir(tmp_path, execution_kind="harbor")
    (directory / "agents").mkdir()
    loaded = load_task("json", directory)
    assert any("fixture input agents" in error for error in research_task_errors(loaded))


@pytest.mark.parametrize("returned", [None, {}, [1]])
def test_invalid_research_contract_is_not_treated_as_no_errors(tmp_path, json_source, monkeypatch, returned):
    loaded = load_task("json", task_dir(tmp_path))
    monkeypatch.setattr(json_source, "research_errors", lambda task: returned)
    with pytest.raises(ValueError, match="list of strings"):
        research_task_errors(loaded)


def test_docker_rejects_an_unrelated_execution_kind_before_running_it(tmp_path, json_source):
    directory = task_dir(tmp_path)
    with pytest.raises(ValueError, match="compatible execution backend"):
        _require_harbor_execution(load_task("json", directory))
    path = tmp_path / "world.yaml"
    path.write_text(yaml.safe_dump(world()))
    report = validate_manifest(path)
    assert not report.ok and any("declares 'json'" in error for error in report.errors)
    assert not any("instruction.md" in error for error in report.errors)


def test_selected_source_cannot_redirect_the_declared_input_root(tmp_path, json_source, monkeypatch):
    directory = task_dir(tmp_path)
    other = task_dir(tmp_path, "other")
    monkeypatch.setattr(json_source, "load", lambda *args, **kwargs: JsonSource().load(other))
    with pytest.raises(ValueError, match="different input root"):
        load_task("json", directory)


@pytest.mark.parametrize("name", ["", ".", "..", "../escape", "/absolute", "a/b", "a\\b", "bad\x00name"])
def test_task_names_cannot_escape_the_run_artifact_root(tmp_path, json_source, monkeypatch, name):
    directory = task_dir(tmp_path)
    loaded = json_source.load(directory)
    loaded.name = name
    monkeypatch.setattr(json_source, "load", lambda *args, **kwargs: loaded)
    with pytest.raises(ValueError, match="unsafe task name"):
        load_task("json", directory)


def test_task_names_allow_external_identifiers_without_platform_specific_naming_rules(tmp_path, json_source):
    loaded = load_task("json", task_dir(tmp_path), name="issue:123 (original)")
    assert loaded.name == "issue:123 (original)"


@pytest.mark.parametrize("shape", ["not-list", "not-path", "duplicate", "over-limit", "empty"])
def test_resolved_collections_require_an_unambiguous_bounded_path_list(tmp_path, json_source, monkeypatch, shape):
    directory = task_dir(tmp_path)
    other = task_dir(tmp_path, "other")
    returned = {"not-list": (directory,), "not-path": [str(directory)], "duplicate": [directory, directory],
                "over-limit": [directory, other], "empty": []}[shape]
    monkeypatch.setattr(json_source, "resolve", lambda *args, **kwargs: returned)
    with pytest.raises(ValueError if shape != "empty" else FileNotFoundError):
        resolve_task_paths("json", "collection", roots=[tmp_path], limit=1 if shape == "over-limit" else None)


async def test_stream_uses_its_selected_source_without_harbor_file_detection(tmp_path, json_source):
    directory = task_dir(tmp_path / "incoming", execution_kind="harbor")
    manifest = WorldManifest.model_validate(world(tasks=[], mode="persistent", stream={
        "dir": "incoming", "source": "json", "max_tasks": 1, "poll_seconds": 0.001,
    }))
    bundle = RunBundle.create(tmp_path / "runs", "stream-test")
    queue = TaskQueue()
    await _stream_tasks(manifest, tmp_path / "world.yaml", bundle, queue, 1)
    loaded = queue.q.get_nowait()
    assert loaded.path == directory and loaded.source == "json"
    assert queue.loaded == 1


def test_harbor_fingerprint_matches_existing_execution_tree_digest():
    from mesocosm.core.evaluation import _path_fingerprint

    directory = pathlib.Path(__file__).resolve().parents[1] / "examples/fixtures/tasks/hello-world"
    source = HarborSource()
    task = source.load(directory)
    files = _path_fingerprint(directory)["files"]
    execution = {name: value for name, value in files.items()
                 if name in ("tests", "environment") or name.startswith(("tests/", "environment/"))}
    expected = hashlib.sha256(json.dumps(execution, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert source.fingerprint(task)["execution_sha256"] == expected
