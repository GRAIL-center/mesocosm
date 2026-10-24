"""Managed-worker contracts at mocked Docker boundaries, not real-agent acceptance."""

from __future__ import annotations

import asyncio
import json
import pathlib

import pytest
import yaml

from mesocosm.core.manifest import load_manifest
from mesocosm.core.records import RunBundle
from mesocosm.core.run import RunResult
from mesocosm.env import stack as stack_module
from mesocosm.execution import docker, local

REPO = pathlib.Path(__file__).resolve().parents[1]


def package(tmp_path, *, patch=None, experiment=False):
    job = tmp_path / "job"
    inputs = job / "input"
    inputs.mkdir(parents=True)
    task = inputs / "task"
    (task / "environment").mkdir(parents=True)
    (task / "tests").mkdir()
    (task / "instruction.md").write_text("Perform the original task.")
    (task / "task.toml").write_text('[metadata]\nname = "original"\n')
    (task / "environment/Dockerfile").write_text("FROM debian:bookworm\n")
    (task / "tests/test.sh").write_text("#!/bin/sh\nprintf 1 > /logs/verifier/reward.txt\n")
    world = {"name": "submitted", "model": {"id": "test-model", "hf_repo": "test/model"},
             "gateway": {"url": "http://client-only:8080", "api_key": "client-secret",
                         "upstream_key": "must-not-forward"},
             "agents": [{"id": f"agent-{i}", "url": "http://client-only:4096"} for i in range(1, 4)],
             "tasks": [{"path": "task"}], "services": {"enabled": False}}
    world.update(patch or {})
    (inputs / "world.yaml").write_text(yaml.safe_dump(world))
    entrypoint = "world.yaml"
    if experiment:
        entrypoint = "experiment.yaml"
        (inputs / entrypoint).write_text(yaml.safe_dump({"name": "conditions", "base": "world.yaml",
            "order": "listed", "conditions": [{"name": "base"},
                {"name": "isolated", "patch": {"isolate_agents": True}}]}))
    return job, inputs, entrypoint


@pytest.fixture
def boundaries(monkeypatch):
    calls = []
    state = {"up_error": None, "down_error": None, "run_error": None, "release": None,
             "entered": None, "cleanup_entered": None, "cleanup_release": None}

    class Stack:
        def __init__(self, compose_file):
            self.compose_file = str(compose_file)
            self.cleanup_evidence = None

        async def up(self):
            calls.append(("up", self.compose_file))
            if state["up_error"]:
                raise state["up_error"]

        async def down(self):
            calls.append(("down", self.compose_file))
            if state["cleanup_entered"]:
                state["cleanup_entered"].set()
                await state["cleanup_release"].wait()
            if state["down_error"]:
                raise state["down_error"]
            self.cleanup_evidence = {"containers_removed": True}

        async def _docker(self, *args, **kwargs):
            calls.append(("docker", args))
            return ""

    async def capability(self):
        return {"status": "supported"}

    async def run(manifest, compose_file, runs_root, workspace, *args, **kwargs):
        spec = load_manifest(manifest)
        calls.append(("run", spec))
        if state["entered"]:
            state["entered"].set()
            await state["release"].wait()
        if state["run_error"]:
            raise state["run_error"]
        bundle = RunBundle.create(runs_root)
        bundle.record("run.start")
        bundle.write_summary({"status": "ok", "task_outcomes": [], "learning_rewards": [],
                              "model_calls": 0, "flags": {}, "agent_seconds": {}, "events": 1})
        return RunResult(bundle.run_id, bundle.root, "ok", {})

    monkeypatch.setattr(stack_module, "DockerStack", Stack)
    monkeypatch.setattr(docker, "DockerStack", Stack)
    monkeypatch.setattr(docker.DockerBackend, "capabilities", capability)
    monkeypatch.setattr(local, "run_manifest", run)
    return calls, state


async def test_world_binds_worker_endpoints_and_owns_lifecycle_without_mutating_inputs(tmp_path, boundaries):
    calls, _ = boundaries
    job, inputs, entrypoint = package(tmp_path)
    original = (inputs / entrypoint).read_bytes()
    result = await docker.DockerBackend(REPO).execute(job, inputs, "world", entrypoint)
    assert result["status"] == "ok", result
    assert result["cleanup"]["status"] == "complete"
    assert [kind for kind, _ in calls if kind != "docker"] == ["up", "run", "down"]
    spec = next(value for kind, value in calls if kind == "run")
    assert spec.gateway.api_key != "client-secret" and len(spec.gateway.api_key) >= 32
    assert spec.gateway.upstream_key is None
    assert spec.gateway.url == "http://172.30.0.2:8080"
    assert [agent.url for agent in spec.agents] == [f"http://172.30.0.{i}:4096" for i in (10, 11, 12)]
    assert (inputs / entrypoint).read_bytes() == original
    compose = yaml.safe_load(next((job / "compose").glob("*.yml")).read_text())
    assert compose["services"]["gateway"]["build"]["context"] == str(REPO)
    inventory = json.loads((job / docker.INVENTORY).read_text())
    assert inventory["worlds"][0]["project"] == compose["name"]
    assert inventory["worlds"][0]["status"] == "cleaned"
    assert pathlib.Path(result["bundle"]).is_relative_to(job / "runs")


