"""Remote execution CLI with durable submission identity and explicit stopping."""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import re
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import typer

from mesocosm.execution.client import ExecutionClient, _job_id, worker_url
from mesocosm.execution.package import _read, pack_submission
from mesocosm.execution.protocol import PROTOCOL_VERSION, ExecutionError

execution_app = typer.Typer(no_args_is_help=True, help="Submit, observe and retrieve worker-owned experiments")
WORKER = typer.Option(None, "--worker", envvar="MESOCOSM_WORKER_URL", help="HTTPS worker or loopback tunnel URL")
TOKEN_ENV = typer.Option("MESOCOSM_WORKER_TOKEN", "--token-env", help="Environment variable holding the worker token")
JSON = typer.Option(False, "--json", help="Print exactly one JSON object")


def _emit(value: dict[str, Any], as_json: bool, *, failed: bool = False) -> None:
    if as_json:
        sys.stdout.write(json.dumps(value, ensure_ascii=False) + "\n")
    else:
        for key, item in value.items():
            typer.echo(f"{key}: {item}")
    if failed:
        raise typer.Exit(1)


def _token(variable: str) -> str:
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", variable):
        raise ValueError("invalid token environment variable")
    value = os.environ.get(variable)
    if not value:
        raise ValueError("worker token environment variable is not set")
    return value


def _url(value: str | None) -> str:
    if value is None:
        raise ValueError("worker URL is required")
    return worker_url(value)


def _error(exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, KeyboardInterrupt):
        return {"ok": False, "error": "client_interrupted", "detached": True}
    if isinstance(exc, ExecutionError):
        return {"ok": False, "error": str(exc), "http_status": exc.status_code}
    # Validation exceptions may contain submitted secrets; report their type only.
    return {"ok": False, "error": type(exc).__name__}


def _call(operation: Callable[[ExecutionClient], Awaitable[dict[str, Any]]], worker: str | None,
          token_env: str, as_json: bool, *, job_id: str | None = None, stopping: bool = False) -> None:
    async def invoke() -> dict[str, Any]:
        async with ExecutionClient(_url(worker), _token(token_env)) as client:
            return await operation(client)
    try:
        result = asyncio.run(invoke())
    except (Exception, KeyboardInterrupt) as exc:
        error = _error(exc)
        if job_id is not None:
            error.update(job_id=job_id, stop_requested=stopping, remote_state="unknown")
        _emit(error, as_json, failed=True)
        return
    _emit(result, as_json)


