"""Worker ownership and HTTP contract tests, not live Docker/agent acceptance."""

from __future__ import annotations

import asyncio
import json
import socket
import uuid

import httpx
import pytest
import yaml
from fastapi import HTTPException
from typer.testing import CliRunner

from mesocosm.execution.package import extract_results, pack_submission
from mesocosm.execution.protocol import JobRecord
from mesocosm.execution.worker import Worker, create_app
from mesocosm.execution.worker_cli import worker_app

TOKEN = "test-worker-key-" * 3


class Backend:
    def __init__(self):
        self.started = []
        self.recovered = []
        self.release = asyncio.Event()
        self.recover_release = asyncio.Event()
        self.recover_release.set()
        self.fail = False
        self.cleanup_fail = False

    async def capabilities(self):
        return {"status": "supported", "backend": "test-only"}

    async def execute(self, job_dir, input_dir, kind, entrypoint):
        self.started.append(job_dir.name)
        assert (input_dir / entrypoint).is_file()
        await self.release.wait()
        if self.fail:
            raise RuntimeError("private secret must not enter API")
        bundle = job_dir / "runs/run_original"
        bundle.mkdir(parents=True)
        (bundle / "manifest.json").write_text('{"original":true}')
        (bundle / "events.jsonl").write_text('{"kind":"original.event"}\n')
        return {"status": "ok", "cleanup": {"status": "complete"}}

    async def recover(self, job_dir):
        self.recovered.append(job_dir.name)
        await self.recover_release.wait()
        if self.cleanup_fail:
            raise RuntimeError("private cleanup command")
        return {"status": "complete"}


@pytest.fixture
def archive(tmp_path):
    task = tmp_path / "task"
    (task / "environment").mkdir(parents=True)
    (task / "environment/Dockerfile").write_text("FROM debian:bookworm\n")
    (task / "instruction.md").write_text("External task content")
    manifest = tmp_path / "world.yaml"
    manifest.write_text(yaml.safe_dump({"name": "worker-test", "model": {"id": "test"},
                                       "gateway": {"url": "http://gateway.invalid"},
                                       "agents": [{"id": f"a{i}"} for i in range(3)],
                                       "tasks": [{"path": "task"}]}))
    destination = tmp_path / "submission.zip"
    pack_submission(manifest, destination)
    return destination


def make_worker(tmp_path, backend=None):
    return Worker(tmp_path / "private", backend or Backend(), lock_file=tmp_path / "host.lock")


async def until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.005)


async def test_http_authentication_idempotency_disconnect_and_original_results(tmp_path, archive):
    backend = Backend()
    worker = make_worker(tmp_path, backend)
    app = create_app(worker, TOKEN)
    job = uuid.uuid4().hex
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            for method, path in [("GET", "/v1/capabilities"), ("POST", "/v1/jobs"),
                                 ("GET", f"/v1/jobs/{job}/results"), ("POST", f"/v1/jobs/{job}/stop")]:
                assert (await client.request(method, path)).status_code == 401
            client.headers["Authorization"] = f"Bearer {TOKEN}"
            response = await client.post("/v1/jobs", content=archive.read_bytes(), headers={"Idempotency-Key": job})
            assert response.status_code == 202
            duplicate = await client.post("/v1/jobs", content=archive.read_bytes(), headers={"Idempotency-Key": job})
            assert duplicate.json()["job_id"] == job
            await until(lambda: backend.started)
            assert (await client.get(f"/v1/jobs/{job}/results")).status_code == 409
        # Accepted execution is independent of the submitting connection.
        assert not worker.tasks[job].done()
        backend.release.set()
        await worker.tasks[job]
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                    headers={"Authorization": f"Bearer {TOKEN}"}) as client:
            status = (await client.get(f"/v1/jobs/{job}")).json()
            assert status["status"] == "completed" and len(backend.started) == 1
            result = await client.get(f"/v1/jobs/{job}/results")
            assert result.status_code == 200
            path = tmp_path / "results.zip"
            path.write_bytes(result.content)
            extract_results(path, tmp_path / "downloaded")
            assert (tmp_path / "downloaded/runs/run_original/events.jsonl").read_bytes() == b'{"kind":"original.event"}\n'
            assert not (tmp_path / "downloaded/input").exists()


async def test_submission_identity_cannot_change_inputs(tmp_path, archive):
    worker = make_worker(tmp_path)
    await worker.start()
    try:
        job = uuid.uuid4().hex
        await worker.submit(archive, job)
        manifest = tmp_path / "world.yaml"
        data = yaml.safe_load(manifest.read_text())
        data["name"] = "different"
        manifest.write_text(yaml.safe_dump(data))
        different = tmp_path / "different.zip"
        pack_submission(manifest, different)
        with pytest.raises(HTTPException) as exc:
            await worker.submit(different, job)
        assert exc.value.status_code == 409
    finally:
        await worker.close()


