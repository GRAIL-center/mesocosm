"""Research rejects authored mechanisms; fixture runs keep their regression coverage."""

from __future__ import annotations

import pathlib
import shutil

import pytest
import yaml

from mesocosm.adapters.harbor.task import load_task
from mesocosm.core.manifest import WorldManifest, load_manifest, validate_manifest
from mesocosm.core.research import ResearchProfileError, validate_research_tasks
from mesocosm.core.scenario import Scenario, stage_manifests

ROOT = pathlib.Path(__file__).resolve().parents[1]


def world(**changes):
    return {"name": "research", "model": {"id": "base"}, "gateway": {"url": "http://gateway"},
            "agents": [{"id": "agent-1"}], "tasks": [{"path": "task"}], **changes}


@pytest.mark.parametrize("changes, control", [
    ({"services": {"clock": {"enabled": True}}}, "services.clock"),
    ({"services": {"ledger": {"enabled": True}}}, "services.ledger"),
    ({"services": {"honeypot": {"enabled": True}}}, "services.honeypot"),
    ({"services": {"seed": "past"}}, "services.seed"),
    ({"services": {"cache": {"enabled": True, "seed": {"key": "planted"}}}}, "services.cache.seed"),
    ({"services": {"accounts": [{"user": "decoy", "password": "test", "trap": True}]}}, "accounts"),
    ({"services": {"peer_review": {"enabled": True}}}, "peer_review.protocol: legacy"),
    ({"tasks": [{"path": "task", "side_task": []}]}, "tasks[].side_task"),
    ({"judge": {"monitor": {"side_task_aware": True}}}, "side_task_aware"),
])
def test_research_default_rejects_fixture_controls_but_explicit_fixture_preserves_them(changes, control):
    with pytest.raises(ValueError) as exc:
        WorldManifest.model_validate(world(**changes))
    assert "profile: research excludes" in str(exc.value) and control in str(exc.value)
    fixture = WorldManifest.model_validate(world(profile="fixture", **changes))
    assert fixture.profile == "fixture"


def test_functional_services_and_original_environment_inputs_stay_available(tmp_path):
    task = tmp_path / "task"
    (task / "environment" / "database").mkdir(parents=True)
    (task / "instruction.md").write_text("Repair the original service.")
    source = task / "environment" / "app.py"
    database = task / "environment" / "database" / "init.sql"
    source.write_text("print('original source')\n")
    database.write_text("CREATE TABLE task_results (id INTEGER);\n")
    data = world(services={"board": True, "git": ["project"], "chat": {"enabled": True},
                          "cache": {"enabled": True}, "wiki": {"docs": "docs"},
                          "accounts": [{"user": "writer", "password": "test", "roles": ["push"]}]})
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "api.md").write_text("Original API documentation.")
    path = tmp_path / "world.yaml"
    path.write_text(yaml.safe_dump(data))
    manifest = load_manifest(path)
    assert manifest.profile == "research"
    assert validate_manifest(path).ok
    before = (source.read_bytes(), database.read_bytes())
    validate_research_tasks(manifest, [load_task(task)])
    assert before == (source.read_bytes(), database.read_bytes())


@pytest.mark.parametrize("relative", ["side_task.md", "tests/side_test.sh", "agents/a/task.txt",
                                      "instances/a/task.txt", "protected/decoy.txt"])
def test_task_extensions_are_checked_on_local_and_resolved_dataset_paths(tmp_path, relative):
    task = tmp_path / "task"
    task.mkdir()
    (task / "instruction.md").write_text("Original task")
    extra = task / relative
    extra.parent.mkdir(parents=True, exist_ok=True)
    extra.write_text("Authored fixture")
    path = tmp_path / "world.yaml"
    path.write_text(yaml.safe_dump(world()))
    assert not validate_manifest(path).ok
    dataset_manifest = WorldManifest.model_validate(world(tasks=[{"dataset": "external@1/task"}]))
    with pytest.raises(ResearchProfileError, match="fixture input"):
        validate_research_tasks(dataset_manifest, [load_task(task, dataset="external@1/task")])
    fixture = WorldManifest.model_validate(world(profile="fixture"))
    validate_research_tasks(fixture, [load_task(task)])


@pytest.mark.parametrize("config", ['[[rounds]]\nprompt="Relay this answer."\n',
                                     '[metadata]\nfixture=true\n'])
def test_authored_round_prompts_and_declared_fixtures_are_refused(tmp_path, config):
    (tmp_path / "instruction.md").write_text("Original task")
    (tmp_path / "task.toml").write_text(config)
    with pytest.raises(ResearchProfileError, match="fixture input"):
        validate_research_tasks(WorldManifest.model_validate(world()), [load_task(tmp_path)])


def test_runtime_rechecks_mutated_research_manifest():
    manifest = WorldManifest.model_validate(world())
    manifest.services.clock.enabled = True
    with pytest.raises(ResearchProfileError, match="services.clock"):
        validate_research_tasks(manifest, [])


def test_archived_worlds_and_tasks_are_explicit_fixtures_even_after_relocation(tmp_path):
    for path in sorted((ROOT / "examples/fixtures").glob("*.yaml")):
        assert load_manifest(path).profile == "fixture", path
        report = validate_manifest(path)
        assert report.ok, (path, report.errors)
    for path in sorted((ROOT / "examples").glob("*.yaml")):
        assert load_manifest(path).profile == "research", path
    task = tmp_path / "renamed-original"
    shutil.copytree(ROOT / "examples/fixtures/tasks/hello-world", task)
    with pytest.raises(ResearchProfileError, match="metadata.fixture"):
        validate_research_tasks(WorldManifest.model_validate(world()), [load_task(task)])


def test_legacy_scenario_cannot_run_a_research_world_or_switch_a_stage_to_research(tmp_path):
    base = tmp_path / "base.yaml"
    base.write_text(yaml.safe_dump(world()))
    scenario = Scenario(name="legacy", base="base.yaml", stages=[{"name": "first"}])
    with pytest.raises(ValueError, match="require a base world with profile: fixture"):
        stage_manifests(scenario, tmp_path / "scenario.yaml", tmp_path / "out")
    assert not (tmp_path / "out").exists()
    base.write_text(yaml.safe_dump(world(profile="fixture")))
    for patch in ({"profile": "research"}, {"profile": None}):
        scenario.stages[0].patch = patch
        with pytest.raises(ValueError, match="retain profile: fixture"):
            stage_manifests(scenario, tmp_path / "scenario.yaml", tmp_path / "out")
    assert not (tmp_path / "out").exists()
