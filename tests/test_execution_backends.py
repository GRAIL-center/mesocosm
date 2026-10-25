"""Provider dispatch and ownership checks; these are CPU contract tests, not agent acceptance."""

from __future__ import annotations

import json
import uuid

import pytest
import yaml
from typer.testing import CliRunner

from mesocosm.core.manifest import WorldManifest
from mesocosm.execution.backends import BackendContext, DockerProvider
from mesocosm.execution.protocol import JobRecord
from mesocosm.execution.worker import BACKEND_OWNER, Worker
from mesocosm.execution.worker_cli import worker_app

TOKEN = "worker-contract-test-token-" * 2


def world(**patch):
    return WorldManifest.model_validate({"name": "provider-test", "model": {"id": "local"},
        "gateway": {"url": "http://gateway.invalid"}, "agents": [{"id": "agent"}], **patch})


@pytest.mark.parametrize("patch,reason", [
    ({"agents": [{"id": "agent", "harness": "other"}]}, "harness"),
    ({"execution": {"backend": "docker", "options": {"ignored": True}}}, "execution.options"),
    ({"execution": {"backend": "other"}}, "execution.backend"),
])
def test_docker_provider_rejects_features_it_does_not_implement(patch, reason):
    assert any(reason in error for error in DockerProvider().validate(world(**patch)))
    assert DockerProvider().validate(world()) == []


@pytest.mark.parametrize("options", [
    {}, {"repo_root": ".", "unknown": True}, {"repo_root": ".", "vllm_port": True},
    {"repo_root": ".", "allow_unenforced_resources": "false"},
    {"repo_root": ".", "model_key_envs": ["credential=value"]},
])
def test_docker_operator_config_is_explicit_and_typed(tmp_path, options):
    with pytest.raises(ValueError):
        DockerProvider().create(BackendContext(tmp_path, options))


class Backend:
    def __init__(self):
        self.recovered = []

    async def capabilities(self):
        return {"status": "supported", "backend": "contract-test"}

    async def execute(self, *args):
        pytest.fail("these recovery tests must never execute agents")

    async def recover(self, directory):
        self.recovered.append(directory.name)
        return {"status": "complete"}


def test_custom_worker_cli_uses_installed_provider_without_docker_flags(tmp_path, monkeypatch):
    import uvicorn

    from mesocosm.integrations import registry

    options = tmp_path / "operator.yaml"
    options.write_text("endpoint: http://operator.invalid\n")
    seen = []
    identity = {"id": "contract-test", "version": "1"}

    class Provider:
        def create(self, context):
            seen.append(context)
            return Backend()

    monkeypatch.setenv("MESOCOSM_WORKER_TOKEN", TOKEN)
    monkeypatch.setattr(registry, "get_backend_provider", lambda name: Provider())
    monkeypatch.setattr(registry, "backend_identity", lambda name: identity)
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: None)
    result = CliRunner().invoke(worker_app, ["--root", str(tmp_path / "private"), "--backend", "contract-test",
        "--backend-config", str(options), "--json"])
    assert result.exit_code == 0, result.output
    receipt = json.loads(result.stdout)
    assert receipt["backend"] == "contract-test" and receipt["backend_identity"] == identity
    assert seen == [BackendContext((tmp_path / "private").resolve(), {"endpoint": "http://operator.invalid"})]
    assert "operator_policy" not in receipt and TOKEN not in result.stdout


def test_custom_worker_refuses_silently_ignored_docker_flags(tmp_path, monkeypatch):
    monkeypatch.setenv("MESOCOSM_WORKER_TOKEN", TOKEN)
    result = CliRunner().invoke(worker_app, ["--root", str(tmp_path / "private"), "--backend", "custom",
        "--repo-root", str(tmp_path), "--json"])
    assert result.exit_code == 1
    assert "Docker operator flags require" in json.loads(result.stdout)["detail"]


