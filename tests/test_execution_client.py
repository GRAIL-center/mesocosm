"""Remote transport and CLI contracts; no real Docker or model acceptance evidence."""

from __future__ import annotations

import asyncio
import json
import stat
import zipfile

import httpx
import pytest
import yaml
from typer.testing import CliRunner

from mesocosm.execution import cli, client, package
from mesocosm.execution.protocol import ExecutionError

JOB = "a" * 32
TOKEN = "not-a-real-worker-token-" + "x" * 32
DIGEST = "b" * 64


def record(**changes):
    return {"protocol_version": 1, "job_id": JOB, "kind": "world", "digest": DIGEST,
            "entrypoint": "world.yaml", "status": "running", "created": 1, "updated": 2,
            "revision": 1, "runs": [], **changes}


def caps():
    return httpx.Response(200, json={"protocol_version": 1, "backend": {"status": "supported"}})


@pytest.mark.parametrize("url", ["http://example.com", "ftp://example.com", "https://user:password@example.com",
                                  "https://example.com?token=secret", "https://example.com/#secret",
                                  "http://127.0.0.1/../wrong", "https://example.com:99999"])
def test_worker_url_refuses_insecure_remote_and_embedded_credentials(url):
    with pytest.raises(ValueError):
        client.worker_url(url)


@pytest.mark.parametrize("url", ["https://worker.example", "https://worker.example/prefix",
                                  "http://127.0.0.1:8080", "http://[::1]:8080", "http://localhost:8080"])
def test_worker_url_allows_tls_or_local_tunnel(url):
    assert client.worker_url(url) == url + "/"


async def test_authenticated_protocol_negotiation_job_events_and_explicit_stop():
    calls = []
    async def respond(request):
        calls.append((request.method, request.url.path, request.url.params))
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        if request.url.path.endswith("capabilities"):
            return caps()
        if request.url.path.endswith("events"):
            return httpx.Response(200, json={"events": [{"kind": "run.started"}], "next_offset": 7, "eof": False})
        return httpx.Response(200, json=record(status="cancelling" if request.method == "POST" else "running"))
    async with client.ExecutionClient("https://worker.example", TOKEN, transport=httpx.MockTransport(respond)) as api:
        assert (await api.status(JOB))["status"] == "running"
        assert (await api.events(JOB, run_id="run_123", limit=2))["next_offset"] == 7
        assert (await api.stop(JOB))["status"] == "cancelling"
    assert [call[1] for call in calls].count("/v1/capabilities") == 1
    assert calls[-1][:2] == ("POST", f"/v1/jobs/{JOB}/stop")
    assert calls[-2][2]["run_id"] == "run_123"


async def test_submit_never_retries_after_uncertain_transport_failure(tmp_path):
    archive = tmp_path / "input.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("package.json", json.dumps({"digest": DIGEST}))
    original = archive.read_bytes()
    calls = []
    async def respond(request):
        calls.append(request.method)
        if request.url.path.endswith("capabilities"):
            return caps()
        assert request.headers["Idempotency-Key"] == JOB
        assert await request.aread() == original
        raise httpx.ReadError("DO NOT ECHO SECRET TOKEN", request=request)
    async with client.ExecutionClient("https://worker.example", TOKEN, transport=httpx.MockTransport(respond)) as api:
        with pytest.raises(ExecutionError, match="connection_failed") as error:
            await api.submit(archive, JOB, digest=DIGEST)
    assert "SECRET" not in str(error.value)
    assert calls == ["GET", "POST"]


@pytest.mark.parametrize("response", [httpx.Response(307, headers={"Location": "https://elsewhere.example"}),
                                     httpx.Response(401, json={"detail": "SECRET TOKEN"}),
                                     httpx.Response(200, json={"protocol_version": 2, "backend": {}})])
async def test_redirect_error_body_and_protocol_mismatch_never_leak_or_follow(response):
    calls = []
    def respond(request):
        calls.append(request.url)
        return response
    async with client.ExecutionClient("https://worker.example", TOKEN, transport=httpx.MockTransport(respond)) as api:
        with pytest.raises(ExecutionError) as error:
            await api.status(JOB)
    assert "SECRET" not in str(error.value)
    assert len(calls) == 1


async def test_cancelled_wait_and_wait_timeout_never_call_stop():
    calls = []
    async def respond(request):
        calls.append((request.method, request.url.path))
        return caps() if request.url.path.endswith("capabilities") else httpx.Response(200, json=record())
    async with client.ExecutionClient("https://worker.example", TOKEN, transport=httpx.MockTransport(respond)) as api:
        timed = await api.wait(JOB, poll_seconds=0.001, timeout_seconds=0.002)
        assert timed["detached"] and timed["reason"] == "client_wait_timeout"
        task = asyncio.create_task(api.wait(JOB))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert all(method == "GET" and not path.endswith("stop") for method, path in calls)


