"""Authenticated bounded remote execution client; disconnecting never stops a job."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import math
import os
import pathlib
import re
import shutil
import tempfile
import time
import zipfile
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlsplit

import httpx

from mesocosm.execution.package import MAX_ARCHIVE_BYTES, MAX_INDEX_BYTES, _identity, _regular, extract_results
from mesocosm.execution.protocol import PROTOCOL_VERSION, TERMINAL, ExecutionError, JobRecord

MAX_RESPONSE_BYTES = 8 * 1024 * 1024
JOB_ID = re.compile(r"^[a-f0-9]{32}$")
RUN_ID = re.compile(r"^run_[a-zA-Z0-9_-]+$")


def worker_url(value: str) -> str:
    """Allow authenticated HTTPS or a local tunnel without embedding credentials."""
    try:
        parsed = urlsplit(value)
        port = parsed.port
        host = parsed.hostname
        if (not host or any(character.isspace() for character in value)
                or parsed.scheme not in ("https", "http") or parsed.username or parsed.password
                or parsed.query or parsed.fragment or (port is not None and port <= 0)):
            raise ValueError
        if parsed.scheme == "http" and host != "localhost":
            try:
                loopback = ipaddress.ip_address(host).is_loopback
            except ValueError:
                loopback = False
            if not loopback:
                raise ValueError
        if any(part in (".", "..") for part in parsed.path.split("/")):
            raise ValueError
    except (ValueError, UnicodeError):
        raise ValueError("worker URL requires HTTPS or loopback HTTP without credentials or query parameters") from None
    return value.rstrip("/") + "/"


def _job_id(value: str) -> str:
    if not JOB_ID.fullmatch(value):
        raise ValueError("job ID must contain exactly 32 lowercase hexadecimal characters")
    return value


class ExecutionClient:
    def __init__(self, url: str, token: str, *, transport: httpx.AsyncBaseTransport | None = None):
        self.url = worker_url(url)
        if len(token) < 32 or any(ord(character) < 33 or ord(character) > 126 for character in token):
            raise ValueError("worker token must contain at least 32 printable non-space ASCII characters")
        self._http = httpx.AsyncClient(base_url=self.url, headers={"Authorization": f"Bearer {token}"},
            follow_redirects=False, trust_env=False, transport=transport,
            timeout=httpx.Timeout(60, connect=10, write=120, pool=10))
        self._checked = False

    async def __aenter__(self) -> ExecutionClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def close(self) -> None:
        await self._http.aclose()

    @staticmethod
    def _status(response: httpx.Response) -> None:
        if not 200 <= response.status_code < 300:
            # Neither server error bodies nor HTTP exception reprs are safe to echo.
            reason = "worker_redirect_refused" if response.is_redirect else "worker_request_rejected"
            raise ExecutionError(reason, status_code=response.status_code)

    async def _json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            async with self._http.stream(method, path, **kwargs) as response:
                self._status(response)
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(content) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise ExecutionError("worker_response_too_large")
                    content.extend(chunk)
                try:
                    value = json.loads(content)
                except (ValueError, UnicodeError):
                    raise ExecutionError("worker_response_invalid_json") from None
                if not isinstance(value, dict):
                    raise ExecutionError("worker_response_invalid_shape")
                return value
        except httpx.HTTPError:
            raise ExecutionError("worker_connection_failed; accepted jobs continue independently") from None

    async def capabilities(self) -> dict[str, Any]:
        value = await self._json("GET", "v1/capabilities")
        if type(value.get("protocol_version")) is not int or value["protocol_version"] != PROTOCOL_VERSION:
            raise ExecutionError("worker_protocol_mismatch")
        if not isinstance(value.get("backend"), dict):
            raise ExecutionError("worker_capabilities_invalid")
        self._checked = True
        return value

    async def _check(self) -> None:
        if not self._checked:
            await self.capabilities()

    @staticmethod
    def _record(value: dict[str, Any], job_id: str, digest: str | None = None) -> dict[str, Any]:
        if type(value.get("protocol_version")) is not int or value["protocol_version"] != PROTOCOL_VERSION:
            raise ExecutionError("worker_protocol_mismatch")
        record = {key: item for key, item in value.items() if key != "runs"}
        try:
            JobRecord.model_validate(record)
        except ValueError:
            raise ExecutionError("worker_job_response_invalid") from None
        if value["job_id"] != job_id or (digest is not None and value["digest"] != digest):
            raise ExecutionError("worker_job_identity_mismatch")
        runs = value.get("runs", [])
        if not isinstance(runs, list) or any(not isinstance(run, dict) or not RUN_ID.fullmatch(
                str(run.get("run_id", ""))) for run in runs):
            raise ExecutionError("worker_run_identity_invalid")
        return value

    async def submit(self, archive: pathlib.Path, job_id: str, *, digest: str | None = None) -> dict[str, Any]:
        """Submit once with a caller-retained identity; never automatically retry uncertain requests."""
        _job_id(job_id)
        before = _regular(archive)
        if not archive.is_file() or before.st_size > MAX_ARCHIVE_BYTES:
            raise ValueError("submission archive is not a bounded regular file")
        if digest is not None:
            try:
                with zipfile.ZipFile(archive) as package:
                    index = package.getinfo("package.json")
                    if index.file_size > MAX_INDEX_BYTES or index.compress_type != zipfile.ZIP_STORED:
                        raise ValueError("submission index must be bounded and uncompressed")
                    metadata = json.loads(package.read(index))
                    if not isinstance(metadata, dict) or metadata.get("digest") != digest:
                        raise ValueError("submission differs from retained receipt digest")
            except (zipfile.BadZipFile, KeyError, UnicodeError):
                raise ValueError("submission archive has an invalid index") from None
        await self._check()

        async def chunks() -> AsyncIterator[bytes]:
            descriptor = os.open(archive, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as source:
                if _identity(os.fstat(source.fileno())) != _identity(before):
                    raise ValueError("submission changed before upload")
                total = 0
                while chunk := source.read(65536):
                    total += len(chunk)
                    if total > before.st_size:
                        raise ValueError("submission changed during upload")
                    yield chunk
                if total != before.st_size or _identity(os.fstat(source.fileno())) != _identity(before):
                    raise ValueError("submission changed during upload")

        value = await self._json("POST", "v1/jobs", content=chunks(), headers={
            "Content-Type": "application/zip", "Content-Length": str(before.st_size), "Idempotency-Key": job_id})
        return self._record(value, job_id, digest)

    async def status(self, job_id: str) -> dict[str, Any]:
        _job_id(job_id)
        await self._check()
        return self._record(await self._json("GET", f"v1/jobs/{job_id}"), job_id)

    async def stop(self, job_id: str) -> dict[str, Any]:
        """Explicit operator action; never called by wait, close, errors or cancellation."""
        _job_id(job_id)
        await self._check()
        return self._record(await self._json("POST", f"v1/jobs/{job_id}/stop"), job_id)

    async def events(self, job_id: str, *, run_id: str | None = None,
                     offset: int = 0, limit: int = 200) -> dict[str, Any]:
        _job_id(job_id)
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("invalid event cursor or limit")
        if run_id is not None and not RUN_ID.fullmatch(run_id):
            raise ValueError("invalid run ID")
        await self._check()
        parameters: dict[str, Any] = {"offset": offset, "limit": limit}
        if run_id is not None:
            parameters["run_id"] = run_id
        value = await self._json("GET", f"v1/jobs/{job_id}/events", params=parameters)
        if (not isinstance(value.get("events"), list) or len(value["events"]) > limit
                or type(value.get("next_offset")) is not int or value["next_offset"] < offset
                or type(value.get("eof")) is not bool):
            raise ExecutionError("worker_event_response_invalid")
        return value

    async def wait(self, job_id: str, *, poll_seconds: float = 2,
                   timeout_seconds: float | None = None) -> dict[str, Any]:
        if not math.isfinite(poll_seconds) or not 0 < poll_seconds <= 60:
            raise ValueError("poll_seconds must be between zero and 60")
        if timeout_seconds is not None and (not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
            raise ValueError("timeout_seconds must be positive")
        started = time.monotonic()
        latest: dict[str, Any] = {"job_id": _job_id(job_id), "status": "unknown"}
        while True:
            remaining = None if timeout_seconds is None else timeout_seconds - (time.monotonic() - started)
            if remaining is not None and remaining <= 0:
                return {**latest, "detached": True, "reason": "client_wait_timeout"}
            try:
                async with asyncio.timeout(remaining):
                    value = await self.status(job_id)
            except TimeoutError:
                return {**latest, "detached": True, "reason": "client_wait_timeout"}
            latest = value
            if value["status"] in TERMINAL:
                return value
            remaining = None if timeout_seconds is None else timeout_seconds - (time.monotonic() - started)
            if remaining is not None and remaining <= 0:
                return {**value, "detached": True, "reason": "client_wait_timeout"}
            await asyncio.sleep(poll_seconds if remaining is None else min(poll_seconds, remaining))

    async def fetch(self, job_id: str, destination: pathlib.Path) -> dict[str, Any]:
        _job_id(job_id)
        if destination.exists() or destination.is_symlink():
            raise FileExistsError("result destination must be new")
        await self._check()
        descriptor, name = tempfile.mkstemp(prefix=".mesocosm-download-", suffix=".zip", dir=destination.parent)
        os.close(descriptor)
        archive = pathlib.Path(name)
        extracted = False
        try:
            try:
                async with self._http.stream("GET", f"v1/jobs/{job_id}/results") as response:
                    self._status(response)
                    if response.headers.get("content-type", "").split(";")[0] != "application/zip":
                        raise ExecutionError("worker_result_content_type_invalid")
                    size = response.headers.get("content-length")
                    if size is not None and (not size.isdigit() or int(size) > MAX_ARCHIVE_BYTES):
                        raise ExecutionError("worker_result_too_large")
                    total = 0
                    with archive.open("wb") as output:
                        async for chunk in response.aiter_bytes():
                            total += len(chunk)
                            if total > MAX_ARCHIVE_BYTES:
                                raise ExecutionError("worker_result_too_large")
                            output.write(chunk)
            except httpx.HTTPError:
                raise ExecutionError("worker_download_failed; remote results remain available") from None
            metadata = extract_results(archive, destination)
            extracted = True
            job = json.loads((destination / "job.json").read_text())
            if not isinstance(job, dict) or job.get("job_id") != job_id:
                raise ExecutionError("worker_result_identity_mismatch")
            self._record(job, job_id)
            if job["status"] not in TERMINAL:
                raise ExecutionError("worker_result_not_terminal")
            return {"job_id": job_id, "destination": str(destination.resolve()), "digest": metadata["digest"],
                    "sensitive": True, "redacted": False}
        except BaseException:
            if extracted:
                shutil.rmtree(destination)
            raise
        finally:
            archive.unlink(missing_ok=True)
