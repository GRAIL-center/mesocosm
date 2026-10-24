"""Authenticated, durable single-host job ownership for whole research worlds.

Client disconnects do not cancel accepted jobs. Worker restart cleans interrupted worlds before
admission; observations are never silently retried. The service is for one trusted research operator.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import os
import pathlib
import re
import shutil
import time
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any, TypeVar

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from mesocosm.execution.package import MAX_ARCHIVE_BYTES, export_results, unpack_submission
from mesocosm.execution.protocol import PROTOCOL_VERSION, TERMINAL, ExecutionBackend, JobRecord

JOB_ID = re.compile(r"^[a-f0-9]{32}$")
RUN_ID = re.compile(r"^run_[a-zA-Z0-9_-]+$")
MAX_UPLOAD_BYTES = MAX_ARCHIVE_BYTES
MAX_EVENT_BYTES = 4 * 1024 * 1024
T = TypeVar("T")


async def _transfer(operation: Callable[[], T]) -> T:
    """Keep hashing/copying off the world event loop, and finish before deleting its staging path."""
    task = asyncio.create_task(asyncio.to_thread(operation))
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            break
    result = task.result()
    if cancelled:
        raise asyncio.CancelledError
    return result


def _write_json(path: pathlib.Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        os.chmod(temporary, 0o600)
        json.dump(value, stream, ensure_ascii=False, indent=1)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class Worker:
    def __init__(self, root: pathlib.Path, backend: ExecutionBackend, *, max_pending: int = 16,
                 lock_file: pathlib.Path | None = None):
        self.root = root.resolve()
        if max_pending < 1:
            raise ValueError("max_pending must be positive")
        self.backend = backend
        self.max_pending = max_pending
        # One lease per host, independent of the chosen job directory. All managed Docker worlds
        # share the current fixed subnet allocation. Manual Docker deployments remain operator-owned.
        self.lock_file = lock_file or pathlib.Path("/tmp/mesocosm-docker-worker.lock")
        self.records: dict[str, JobRecord] = {}
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.serial = asyncio.Semaphore(1)
        self.admission = asyncio.Lock()
        self.exports = asyncio.Lock()
        self.blocked: set[str] = set()
        self.closing = False
        self.lease: Any = None

    def directory(self, job_id: str) -> pathlib.Path:
        if not JOB_ID.fullmatch(job_id):
            raise HTTPException(404, "unknown_job")
        return self.root / "jobs" / job_id

    def record(self, job_id: str) -> JobRecord:
        self.directory(job_id)
        if job_id not in self.records:
            raise HTTPException(404, "unknown_job")
        return self.records[job_id]

    def _save(self, record: JobRecord, **changes: Any) -> None:
        changed = record.model_copy(update={**changes, "updated": time.time(), "revision": record.revision + 1})
        # Revalidate persisted state so a malformed backend result cannot silently corrupt admission.
        record = JobRecord.model_validate(changed.model_dump())
        self.records[record.job_id] = record
        directory = self.directory(record.job_id)
        _write_json(directory / "job.json", record.model_dump(mode="json"))
        (directory / "results.zip").unlink(missing_ok=True)
        with (directory / "events.jsonl").open("a", encoding="utf-8") as stream:
            os.chmod(directory / "events.jsonl", 0o600)
            stream.write(json.dumps({"kind": "execution.status", **record.model_dump(mode="json")}) + "\n")

    def _receipt(self, job_id: str, result: dict[str, Any]) -> None:
        path = self.directory(job_id) / "result.json"
        if path.exists():
            result = {**result, "previous_result": json.loads(path.read_text())}
        _write_json(path, result)

    async def start(self) -> None:
        import fcntl

        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.stat().st_mode & 0o077:
            raise ValueError("worker root must be private (mode 0700)")
        (self.root / "jobs").mkdir(exist_ok=True, mode=0o700)
        descriptor = os.open(self.lock_file, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        self.lease = os.fdopen(descriptor, "a+")
        try:
            fcntl.flock(self.lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            for path in sorted((self.root / "jobs").glob("*/job.json")):
                record = JobRecord.model_validate_json(path.read_text())
                if path.parent != self.directory(record.job_id) or path.is_symlink() or path.parent.is_symlink():
                    raise ValueError("invalid persisted job identity")
                self.records[record.job_id] = record
            for record in list(self.records.values()):
                if record.status not in TERMINAL or record.status == "failed_cleanup":
                    try:
                        cleanup = await self.backend.recover(self.directory(record.job_id))
                        if cleanup.get("status") != "complete":
                            raise RuntimeError("unverified recovery")
                    except Exception as exc:
                        self.blocked.add(record.job_id)
                        self._receipt(record.job_id, {"status": "failed_cleanup", "cleanup": {"status": "failed"},
                                                      "error_type": type(exc).__name__})
                        self._save(record, status="failed_cleanup", error_type=type(exc).__name__,
                                   cleanup={"status": "failed"})
                    else:
                        self._receipt(record.job_id, {"status": "interrupted", "cleanup": cleanup})
                        self._save(record, status="interrupted", cleanup=cleanup,
                                   error_type="WorkerRestarted")
        except BaseException:
            self.lease.close()
            self.lease = None
            raise

    async def close(self) -> None:
        self.closing = True
        for job_id, task in list(self.tasks.items()):
            if not task.done():
                self.stop(job_id)
        pending = asyncio.gather(*self.tasks.values(), return_exceptions=True)
        while not pending.done():
            try:
                await asyncio.shield(pending)
            except asyncio.CancelledError:
                continue
        pending.result()
        if self.lease is not None:
            self.lease.close()
            self.lease = None

    async def capabilities(self) -> dict[str, Any]:
        return {"protocol_version": PROTOCOL_VERSION, "backend": await self.backend.capabilities(),
                "job_kinds": ["world", "experiment"], "max_active": 1, "max_pending": self.max_pending,
                "admission": "closed" if self.closing or self.blocked else "open",
                "blocked_jobs": sorted(self.blocked), "recovery": "cleanup_without_observation_retry"}

    async def submit(self, archive: pathlib.Path, job_id: str) -> JobRecord:
        directory = self.directory(job_id)
        async with self.admission:
            # Validate retries too: the same identity is only idempotent for the same input digest.
            temporary = self.root / f".submission-{uuid.uuid4().hex}"
            try:
                metadata = await _transfer(lambda: unpack_submission(archive, temporary))
                if job_id in self.records:
                    previous = self.records[job_id]
                    if previous.digest != metadata["digest"]:
                        raise HTTPException(409, "job_id_reused_with_different_inputs")
                    return previous
                if self.closing or self.blocked:
                    raise HTTPException(503, "worker_cleanup_pending")
                if sum(record.status not in TERMINAL for record in self.records.values()) >= self.max_pending:
                    raise HTTPException(429, "worker_queue_full")
                available = await self.backend.capabilities()
                if self.closing or self.blocked:
                    raise HTTPException(503, "worker_cleanup_pending")
                if available.get("status") != "supported":
                    raise HTTPException(503, "execution_backend_unavailable")
                if directory.exists():
                    # No durable job record means admission never completed and no execution task
                    # was launched. Fail closed instead of guessing whether disk state is safe.
                    raise HTTPException(409, "incomplete_admission_use_new_job_id")
                directory.mkdir(mode=0o700)
                temporary.rename(directory / "input")
                now = time.time()
                record = JobRecord(job_id=job_id, kind=metadata["kind"], digest=metadata["digest"],
                                   entrypoint=metadata["entrypoint"], created=now, updated=now)
                self._save(record)
                self.tasks[job_id] = asyncio.create_task(self._run(job_id))
                return self.records[job_id]
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)

    async def _recover(self, directory: pathlib.Path) -> dict[str, Any]:
        cleanup_task = asyncio.create_task(self.backend.recover(directory))
        while not cleanup_task.done():
            try:
                await asyncio.shield(cleanup_task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        try:
            cleanup = cleanup_task.result()
            if cleanup.get("status") != "complete":
                raise RuntimeError("unverified cleanup")
            return cleanup
        except (Exception, asyncio.CancelledError):
            return {"status": "failed"}

    async def _run(self, job_id: str) -> None:
        directory = self.directory(job_id)
        # Cancellation while queued must never clean up concurrently with the active world.
        async with self.serial:
            record = self.record(job_id)
            if record.status == "cancelled":
                return
            if self.blocked or self.closing:
                self._receipt(job_id, {"status": "cancelled", "cleanup": {"status": "complete", "started": False}})
                self._save(record, status="cancelled", error_type="WorkerUnavailable",
                           cleanup={"status": "complete", "started": False})
                return
            self._save(record, status="running")
            try:
                result = await self.backend.execute(directory, directory / "input", record.kind, record.entrypoint)
                self._receipt(job_id, result)
                record = self.record(job_id)
                cleanup = result.get("cleanup")
                if not isinstance(cleanup, dict) or cleanup.get("status") != "complete":
                    self.blocked.add(job_id)
                    status = "failed_cleanup"
                elif record.status == "cancelling" or result.get("status") == "cancelled":
                    status = "cancelled"
                else:
                    status = "completed" if result.get("status") == "ok" else "failed"
                self._save(record, status=status, result_status=result.get("status"), cleanup=cleanup)
            except (Exception, asyncio.CancelledError) as exc:
                # Keep the scheduling lease until recovery finishes, including repeated cancellation.
                cleanup = await self._recover(directory)
                if cleanup.get("status") != "complete":
                    self.blocked.add(job_id)
                record = self.record(job_id)
                status = ("failed_cleanup" if job_id in self.blocked else
                          "cancelled" if isinstance(exc, asyncio.CancelledError) or record.status == "cancelling"
                          else "failed")
                self._receipt(job_id, {"status": status, "error_type": type(exc).__name__, "cleanup": cleanup})
                self._save(record, status=status, error_type=type(exc).__name__, cleanup=cleanup)

    def stop(self, job_id: str) -> JobRecord:
        record = self.record(job_id)
        if record.status in TERMINAL or record.status == "cancelling":
            return record
        if record.status == "queued":
            self._receipt(job_id, {"status": "cancelled", "cleanup": {"status": "complete", "started": False}})
            self._save(record, status="cancelled", cleanup={"status": "complete", "started": False})
        else:
            self._save(record, status="cancelling")
        task = self.tasks.get(job_id)
        if task is not None:
            task.cancel()
        return self.record(job_id)

    def snapshot(self, job_id: str) -> dict[str, Any]:
        record = self.record(job_id)
        runs = []
        for path in sorted((self.directory(job_id) / "runs").glob("run_*")):
            if path.is_dir() and not path.is_symlink() and (path / "manifest.json").is_file():
                runs.append({"run_id": path.name, "summary_available": (path / "summary.json").is_file()})
        return {**record.model_dump(mode="json"), "runs": runs}

    def events(self, job_id: str, run_id: str | None, offset: int, limit: int) -> dict[str, Any]:
        self.record(job_id)
        directory = self.directory(job_id)
        if run_id is not None:
            if not RUN_ID.fullmatch(run_id):
                raise HTTPException(404, "unknown_run")
            directory = directory / "runs" / run_id
            if directory.is_symlink() or not (directory / "manifest.json").is_file():
                raise HTTPException(404, "unknown_run")
        path = directory / "events.jsonl"
        if not path.exists():
            return {"events": [], "next_offset": 0, "eof": True}
        if path.is_symlink() or not path.is_file():
            raise HTTPException(409, "invalid_event_file")
        values: list[Any] = []
        size = path.stat().st_size
        if offset < 0 or offset > size or not 1 <= limit <= 1000:
            raise HTTPException(422, "invalid_event_cursor")
        with path.open("rb") as stream:
            stream.seek(offset)
            while len(values) < limit and stream.tell() - offset < MAX_EVENT_BYTES:
                before = stream.tell()
                line = stream.readline(MAX_EVENT_BYTES + 1)
                if len(line) > MAX_EVENT_BYTES:
                    if not values:
                        raise HTTPException(413, "event_too_large_use_result_bundle")
                    stream.seek(before)
                    break
                if not line.endswith(b"\n"):
                    stream.seek(before)
                    break
                try:
                    values.append(json.loads(line))
                except (ValueError, UnicodeDecodeError):
                    raise HTTPException(409, "malformed_event_record") from None
            next_offset = stream.tell()
        return {"events": values, "next_offset": next_offset, "eof": next_offset >= size}


def create_app(worker: Worker, token: str) -> FastAPI:
    if len(token) < 32 or any(ord(character) < 33 or ord(character) > 126 for character in token):
        raise ValueError("worker token must contain at least 32 printable non-space ASCII characters")

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await worker.start()
        try:
            yield
        finally:
            await worker.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def authenticate(request: Request, call_next: Any) -> Any:
        value = request.headers.get("Authorization", "")
        if not hmac.compare_digest(value.encode(), f"Bearer {token}".encode()):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return await call_next(request)

    @app.get("/v1/capabilities")
    async def capabilities() -> dict[str, Any]:
        return await worker.capabilities()

    @app.get("/v1/jobs")
    async def jobs() -> dict[str, Any]:
        return {"jobs": [worker.snapshot(record.job_id) for record in
                         sorted(worker.records.values(), key=lambda item: item.created, reverse=True)[:100]]}

    @app.post("/v1/jobs", status_code=202)
    async def submit(request: Request) -> dict[str, Any]:
        job_id = request.headers.get("Idempotency-Key", "")
        worker.directory(job_id)
        archive = worker.root / f".upload-{uuid.uuid4().hex}.zip"
        total = 0
        try:
            with archive.open("xb") as stream:
                os.chmod(archive, 0o600)
                async for chunk in request.stream():
                    total += len(chunk)
                    if total > MAX_UPLOAD_BYTES:
                        raise HTTPException(413, "submission_too_large")
                    stream.write(chunk)
            try:
                record = await worker.submit(archive, job_id)
            except (ValueError, OSError) as exc:
                raise HTTPException(422, f"invalid_submission:{type(exc).__name__}") from None
            return worker.snapshot(record.job_id)
        finally:
            archive.unlink(missing_ok=True)

    @app.get("/v1/jobs/{job_id}")
    async def status(job_id: str) -> dict[str, Any]:
        return worker.snapshot(job_id)

    @app.post("/v1/jobs/{job_id}/stop")
    async def stop(job_id: str) -> dict[str, Any]:
        worker.stop(job_id)
        return worker.snapshot(job_id)

    @app.get("/v1/jobs/{job_id}/events")
    async def events(job_id: str, offset: int = 0, limit: int = 200,
                     run_id: str | None = None) -> dict[str, Any]:
        return worker.events(job_id, run_id, offset, limit)

    @app.get("/v1/jobs/{job_id}/results")
    async def results(job_id: str) -> FileResponse:
        if worker.record(job_id).status not in TERMINAL:
            raise HTTPException(409, "results_not_final")
        directory = worker.directory(job_id)
        archive = directory / "results.zip"
        async with worker.exports:
            if not archive.exists():
                try:
                    await _transfer(lambda: export_results(directory, archive))
                except (OSError, ValueError) as exc:
                    raise HTTPException(409, f"results_unavailable:{type(exc).__name__}") from None
        return FileResponse(archive, media_type="application/zip", filename=f"{job_id}.zip")

    return app
