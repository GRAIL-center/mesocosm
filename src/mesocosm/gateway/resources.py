"""Atomic team admission at the actual model gateway, including streaming leases."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class ResourceExhausted(ValueError):
    pass


@dataclass
class TeamResources:
    requests: int | None = None
    completion_tokens: int | None = None
    concurrency: int | None = None
    used_requests: int = 0
    used_completion_tokens: int = 0
    active: int = 0
    reserved: int = 0
    unknown_usage: bool = False
    leases: dict[str, int] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {"requests": self.used_requests, "completion_tokens": self.used_completion_tokens,
                "active": self.active, "reserved_completion_tokens": self.reserved,
                "unknown_usage": self.unknown_usage}

    def acquire(self, request_id: str, body: dict[str, Any]) -> None:
        """No awaits: check, reserve and admit as one event-loop operation.

        A slot is held until stream completion/disconnection. Output tokens are reserved before
        dispatch; absent usage blocks subsequent token-limited calls instead of refunding guesses.
        """
        if self.requests is not None and self.used_requests >= self.requests:
            raise ResourceExhausted("team_requests")
        if self.concurrency is not None and self.active >= self.concurrency:
            raise ResourceExhausted("team_concurrency")
        reservation = 0
        if self.completion_tokens is not None:
            remaining = self.completion_tokens - self.used_completion_tokens - self.reserved
            if self.unknown_usage or remaining < 1:
                raise ResourceExhausted("team_completion_tokens")
            count = body.get("n", 1)
            if type(count) is not int or count < 1:
                raise ValueError("invalid completion count")
            allowance = remaining // count
            if allowance < 1:
                raise ResourceExhausted("team_completion_tokens")
            keys = [k for k in ("max_tokens", "max_completion_tokens") if k in body] or ["max_tokens"]
            for key in keys:
                value = body.get(key)
                if value is not None and (type(value) is not int or value < 1):
                    raise ValueError("invalid completion limit")
                body[key] = min(value, allowance) if value is not None else allowance
            cap = min(body[k] for k in keys)
            for key in keys:
                body[key] = cap
            reservation = cap * count
        self.leases[request_id] = reservation
        self.used_requests += 1
        self.active += 1
        self.reserved += reservation

    def release(self, request_id: str, usage: dict[str, Any] | None) -> None:
        if request_id not in self.leases:
            return
        reservation = self.leases.pop(request_id)
        self.active -= 1
        self.reserved -= reservation
        consumed = usage.get("completion_tokens") if isinstance(usage, dict) else None
        if type(consumed) is not int or consumed < 0:
            self.unknown_usage = True
        else:
            self.used_completion_tokens += consumed
            if self.completion_tokens is not None and consumed > reservation:
                # A noncompliant upstream cannot silently reset the pool after exceeding its cap.
                self.unknown_usage = True
