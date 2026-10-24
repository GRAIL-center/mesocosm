"""Whole-world execution contract; experiment logic does not depend on a container client."""

from __future__ import annotations

import pathlib
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

PROTOCOL_VERSION: Literal[1] = 1
TERMINAL = frozenset({"completed", "failed", "cancelled", "interrupted", "failed_cleanup"})


class ExecutionBackend(Protocol):
    async def capabilities(self) -> dict[str, Any]: ...
    async def execute(self, job_dir: pathlib.Path, input_dir: pathlib.Path,
                      kind: str, entrypoint: str) -> dict[str, Any]: ...
    async def recover(self, job_dir: pathlib.Path) -> dict[str, Any]: ...


class JobRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol_version: Literal[1] = PROTOCOL_VERSION
    job_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    kind: Literal["world", "experiment"]
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    entrypoint: str
    status: Literal["queued", "running", "cancelling", "completed", "failed", "cancelled",
                    "interrupted", "failed_cleanup"] = "queued"
    created: float
    updated: float
    revision: int = 0
    result_status: str | None = None
    error_type: str | None = None
    cleanup: dict[str, Any] | None = None


class ExecutionError(RuntimeError):
    """An execution request failed without implying that a remote run did not start."""

    def __init__(self, message: str, *, status_code: int | None = None):
        self.status_code = status_code
        super().__init__(message)