async def test_experiment_uses_each_condition_and_cleans_each_world(tmp_path, boundaries):
    calls, _ = boundaries
    job, inputs, entrypoint = package(tmp_path, experiment=True)
    result = await docker.DockerBackend(REPO).execute(job, inputs, "experiment", entrypoint)
    assert result["status"] == "ok", result
    specs = [value for kind, value in calls if kind == "run"]
    assert len(specs) == 2
    assert specs[0].gateway.url == "http://172.30.0.2:8080"
    assert specs[1].gateway.url == "http://172.30.1.2:8080"
    assert specs[1].agents[2].url == "http://172.30.3.12:4096"
    assert specs[0].name != specs[1].name
    assert [kind for kind, _ in calls if kind != "docker"] == ["up", "run", "down", "up", "run", "down"]
    assert (job / "experiment/experiment.json").is_file()


@pytest.mark.parametrize("patch", [{"profile": "fixture"}, {"containment": "adapted"},
                                   {"agents": [{"id": "one"}]}, {"mode": "persistent"}])
async def test_unsupported_world_rejected_before_docker(tmp_path, boundaries, patch):
    calls, _ = boundaries
    job, inputs, entrypoint = package(tmp_path, patch=patch)
    result = await docker.DockerBackend(REPO).execute(job, inputs, "world", entrypoint)
    assert result["status"] == "failed"
    assert "rejected_unsupported" in result["error"]
    assert calls == []


async def test_late_unsupported_condition_rejected_before_any_trial(tmp_path, boundaries):
    calls, _ = boundaries
    job, inputs, entrypoint = package(tmp_path, experiment=True)
    data = yaml.safe_load((inputs / entrypoint).read_text())
    data["conditions"][1]["patch"]["containment"] = "adapted"
    (inputs / entrypoint).write_text(yaml.safe_dump(data))
    result = await docker.DockerBackend(REPO).execute(job, inputs, "experiment", entrypoint)
    assert result["status"] == "failed" and calls == []


@pytest.mark.parametrize("allowed,present", [(False, True), (True, False), (True, True)])
async def test_model_credentials_only_come_from_worker_allowlist(tmp_path, boundaries, monkeypatch, allowed, present):
    calls, _ = boundaries
    job, inputs, entrypoint = package(tmp_path, patch={"model": {
        "id": "remote", "upstream": "https://example.invalid", "api_key_env": "TEST_PROVIDER_KEY"}})
    if present:
        monkeypatch.setenv("TEST_PROVIDER_KEY", "secret-value")
    else:
        monkeypatch.delenv("TEST_PROVIDER_KEY", raising=False)
    backend = docker.DockerBackend(REPO, model_key_envs=("TEST_PROVIDER_KEY",) if allowed else ())
    result = await backend.execute(job, inputs, "world", entrypoint)
    assert result["status"] == ("ok" if allowed and present else "failed")
    assert bool(calls) is (allowed and present)
    assert "secret-value" not in json.dumps(result)


@pytest.mark.parametrize("phase", ["up_error", "run_error", "down_error"])
async def test_failures_always_attempt_teardown_and_cleanup_failure_has_priority(tmp_path, boundaries, phase):
    calls, state = boundaries
    state[phase] = RuntimeError("boundary failed")
    job, inputs, entrypoint = package(tmp_path)
    result = await docker.DockerBackend(REPO).execute(job, inputs, "world", entrypoint)
    assert result["status"] == ("failed_cleanup" if phase == "down_error" else "failed")
    assert len([value for kind, value in calls if kind == "down"]) == 1


async def test_repeated_cancellation_waits_for_cleanup_then_returns_cancelled(tmp_path, boundaries):
    calls, state = boundaries
    state.update(entered=asyncio.Event(), release=asyncio.Event(), cleanup_entered=asyncio.Event(),
                 cleanup_release=asyncio.Event())
    job, inputs, entrypoint = package(tmp_path)
    task = asyncio.create_task(docker.DockerBackend(REPO).execute(job, inputs, "world", entrypoint))
    await asyncio.wait_for(state["entered"].wait(), 2)
    task.cancel()
    await asyncio.wait_for(state["cleanup_entered"].wait(), 2)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    state["cleanup_release"].set()
    result = await asyncio.wait_for(task, 2)
    assert result["status"] == "cancelled" and result["cleanup"]["status"] == "complete"
    assert len([value for kind, value in calls if kind == "down"]) == 1


