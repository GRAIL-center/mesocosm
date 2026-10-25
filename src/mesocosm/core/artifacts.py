"""Declared workspace snapshots and an immutable, controller-published artifact journal.

Capture runs before the original verifier. Only named files below the trusted workspace root are
read; verification output and other controller storage must never be passed as that root.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import pathlib
import stat
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, field_validator, model_validator

if TYPE_CHECKING:
    from mesocosm.integrations.tasks import Reward, Task

MAX_ARTIFACT_FILES = 32
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024


def artifact_path(value: str) -> str:
    """Require an exact portable relative path, without implicit normalization or globbing."""
    if (not value or len(value.encode("utf-8")) > 1024 or "\\" in value or ":" in value
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
            or any(part in ("", ".", "..") for part in value.split("/"))):
        raise ValueError("artifact paths must be exact relative paths without traversal")
    return value


class ArtifactSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    paths: list[str] = Field(default_factory=list, max_length=MAX_ARTIFACT_FILES)
    eligibility: Literal["completed", "passing"] = "completed"
    success_threshold: FiniteFloat = 1.0
    show_author: bool = True
    show_score: bool = False
    max_bytes: int = Field(default=1024 * 1024, ge=1, le=MAX_ARTIFACT_BYTES)

    @field_validator("paths")
    @classmethod
    def valid_paths(cls, values: list[str]) -> list[str]:
        for value in values:
            artifact_path(value)
        if len(values) != len(set(values)):
            raise ValueError("artifact paths must be unique")
        return values

    @model_validator(mode="after")
    def meaningful(self) -> ArtifactSpec:
        if self.enabled and not self.paths:
            raise ValueError("enabled artifacts require at least one declared path")
        if self.eligibility == "completed" and self.success_threshold != 1.0:
            raise ValueError("artifact success_threshold only applies to passing eligibility")
        if not self.enabled and (self.paths or self.eligibility != "completed" or self.success_threshold != 1.0
                                 or not self.show_author or self.show_score or self.max_bytes != 1024 * 1024):
            raise ValueError("rejected_unsupported: disabled artifacts cannot configure dormant controls")
        return self


@runtime_checkable
class ArtifactVerifier(Protocol):
    """A verifier that captures stable inputs before grading, without exposing grader output.

    Implementations must acknowledge stopped writers and validate the workspace/input mapping.
    ``on_capture`` persists controller evidence before grading, including on verifier failure.
    A plain ``verify`` implementation cannot provide this contract.
    """

    async def verify_with_artifacts(self, task: Task, workspace: pathlib.Path, logs_dir: pathlib.Path,
                                    spec: ArtifactSpec, *, on_capture: Callable[[dict[str, Any]], None]) -> Reward: ...


def _version(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _read_snapshot(root_fd: int, name: str, remaining: int) -> bytes:
    """Keep every parent open, so a concurrent path replacement cannot redirect a read."""
    descriptors: list[int] = []
    entries: list[tuple[int, str, int, os.stat_result]] = []
    parent_fd = root_fd
    file_opened = False
    try:
        parts = name.split("/")
        for part in parts[:-1]:
            fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
            descriptors.append(fd)
            entries.append((parent_fd, part, fd, os.fstat(fd)))
            parent_fd = fd
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
        descriptors.append(fd)
        file_opened = True
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ValueError(f"artifact must be a regular file with one link: {name}")
        if before.st_size > remaining:
            raise ValueError(f"artifact snapshot exceeds max_bytes: {name}")
        entries.append((parent_fd, parts[-1], fd, before))
        chunks: list[bytes] = []
        count = 0
        while chunk := os.read(fd, min(65536, remaining + 1 - count)):
            chunks.append(chunk)
            count += len(chunk)
            if count > remaining:
                raise ValueError(f"artifact snapshot exceeds max_bytes: {name}")
        try:
            for parent, part, opened, original in entries:
                current = os.stat(part, dir_fd=parent, follow_symlinks=False)
                if _version(original) != _version(os.fstat(opened)) or _version(original) != _version(current):
                    raise ValueError(f"artifact changed during capture: {name}")
        except FileNotFoundError as exc:
            raise ValueError(f"artifact changed during capture: {name}") from exc
        return b"".join(chunks)
    except FileNotFoundError as exc:
        if file_opened:
            raise ValueError(f"artifact changed during capture: {name}") from exc
        # An absent leaf counts as missing only if its already-opened ancestors stayed in place.
        for parent, part, opened, original in entries:
            try:
                current = os.stat(part, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError as changed:
                raise ValueError(f"artifact changed during capture: {name}") from changed
            if _version(original) != _version(os.fstat(opened)) or _version(original) != _version(current):
                raise ValueError(f"artifact changed during capture: {name}") from exc
        raise
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def capture_artifacts(workspace: pathlib.Path, spec: ArtifactSpec) -> dict[str, Any]:
    """Capture immutable UTF-8 content, recording absent declared files as unavailable.

    An unavailable file is not an empty file. Unsafe or unstable inputs reject the capture instead
    of silently yielding an incomplete artifact. The bound is aggregate across declared files.
    """
    if not spec.enabled:
        return {"files": {}, "missing": []}
    files: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    total = 0
    root_fd = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for name in spec.paths:
            try:
                data = _read_snapshot(root_fd, name, spec.max_bytes - total)
            except FileNotFoundError:
                missing.append(name)
                continue
            except OSError as exc:
                reason = "symlink or non-directory" if exc.errno in (errno.ELOOP, errno.ENOTDIR) else "unsafe read"
                raise ValueError(f"artifact {reason}: {name}") from exc
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError(f"artifact is not UTF-8: {name}") from exc
            files[name] = {"text": text, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
            total += len(data)
    finally:
        os.close(root_fd)
    return {"files": files, "missing": missing}


class ArtifactFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(ge=0, le=MAX_ARTIFACT_BYTES)

    @model_validator(mode="after")
    def valid_digest(self) -> ArtifactFile:
        data = self.text.encode("utf-8")
        if self.bytes != len(data) or self.sha256 != hashlib.sha256(data).hexdigest():
            raise ValueError("artifact content does not match its byte count and SHA-256")
        return self


class ArtifactPublication(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_agent: str = Field(min_length=1, max_length=200)
    source_task: str = Field(min_length=1, max_length=200)
    files: dict[str, ArtifactFile] = Field(max_length=MAX_ARTIFACT_FILES)
    missing: list[str] = Field(default_factory=list, max_length=MAX_ARTIFACT_FILES)
    score: FiniteFloat | None = None
    show_author: bool = True
    show_score: bool = False

    @model_validator(mode="after")
    def valid_files(self) -> ArtifactPublication:
        names = [*self.files, *self.missing]
        for name in names:
            artifact_path(name)
        if len(names) > MAX_ARTIFACT_FILES or len(names) != len(set(names)):
            raise ValueError("artifact paths must be unique and bounded")
        if not names:
            raise ValueError("artifact publication requires declared paths")
        if sum(file.bytes for file in self.files.values()) > MAX_ARTIFACT_BYTES:
            raise ValueError("artifact publication exceeds maximum size")
        return self

    def content_id(self) -> str:
        body = json.dumps(self.model_dump(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(body.encode("utf-8")).hexdigest()


class ArtifactStore:
    """Controller-owned append-only storage; a publication ID binds content and provenance."""

    def __init__(self, path: pathlib.Path):
        self.path = path
        self.records: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()
        if path.exists() or path.is_symlink():
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode) or os.fstat(fd).st_nlink != 1:
                    raise ValueError("artifact journal must be a regular file with one link")
                with os.fdopen(fd, "r", encoding="utf-8", closefd=False) as journal:
                    for line in journal:
                        record = json.loads(line)
                        payload = {key: value for key, value in record.items() if key not in ("id", "published_at")}
                        publication = ArtifactPublication.model_validate(payload)
                        if record["id"] != publication.content_id() or record["id"] in self.records:
                            raise ValueError("artifact journal contains inconsistent or duplicate content IDs")
                        self.records[record["id"]] = record
            finally:
                os.close(fd)

    def publish(self, publication: ArtifactPublication) -> dict[str, Any]:
        artifact_id = publication.content_id()
        with self.lock:
            if artifact_id in self.records:
                return {"id": artifact_id, "created": False, "artifact": self.records[artifact_id]}
            record = {"id": artifact_id, "published_at": time.time(), **publication.model_dump()}
            data = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode) or os.fstat(fd).st_nlink != 1:
                    raise ValueError("artifact journal must be a regular file with one link")
                offset = 0
                while offset < len(data):
                    offset += os.write(fd, data[offset:])
                os.fsync(fd)
            finally:
                os.close(fd)
            self.records[artifact_id] = record
            return {"id": artifact_id, "created": True, "artifact": record}

    def visible(self, record: dict[str, Any]) -> dict[str, Any]:
        """Metadata controls do not anonymize the content, paths, or task identity."""
        out = {"id": record["id"], "published_at": record["published_at"], "source_task": record["source_task"],
               "files": {name: {"sha256": file["sha256"], "bytes": file["bytes"]}
                         for name, file in record["files"].items()}, "missing": record["missing"]}
        if record["show_author"]:
            out["author"] = record["source_agent"]
        if record["show_score"]:
            out["score"] = record["score"]
        return out