async def test_wait_deadline_also_bounds_an_unresponsive_status_request():
    calls = []
    async def respond(request):
        calls.append(request.method)
        if request.url.path.endswith("capabilities"):
            return caps()
        await asyncio.Event().wait()
    async with client.ExecutionClient("https://worker.example", TOKEN, transport=httpx.MockTransport(respond)) as api:
        async with asyncio.timeout(1):
            result = await api.wait(JOB, timeout_seconds=0.01)
    assert result["status"] == "unknown" and result["detached"]
    assert calls == ["GET", "GET"]


async def test_invalid_job_identity_and_response_size_are_refused(monkeypatch):
    def respond(request):
        return caps() if request.url.path.endswith("capabilities") else httpx.Response(200, json=record(job_id="c" * 32))
    async with client.ExecutionClient("https://worker.example", TOKEN, transport=httpx.MockTransport(respond)) as api:
        with pytest.raises(ExecutionError, match="identity_mismatch"):
            await api.status(JOB)
    monkeypatch.setattr(client, "MAX_RESPONSE_BYTES", 8)
    async with client.ExecutionClient("https://worker.example", TOKEN, transport=httpx.MockTransport(respond)) as api:
        with pytest.raises(ExecutionError, match="too_large"):
            await api.capabilities()


async def test_fetch_extracts_original_bundle_and_checks_job_identity(tmp_path):
    job = tmp_path / "server"
    bundle = job / "runs/run_test"
    bundle.mkdir(parents=True)
    (bundle / "manifest.json").write_text('{"sensitive":"ephemeral-control-key"}')
    (bundle / "events.jsonl").write_text('{"kind":"run.started"}\n')
    (job / "result.json").write_text('{"status":"ok"}')
    (job / "job.json").write_text(json.dumps(record(status="completed")))
    package.export_results(job, tmp_path / "results.zip")
    content = (tmp_path / "results.zip").read_bytes()
    def respond(request):
        return caps() if request.url.path.endswith("capabilities") else httpx.Response(
            200, content=content, headers={"content-type": "application/zip"})
    async with client.ExecutionClient("https://worker.example", TOKEN, transport=httpx.MockTransport(respond)) as api:
        result = await api.fetch(JOB, tmp_path / "download")
        assert result["sensitive"] and not result["redacted"]
        assert (tmp_path / "download/runs/run_test/manifest.json").read_bytes() == (bundle / "manifest.json").read_bytes()
        with pytest.raises(ExecutionError, match="identity_mismatch"):
            await api.fetch("c" * 32, tmp_path / "wrong-job")
    assert not (tmp_path / "wrong-job").exists()
    assert not list(tmp_path.glob(".mesocosm-download-*"))


async def test_fetch_rejects_oversized_download_without_partial_output(tmp_path, monkeypatch):
    monkeypatch.setattr(client, "MAX_ARCHIVE_BYTES", 10)
    def respond(request):
        return caps() if request.url.path.endswith("capabilities") else httpx.Response(
            200, content=b"x" * 20, headers={"content-type": "application/zip"})
    async with client.ExecutionClient("https://worker.example", TOKEN, transport=httpx.MockTransport(respond)) as api:
        with pytest.raises(ExecutionError, match="too_large"):
            await api.fetch(JOB, tmp_path / "out")
    assert not (tmp_path / "out").exists()
    assert not list(tmp_path.glob(".mesocosm-download-*"))


def make_world(root):
    task = root / "task"
    task.mkdir()
    (task / "instruction.md").write_text("Original input retained in a transport test")
    path = root / "world.yaml"
    path.write_text(yaml.safe_dump({"name": "test", "model": {"id": "model"},
        "gateway": {"url": "http://unused", "api_key": "secret"},
        "agents": [{"id": f"agent-{i}"} for i in range(3)],
        "tasks": [{"path": "task"}], "services": {"enabled": False}}))
    return path


def test_cli_durable_receipt_before_submit_and_resume_retains_exact_identity(tmp_path, monkeypatch):
    path = make_world(tmp_path)
    receipt = tmp_path / "receipt.json"
    calls = []
    state = {"fail": True}
    class API:
        def __init__(self, url, token):
            assert token == TOKEN
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def submit(self, archive, job_id, *, digest):
            saved = json.loads(receipt.read_text())
            assert saved["job_id"] == job_id and saved["digest"] == digest
            assert stat.S_IMODE(receipt.stat().st_mode) == 0o600
            calls.append((job_id, digest, archive.read_bytes()))
            if state["fail"]:
                raise ExecutionError("worker_connection_failed")
            return record(job_id=job_id, digest=digest, status="queued")
    monkeypatch.setattr(cli, "ExecutionClient", API)
    monkeypatch.setenv("MESOCOSM_WORKER_TOKEN", TOKEN)
    runner = CliRunner()
    first = runner.invoke(cli.execution_app, ["submit", str(path), "--worker", "https://worker.example",
        "--receipt", str(receipt), "--job-id", JOB, "--json"])
    assert first.exit_code == 1, first.output
    error = json.loads(first.stdout)
    assert error["job_id"] == JOB and error["submission"] == "unconfirmed"
    assert TOKEN not in first.output
    assert json.loads(receipt.read_text())["state"] == "prepared"
    state["fail"] = False
    second = runner.invoke(cli.execution_app, ["submit", "--resume", str(receipt), "--json"])
    assert second.exit_code == 0, second.output
    assert json.loads(second.stdout)["job_id"] == JOB
    assert calls[0] == calls[1]
    assert json.loads(receipt.read_text())["state"] == "accepted"