async def test_cleanup_failure_keeps_completed_trial_but_prevents_the_next_world(tmp_path, boundaries):
    calls, state = boundaries
    state["down_error"] = RuntimeError("owned volume remains")
    job, inputs, entrypoint = package(tmp_path, experiment=True)
    result = await docker.DockerBackend(REPO).execute(job, inputs, "experiment", entrypoint)
    assert result["status"] == "failed_cleanup"
    assert result["experiment"]["ok"] == 1 and result["experiment"]["not_run"] == 1
    assert len(result["bundles"]) == 1
    assert len([value for kind, value in calls if kind == "up"]) == 1
    saved = json.loads((job / "experiment/experiment.json").read_text())
    assert saved["status"] == "failed_cleanup" and saved["trials"][0]["status"] == "ok"


async def test_existing_job_cannot_be_reexecuted_and_recovery_never_runs_agents(tmp_path, boundaries):
    calls, _ = boundaries
    job, inputs, entrypoint = package(tmp_path)
    backend = docker.DockerBackend(REPO)
    await backend.execute(job, inputs, "world", entrypoint)
    calls.clear()
    with pytest.raises(FileExistsError, match="recover"):
        await backend.execute(job, inputs, "world", entrypoint)
    assert (await backend.recover(job))["status"] == "complete"
    assert [kind for kind, _ in calls if kind != "docker"] == ["down"]


async def test_experiment_recovery_matches_generated_trial_ownership(tmp_path, boundaries):
    calls, _ = boundaries
    job, inputs, entrypoint = package(tmp_path, experiment=True)
    backend = docker.DockerBackend(REPO)
    await backend.execute(job, inputs, "experiment", entrypoint)
    calls.clear()
    recovered = await backend.recover(job)
    assert recovered["status"] == "complete" and len(recovered["worlds"]) == 2
    assert [kind for kind, _ in calls if kind != "docker"] == ["down", "down"]


async def test_recovery_refuses_a_different_project(tmp_path, boundaries):
    calls, _ = boundaries
    job, inputs, entrypoint = package(tmp_path)
    backend = docker.DockerBackend(REPO)
    await backend.execute(job, inputs, "world", entrypoint)
    calls.clear()
    compose = next((job / "compose").glob("*.yml"))
    data = yaml.safe_load(compose.read_text())
    data["name"] = "unrelated-project"
    compose.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError, match="ownership"):
        await backend.recover(job)
    assert calls == []


async def test_recovery_removes_only_inspected_owned_full_container_ids(tmp_path):
    container = "a" * 64
    calls = []
    state = {"present": True, "project": "owned"}

    class Stack:
        async def _docker(self, *args, **kwargs):
            calls.append(args)
            if args[:2] == ("container", "ls"):
                return container if state["present"] else ""
            if args[:2] == ("container", "inspect"):
                return json.dumps([{"Id": container, "Config": {"Labels": {
                    "com.docker.compose.project": state["project"]}}}])
            if args[:2] == ("container", "rm"):
                state["present"] = False
            return ""

    await docker._remove_owned_containers(Stack(), "owned")
    assert ("container", "rm", "-f", "-v", container) in calls
    state.update(present=True, project="different")
    calls.clear()
    with pytest.raises(ValueError, match="ownership"):
        await docker._remove_owned_containers(Stack(), "owned")
    assert not any(call[:2] == ("container", "rm") for call in calls)


@pytest.mark.parametrize("foreign", [None, "volume", "network"])
async def test_recovery_verifies_named_volumes_and_networks_before_removing_them(foreign):
    network = "b" * 64
    present = {"volume": "owned_data", "network": network}
    calls = []

    class Stack:
        async def _docker(self, *args, **kwargs):
            calls.append(args)
            kind = args[0]
            if args[1] == "ls":
                return present.get(kind, "")
            if args[1] == "inspect":
                item = {"Name": "owned_data" if kind == "volume" else "owned_sandbox",
                        "Id": network, "Labels": {
                            "com.docker.compose.project": "other" if kind == foreign else "owned"}}
                return json.dumps([item])
            if args[1] == "rm":
                present.pop(kind)
            return ""

    if foreign:
        with pytest.raises(ValueError, match="ownership"):
            await docker._remove_owned_resources(Stack(), "owned")
        assert not any(call[:2] == (foreign, "rm") for call in calls)
    else:
        evidence = await docker._remove_owned_resources(Stack(), "owned")
        assert evidence["removed_volumes"] == ["owned_data"]
        assert evidence["removed_networks"] == [network]
        assert evidence["remaining_volumes"] == evidence["remaining_networks"] == []


async def test_compose_failure_remains_failure_after_orphan_resource_cleanup():
    calls = []

    class Stack:
        cleanup_evidence = None

        async def _docker(self, *args, **kwargs):
            calls.append(args)
            return ""

        async def down(self):
            raise RuntimeError("original Compose error")

    stack = Stack()
    with pytest.raises(RuntimeError, match="original Compose error"):
        await docker._complete_cleanup(stack, "owned")
    assert any(call[:2] == ("volume", "ls") for call in calls)
    assert any(call[:2] == ("network", "ls") for call in calls)
    assert stack.cleanup_evidence["owned_resource_cleanup"]["remaining_volumes"] == []
