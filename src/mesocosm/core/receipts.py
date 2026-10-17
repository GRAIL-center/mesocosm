"""Receipts: the evidence that a state-changing operation was enforced (docs/operation-contract.md)."""

from __future__ import annotations

import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

Result = Literal["applied", "rejected_unsupported", "rejected_budget", "rejected_policy", "failed"]


class EnforcedAt(BaseModel):
    component: str
    event_id: str = Field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:12]}")
    wallclock: float = Field(default_factory=time.time)


class Verification(BaseModel):
    method: str
    ok: bool
    detail: dict[str, Any] = Field(default_factory=dict)


class Receipt(BaseModel):
    op: str
    run_id: str | None = None
    requested: dict[str, Any]
    previous: dict[str, Any] | None = None
    enforced_at: EnforcedAt
    result: Result
    verification: Verification | None = None