async def test_queued_cancellation_never_recovers_active_world(tmp_path, archive):
    worker = make_worker(tmp_path)
    await worker.start()
    try:
        first, second = uuid.uuid4().hex, uuid.uuid4().hex
        await worker.submit(archive, first)
        await until(lambda: worker.backend.started)
        await worker.submit(archive, second)
        worker.stop(second)
        await asyncio.sleep(0)
        assert worker.record(second).status == "cancelled"
        assert worker.backend.recovered == []
        assert json.loads((worker.directory(second) / "result.json").read_text())["cleanup"]["started"] is False
        worker.backend.release.set()
        await worker.tasks[first]
        assert worker.backend.started == [first]
    finally:
        await worker.close()


async def test_exception_cleanup_holds_scheduling_lease_and_hides_secret(tmp_path, archive):
    backend = Backend()
    backend.fail = True
    backend.recover_release.clear()
    worker = make_worker(tmp_path, backend)
    await worker.start()
    try:
        first, second = uuid.uuid4().hex, uuid.uuid4().hex
        await worker.submit(archive, first)
        await until(lambda: backend.started)
        await worker.submit(archive, second)
        backend.release.set()
        await until(lambda: backend.recovered)
        assert backend.started == [first]
        worker.tasks[first].cancel()  # A second cancellation cannot abandon cleanup.
        await asyncio.sleep(0)
        assert not worker.tasks[first].done()
        backend.fail = False
        backend.recover_release.set()
        await asyncio.gather(*worker.tasks.values())
        assert worker.record(first).status == "failed"
        assert worker.record(second).status == "completed"
        assert "private secret" not in json.dumps(worker.snapshot(first))
    finally:
        backend.recover_release.set()
        await worker.close()


async def test_running_stop_waits_for_cleanup_and_keeps_receipt(tmp_path, archive):
    backend = Backend()
    backend.recover_release.clear()
    worker = make_worker(tmp_path, backend)
    await worker.start()
    try:
        job = uuid.uuid4().hex
        await worker.submit(archive, job)
        await until(lambda: backend.started)
        assert worker.stop(job).status == "cancelling"
        await until(lambda: backend.recovered)
        assert worker.stop(job).status == "cancelling"
        backend.recover_release.set()
        await worker.tasks[job]
        assert worker.record(job).status == "cancelled"
        assert json.loads((worker.directory(job) / "result.json").read_text())["cleanup"]["status"] == "complete"
    finally:
        backend.recover_release.set()
        await worker.close()


async def test_failed_cleanup_blocks_queued_and_new_jobs(tmp_path, archive):
    backend = Backend()
    backend.fail = backend.cleanup_fail = True
    worker = make_worker(tmp_path, backend)
    await worker.start()
    try:
        first, second = uuid.uuid4().hex, uuid.uuid4().hex
        await worker.submit(archive, first)
        await until(lambda: backend.started)
        await worker.submit(archive, second)
        backend.release.set()
        await asyncio.gather(*worker.tasks.values())
        assert worker.record(first).status == "failed_cleanup"
        assert worker.record(second).status == "cancelled"
        assert backend.started == [first]
        assert (await worker.capabilities())["admission"] == "closed"
        with pytest.raises(HTTPException) as exc:
            await worker.submit(archive, uuid.uuid4().hex)
        assert exc.value.status_code == 503
    finally:
        await worker.close()


@pytest.mark.parametrize("prior", ["queued", "running", "cancelling", "failed_cleanup"])
async def test_restart_recovers_without_reexecution(tmp_path, prior):
    backend = Backend()
    worker = make_worker(tmp_path, backend)
    worker.root.mkdir(mode=0o700)
    job = uuid.uuid4().hex
    directory = worker.directory(job)
    directory.mkdir(parents=True)
    record = JobRecord(job_id=job, kind="world", digest="a" * 64, entrypoint="world.yaml",
                       created=1, updated=1, status=prior)
    (directory / "job.json").write_text(record.model_dump_json())
    (directory / "result.json").write_text('{"original":true}')
    (directory / "results.zip").write_bytes(b"stale")
    await worker.start()
    try:
        assert worker.record(job).status == "interrupted"
        assert backend.recovered == [job] and backend.started == []
        result = json.loads((directory / "result.json").read_text())
        assert result["previous_result"] == {"original": True}
        assert not (directory / "results.zip").exists()
    finally:
        await worker.close()


async def test_host_lease_prevents_two_worker_roots(tmp_path):
    first = make_worker(tmp_path)
    second = Worker(tmp_path / "other", Backend(), lock_file=tmp_path / "host.lock")
    await first.start()
    try:
        with pytest.raises(BlockingIOError):
            await second.start()
    finally:
        await first.close()
    await second.start()
    await second.close()


async def test_public_root_refused(tmp_path):
    worker = make_worker(tmp_path)
    worker.root.mkdir(mode=0o755)
    with pytest.raises(ValueError, match="private"):
        await worker.start()