def test_cli_json_validation_failure_is_one_object_without_secret_traceback(tmp_path, monkeypatch):
    monkeypatch.setenv("MESOCOSM_WORKER_TOKEN", TOKEN)
    result = CliRunner().invoke(cli.execution_app, ["status", JOB, "--worker", "https://secret:password@worker.example", "--json"])
    assert result.exit_code == 1
    value = json.loads(result.stdout)
    assert value["error"] == "ValueError"
    assert "password" not in result.output and TOKEN not in result.output and "Traceback" not in result.output


def test_cli_interrupt_does_not_stop_job(monkeypatch):
    calls = []
    class API:
        def __init__(self, *args):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            calls.append("closed")
        async def wait(self, job_id, **kwargs):
            raise KeyboardInterrupt
        async def stop(self, job_id):
            calls.append("STOP")
    monkeypatch.setattr(cli, "ExecutionClient", API)
    monkeypatch.setenv("MESOCOSM_WORKER_TOKEN", TOKEN)
    result = CliRunner().invoke(cli.execution_app, ["wait", JOB, "--worker", "https://worker.example", "--json"])
    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout)["detached"]
    assert calls == ["closed"]


async def test_changed_receipt_package_is_rejected_before_network_submission(tmp_path):
    archive = tmp_path / "changed.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("package.json", json.dumps({"digest": "c" * 64}))
    calls = []
    def respond(request):
        calls.append(request)
        return caps()
    async with client.ExecutionClient("https://worker.example", TOKEN, transport=httpx.MockTransport(respond)) as api:
        with pytest.raises(ValueError, match="retained receipt"):
            await api.submit(archive, JOB, digest=DIGEST)
    assert not calls


async def test_client_uses_real_worker_http_contract_and_detaches_independently(tmp_path):
    from mesocosm.execution.worker import Worker, create_app

    class Backend:
        def __init__(self):
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.calls = 0
        async def capabilities(self):
            return {"status": "supported", "backend": "test_boundary"}
        async def execute(self, job_dir, input_dir, kind, entrypoint):
            self.calls += 1
            self.started.set()
            await self.release.wait()
            bundle = job_dir / "runs/run_original"
            bundle.mkdir(parents=True)
            (bundle / "manifest.json").write_text('{"original":true}')
            (bundle / "events.jsonl").write_text('{"kind":"original.event"}\n')
            return {"status": "ok", "cleanup": {"status": "complete"}}
        async def recover(self, job_dir):
            return {"status": "complete"}

    manifest = make_world(tmp_path)
    metadata = package.pack_submission(manifest, tmp_path / "submission.zip")
    backend = Backend()
    worker = Worker(tmp_path / "private-worker", backend, lock_file=tmp_path / "lease.lock")
    app = create_app(worker, TOKEN)
    async with app.router.lifespan_context(app):
        async with client.ExecutionClient("http://127.0.0.1", TOKEN, transport=httpx.ASGITransport(app=app)) as api:
            submitted = await api.submit(tmp_path / "submission.zip", JOB, digest=metadata["digest"])
            assert submitted["job_id"] == JOB
            await asyncio.wait_for(backend.started.wait(), 3)
            detached = await api.wait(JOB, poll_seconds=0.001, timeout_seconds=0.002)
            assert detached["detached"]
        assert not worker.tasks[JOB].done()
        async with client.ExecutionClient("http://127.0.0.1", TOKEN, transport=httpx.ASGITransport(app=app)) as api:
            duplicate = await api.submit(tmp_path / "submission.zip", JOB, digest=metadata["digest"])
            assert duplicate["job_id"] == JOB and backend.calls == 1
            backend.release.set()
            await worker.tasks[JOB]
            assert (await api.status(JOB))["status"] == "completed"
            events = await api.events(JOB, run_id="run_original")
            assert events["events"] == [{"kind": "original.event"}]
            fetched = await api.fetch(JOB, tmp_path / "download")
            assert fetched["sensitive"]
            assert (tmp_path / "download/runs/run_original/events.jsonl").read_bytes() == b'{"kind":"original.event"}\n'