def _receipt(path: pathlib.Path, value: dict[str, Any], *, new: bool = False) -> None:
    """Durably publish only non-secret submission identity, before the network write."""
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as output:
            temporary.chmod(0o600)
            json.dump(value, output, indent=1, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        if new:
            # Exclusive hard-link publication prevents overwriting an unrelated receipt.
            os.link(temporary, path)
            temporary.unlink()
        else:
            temporary.replace(path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def _prepare(manifest: pathlib.Path | None, kind: str, job_id: str | None,
             receipt: pathlib.Path | None, resume: pathlib.Path | None,
             worker: str | None) -> tuple[pathlib.Path, dict[str, Any]]:
    if resume is not None:
        if manifest is not None or receipt is not None or job_id is not None:
            raise ValueError("resume cannot be combined with a new manifest, receipt or job ID")
        value = json.loads(_read(resume, 65536))
        if (not isinstance(value, dict) or value.get("schema_version") != "mesocosm.execution.receipt.v1"
                or value.get("protocol_version") != PROTOCOL_VERSION):
            raise ValueError("invalid submission receipt")
        _job_id(value["job_id"])
        original_worker = worker_url(value["worker"])
        if worker is not None and worker_url(worker) != original_worker:
            raise ValueError("resume must use the original worker")
        archive = value.get("archive")
        if not isinstance(archive, str) or pathlib.Path(archive).name != archive or archive in (".", ".."):
            raise ValueError("invalid receipt archive path")
        if not re.fullmatch(r"[a-f0-9]{64}", str(value.get("digest", ""))):
            raise ValueError("invalid receipt digest")
        return resume.resolve(), value
    if manifest is None:
        raise ValueError("a manifest or --resume receipt is required")
    identifier = _job_id(job_id) if job_id else uuid.uuid4().hex
    address = _url(worker)
    if receipt is None:
        directory = pathlib.Path.cwd() / ".mesocosm-execution"
        directory.mkdir(mode=0o700, exist_ok=True)
        if directory.is_symlink() or directory.stat().st_mode & 0o077:
            raise ValueError("default receipt directory must be private")
        receipt = directory / f"{identifier}.json"
    receipt = receipt.absolute()
    if receipt.exists() or receipt.is_symlink():
        raise FileExistsError("receipt already exists; use --resume")
    archive_path = receipt.with_suffix(".zip")
    if archive_path == receipt:
        raise ValueError("receipt and archive paths must differ")
    metadata = pack_submission(manifest, archive_path, kind=kind)
    try:
        descriptor = os.open(archive_path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        value = {"schema_version": "mesocosm.execution.receipt.v1", "protocol_version": PROTOCOL_VERSION,
                 "job_id": identifier, "worker": address, "kind": kind, "digest": metadata["digest"],
                 "entrypoint": metadata["entrypoint"], "archive": archive_path.name,
                 "created": time.time(), "state": "prepared"}
        _receipt(receipt, value, new=True)
    except BaseException:
        archive_path.unlink(missing_ok=True)
        raise
    return receipt, value


@execution_app.command()
def capabilities(worker: str | None = WORKER, token_env: str = TOKEN_ENV, json_: bool = JSON) -> None:
    """Inspect the worker protocol, execution capabilities and admission state."""
    _call(lambda client: client.capabilities(), worker, token_env, json_)


@execution_app.command()
def submit(manifest: pathlib.Path | None = typer.Argument(None),
           kind: str = typer.Option("world", "--kind", help="world or experiment"),
           job_id: str | None = typer.Option(None, "--job-id", help="Stable 32-character hexadecimal request ID"),
           receipt: pathlib.Path | None = typer.Option(None, "--receipt", help="New private receipt file"),
           resume: pathlib.Path | None = typer.Option(None, "--resume", help="Retry the exact retained request and ID"),
           worker: str | None = WORKER, token_env: str = TOKEN_ENV, json_: bool = JSON) -> None:
    """Package a world/experiment, persist its identity, then submit once. Does not wait."""
    saved: pathlib.Path | None = None
    record: dict[str, Any] | None = None
    try:
        token = _token(token_env)
        saved, record = _prepare(manifest, kind, job_id, receipt, resume, worker)
        # Keep the receipt unchanged until the result is known. An interruption can always
        # reconnect by its job ID or repeat the same immutable package with --resume.
        async def invoke() -> dict[str, Any]:
            assert saved is not None and record is not None
            async with ExecutionClient(record["worker"], token) as client:
                return await client.submit(saved.parent / record["archive"], record["job_id"], digest=record["digest"])
        result = asyncio.run(invoke())
        record.update(state="accepted", last_status=result["status"])
        _receipt(saved, record)
    except (Exception, KeyboardInterrupt) as exc:
        error = _error(exc)
        if saved is not None and record is not None:
            error.update(job_id=record["job_id"], receipt=str(saved),
                         submission="accepted" if record["state"] == "accepted" else "unconfirmed",
                         remote_job_stopped=False)
        _emit(error, json_, failed=True)
        return
    _emit({**result, "receipt": str(saved)}, json_)


@execution_app.command()
def status(job_id: str, worker: str | None = WORKER, token_env: str = TOKEN_ENV, json_: bool = JSON) -> None:
    """Read status without attaching the client's lifetime to the job."""
    _call(lambda client: client.status(job_id), worker, token_env, json_, job_id=job_id)


@execution_app.command()
def stop(job_id: str, worker: str | None = WORKER, token_env: str = TOKEN_ENV, json_: bool = JSON) -> None:
    """Explicitly request job cancellation and worker-owned cleanup."""
    _call(lambda client: client.stop(job_id), worker, token_env, json_, job_id=job_id, stopping=True)


@execution_app.command()
def events(job_id: str, run_id: str | None = typer.Option(None, "--run-id"),
           offset: int = typer.Option(0, "--offset"), limit: int = typer.Option(200, "--limit"),
           worker: str | None = WORKER, token_env: str = TOKEN_ENV, json_: bool = JSON) -> None:
    """Read one bounded page of job or run events; use next_offset for the next page."""
    _call(lambda client: client.events(job_id, run_id=run_id, offset=offset, limit=limit),
          worker, token_env, json_, job_id=job_id)


@execution_app.command()
def wait(job_id: str, poll_seconds: float = typer.Option(2, "--poll-seconds"),
         timeout_seconds: float | None = typer.Option(None, "--timeout-seconds"),
         worker: str | None = WORKER, token_env: str = TOKEN_ENV, json_: bool = JSON) -> None:
    """Wait for terminal status. Timeout, disconnect and Ctrl-C leave the job running."""
    _call(lambda client: client.wait(job_id, poll_seconds=poll_seconds, timeout_seconds=timeout_seconds),
          worker, token_env, json_, job_id=job_id)


@execution_app.command()
def fetch(job_id: str, out: pathlib.Path = typer.Option(..., "--out", help="New private result directory"),
          worker: str | None = WORKER, token_env: str = TOKEN_ENV, json_: bool = JSON) -> None:
    """Fetch terminal results. Original run bundles are sensitive and unredacted."""
    _call(lambda client: client.fetch(job_id, out), worker, token_env, json_, job_id=job_id)