async def test_event_cursors_and_partial_write(tmp_path, archive):
    worker = make_worker(tmp_path)
    await worker.start()
    try:
        job = uuid.uuid4().hex
        await worker.submit(archive, job)
        worker.stop(job)
        page = worker.events(job, None, 0, 1)
        assert len(page["events"]) == 1 and not page["eof"]
        tail = worker.events(job, None, page["next_offset"], 20)
        assert tail["events"][-1]["status"] == "cancelled"
        path = worker.directory(job) / "events.jsonl"
        with path.open("ab") as stream:
            stream.write(b'{"partial":')
        partial = worker.events(job, None, tail["next_offset"], 20)
        assert partial == {"events": [], "next_offset": tail["next_offset"], "eof": False}
        with pytest.raises(HTTPException):
            worker.events(job, "../input", 0, 20)
        with pytest.raises(HTTPException):
            worker.events(job, None, -1, 20)
    finally:
        await worker.close()


async def test_admission_limits_and_invalid_upload_leave_no_jobs(tmp_path, archive):
    worker = make_worker(tmp_path)
    worker.max_pending = 1
    app = create_app(worker, TOKEN)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                    headers={"Authorization": f"Bearer {TOKEN}"}) as client:
            bad = await client.post("/v1/jobs", content=b"broken", headers={"Idempotency-Key": uuid.uuid4().hex})
            assert bad.status_code == 422 and not worker.records
            await worker.submit(archive, uuid.uuid4().hex)
            with pytest.raises(HTTPException) as exc:
                await worker.submit(archive, uuid.uuid4().hex)
            assert exc.value.status_code == 429
            assert not list(worker.root.glob(".submission-*"))
            assert not list(worker.root.glob(".upload-*"))


async def test_http_transport_submit_reconnect_and_fetch(tmp_path, archive):
    """Real local HTTP transport; the injected backend supplies no agent/Docker evidence."""
    import uvicorn

    from mesocosm.execution.client import ExecutionClient

    backend = Backend()
    worker = make_worker(tmp_path, backend)
    server = uvicorn.Server(uvicorn.Config(create_app(worker, TOKEN), log_level="error", access_log=False))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        url = f"http://127.0.0.1:{listener.getsockname()[1]}"
        serving = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            await until(lambda: server.started)
            job = uuid.uuid4().hex
            async with ExecutionClient(url, TOKEN) as client:
                assert (await client.submit(archive, job))["job_id"] == job
                await until(lambda: backend.started)
            backend.release.set()
            await worker.tasks[job]
            async with ExecutionClient(url, TOKEN) as client:
                assert (await client.status(job))["status"] == "completed"
                assert (await client.events(job))["eof"]
                result = await client.fetch(job, tmp_path / "collected")
                assert result["sensitive"] and result["job_id"] == job
        finally:
            server.should_exit = True
            await serving


def test_worker_cli_private_token_and_prerequisite_errors_emit_one_json(tmp_path, monkeypatch):
    monkeypatch.delenv("MESOCOSM_WORKER_TOKEN", raising=False)
    args = ["--root", str(tmp_path / "private"), "--repo-root", str(tmp_path), "--json"]
    result = CliRunner().invoke(worker_app, args)
    assert result.exit_code == 1
    assert json.loads(result.stdout)["status"] == "error"
    monkeypatch.setenv("MESOCOSM_WORKER_TOKEN", TOKEN)
    from mesocosm.execution.docker import DockerBackend

    async def unavailable(self):
        return {"status": "unavailable", "reason": "docker_missing"}
    monkeypatch.setattr(DockerBackend, "capabilities", unavailable)
    result = CliRunner().invoke(worker_app, args)
    assert result.exit_code == 1
    assert json.loads(result.stdout)["detail"].endswith("docker_missing")
    assert TOKEN not in result.stdout


@pytest.mark.parametrize("explicit", [False, True])
def test_worker_cli_forwards_operator_policy_without_changing_defaults(tmp_path, monkeypatch, explicit):
    import uvicorn

    from mesocosm.execution.docker import DockerBackend

    monkeypatch.setenv("MESOCOSM_WORKER_TOKEN", TOKEN)
    observed = []

    async def capability(self):
        observed.append(self.operator_policy)
        return {"status": "supported"}

    monkeypatch.setattr(DockerBackend, "capabilities", capability)
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: None)
    args = ["--root", str(tmp_path / "private"), "--repo-root", str(tmp_path), "--json"]
    if explicit:
        args.extend(["--verifier-network", "bridge", "--allow-unenforced-resources"])
    result = CliRunner().invoke(worker_app, args)
    assert result.exit_code == 0, result.output
    expected = {"verifier_network": "bridge" if explicit else "none", "allow_unenforced_resources": explicit}
    assert observed == [expected]
    assert json.loads(result.stdout)["operator_policy"] == expected
    assert TOKEN not in result.stdout


@pytest.mark.parametrize("network", ["host", "sandbox"])
def test_worker_cli_invalid_verifier_network_fails_before_startup(tmp_path, monkeypatch, network):
    import uvicorn

    monkeypatch.setenv("MESOCOSM_WORKER_TOKEN", TOKEN)
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: pytest.fail("must not start worker"))
    result = CliRunner().invoke(worker_app, ["--root", str(tmp_path / "private"), "--repo-root", str(tmp_path),
                                           "--verifier-network", network, "--json"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "error" and payload["error_type"] == "ValueError"
    assert "verifier network must be none or bridge" in payload["detail"]
    assert TOKEN not in result.stdout and not (tmp_path / "private").exists()
