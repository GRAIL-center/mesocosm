"""What an agent is told before it designs anything: the original study and the measured baseline.

A reference is what the original study states, field by field, each with the passage that says so or
``null`` where the entry's author found no passage that states it. A profile is what this environment
measures at baseline, for every measure it declares, with the run-to-run spread.
"""

from __future__ import annotations

from collections.abc import Sequence
from statistics import stdev
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from mesocosm.study.card import summarize
from mesocosm.study.condition import Lever
from mesocosm.study.substrate import Measure
from mesocosm.study.trial import TrialRecord


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    citation: str = Field(min_length=1)
    url: str = Field(min_length=1)


class StatedCondition(BaseModel):
    """One condition of the original study. ``passage`` is ``None`` when no passage stating it was found."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    lever: Lever
    name: str = Field(min_length=1)
    value: str | None = None
    passage: str | None = None


class Reported(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    statement: str = Field(min_length=1)
    source: str = Field(min_length=1)


class Reference(BaseModel):
    """The original study of a behavior, as far as its sources state it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    behavior: str = Field(min_length=1)
    sources: tuple[Source, ...] = Field(min_length=1)
    environment: str | None = None
    measure: str | None = None
    conditions: tuple[StatedCondition, ...] = ()
    reported: tuple[Reported, ...] = ()
    mechanism: str | None = None
    retests: tuple[Reported, ...] = ()
    notes: str = ""


def profile(measures: Sequence[Measure], records: Sequence[TrialRecord]) -> dict[str, Any]:
    """Every declared measure over the given trials: n, mean, lowest, highest and standard deviation."""
    out: dict[str, Any] = {}
    for measure in measures:
        values = [value for record in records if record.outcome is not None
                  for value in [record.outcome.measures.get(measure.name)] if value is not None]
        out[measure.name] = {**summarize(values).model_dump(), "sd": stdev(values) if len(values) >= 2 else None,
                             "description": measure.description}
    return out