@pytest.mark.parametrize("experiment", [False, True])
def test_worker_rejects_backend_mismatch_in_world_or_late_condition(tmp_path, experiment):
    task = tmp_path / "task"
    task.mkdir()
    (task / "instruction.md").write_text("Original instruction")
    spec = world(tasks=[{"path": "task"}]).model_dump(mode="json")
    if not experiment:
        spec["execution"]["backend"] = "different"
    (tmp_path / "world.yaml").write_text(yaml.safe_dump(spec))
    entrypoint = "world.yaml"
    if experiment:
        entrypoint = "experiment.yaml"
        (tmp_path / entrypoint).write_text(yaml.safe_dump({"name": "compare", "base": "world.yaml",
            "conditions": [{"name": "first"}, {"name": "second", "patch": {"execution": {"backend": "different"}}}]}))
    worker = Worker(tmp_path / "worker", Backend(), lock_file=tmp_path / "host.lock")
    with pytest.raises(ValueError, match="backend mismatch"):
        worker._validate_submission(tmp_path, "experiment" if experiment else "world", entrypoint)


@pytest.mark.parametrize("backend_id,previous,current,recover", [
    ("docker", {"version": "old"}, {"version": "new"}, True),
    ("custom", {"version": "old"}, {"version": "old"}, True),
    ("custom", {"version": "old"}, {"version": "new"}, False),
    ("custom", None, {"version": "new"}, False),
])
async def test_restart_respects_owner_without_loading_tasks(tmp_path, monkeypatch, backend_id, previous, current, recover):
    from mesocosm.integrations import registry

    monkeypatch.setattr(registry, "validate_integrations", lambda *args, **kwargs: pytest.fail("recovery loaded tasks"))
    implementation = Backend()
    worker = Worker(tmp_path / "private", implementation, lock_file=tmp_path / "host.lock",
                    backend_id=backend_id, backend_identity=current)
    worker.root.mkdir(mode=0o700)
    job = uuid.uuid4().hex
    directory = worker.directory(job)
    directory.mkdir(parents=True)
    record = JobRecord(job_id=job, kind="world", digest="a" * 64, entrypoint="missing.yaml",
                       created=1, updated=1, status="running")
    (directory / "job.json").write_text(record.model_dump_json())
    if previous is not None:
        (directory / BACKEND_OWNER).write_text(json.dumps({"backend": backend_id, "identity": previous}))
    await worker.start()
    try:
        assert implementation.recovered == ([job] if recover else [])
        assert worker.record(job).status == ("interrupted" if recover else "failed_cleanup")
        assert bool(worker.blocked) is not recover
    finally:
        await worker.close()


def test_instantiated_worker_cannot_accept_a_new_backend_identity(tmp_path, monkeypatch):
    from mesocosm.integrations import registry

    (tmp_path / "world.yaml").write_text(yaml.safe_dump(world().model_dump(mode="json")))
    old, new = {"code_sha256": "a" * 64}, {"code_sha256": "b" * 64}
    monkeypatch.setattr(registry, "backend_identity", lambda backend: new)
    worker = Worker(tmp_path / "worker", Backend(), backend_identity=old)
    with pytest.raises(ValueError, match="worker backend identity changed"):
        worker._validate_submission(tmp_path, "world", "world.yaml")


def test_explicit_default_flags_override_operator_config(tmp_path, monkeypatch):
    import uvicorn

    from mesocosm.execution.docker import DockerBackend

    options = tmp_path / "operator.yaml"
    options.write_text(yaml.safe_dump({"repo_root": str(tmp_path), "verifier_network": "bridge",
                                       "allow_unenforced_resources": True}))
    monkeypatch.setenv("MESOCOSM_WORKER_TOKEN", TOKEN)
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: None)

    async def capabilities(self):
        return {"status": "supported"}

    monkeypatch.setattr(DockerBackend, "capabilities", capabilities)
    result = CliRunner().invoke(worker_app, ["--root", str(tmp_path / "private"), "--backend-config", str(options),
        "--verifier-network", "none", "--no-allow-unenforced-resources", "--json"])
    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout)["operator_policy"] == {
        "verifier_network": "none", "allow_unenforced_resources": False}
