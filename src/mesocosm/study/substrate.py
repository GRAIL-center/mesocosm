"""The contract between a study and an environment in which a behavior is defined.

A substrate owns one trial: it starts the environment under a condition, lets the agents act, scores
the trial with the environment's own measures and reports the model calls in the order the model
gateway admitted them. The study layer never simulates agents and never scores a trial itself.
"""

from __future__ import annotations

import math
import pathlib
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from mesocosm.study.condition import Condition, LeverSpace

NAME = r"^[a-z][a-z0-9_-]{0,60}$"
SHA256 = r"^[0-9a-f]{64}$"


class SubstrateInfo(BaseModel):
    """Identity of an environment. ``original`` runs the published code; ``reconstruction`` is our own
    implementation of a described setting; ``native_world`` is the Docker/OpenCode task world."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=NAME)
    version: str = Field(min_length=1)
    kind: Literal["original", "reconstruction", "native_world"]
    source: str = Field(min_length=1)


class Measure(BaseModel):
    """A per-trial number the environment reports. At or below ``floor`` the behavior counts as absent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=NAME)
    description: str = Field(min_length=1)
    floor: float = 0.0


class ModelCall(BaseModel):
    """One admitted model call. Digests cover the exact request input and the returned output."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_id: str = Field(min_length=1)
    input_sha256: str = Field(pattern=SHA256)
    output_sha256: str = Field(pattern=SHA256)


class TrialOutcome(BaseModel):
    """What one trial produced. A measure the trial could not determine is ``None``, never zero."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    measures: dict[str, float | None]
    calls: tuple[ModelCall, ...] = ()
    note: str = ""

    @field_validator("measures")
    @classmethod
    def _finite(cls, measures: dict[str, float | None]) -> dict[str, float | None]:
        if any(value is not None and not math.isfinite(value) for value in measures.values()):
            raise ValueError("measures must be finite numbers or null")
        return measures


class Substrate(Protocol):
    """An installed environment. ``run`` must start a fresh environment for every call."""

    info: SubstrateInfo
    space: LeverSpace
    measures: tuple[Measure, ...]

    def run(self, condition: Condition, seed: int, workdir: pathlib.Path) -> TrialOutcome: ...
