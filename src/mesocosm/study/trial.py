"""Trials and their records. Every scheduled trial keeps exactly one record; nothing is rewritten.

A failed trial stays failed and counts as a missing observation. It is never replaced by another
attempt, because replacing trials after seeing how they ended would select observations by outcome.
"""

from __future__ import annotations

import os
import pathlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from mesocosm.study.condition import Condition, StudyError
from mesocosm.study.substrate import SubstrateInfo, TrialOutcome


class TrialSpec(BaseModel):
    """One scheduled run of a substrate. Trials of different arms with the same repetition share a seed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    trial_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.=+-]{0,160}$")
    arm: str = Field(min_length=1)
    repetition: int = Field(ge=0)
    seed: int
    condition: Condition


class TrialRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    spec: TrialSpec
    substrate: SubstrateInfo
    status: Literal["completed", "failed"]
    outcome: TrialOutcome | None = None
    error: str | None = None
    started_at: float
    finished_at: float

    @model_validator(mode="after")
    def _one_result(self) -> TrialRecord:
        if (self.status == "completed") != (self.outcome is not None) or (self.status == "failed") != bool(self.error):
            raise ValueError("a completed trial has an outcome and no error; "
                             "a failed trial has an error and no outcome")
        return self


class TrialStore:
    """Trial records under ``root/trials/<trial_id>/record.json``, written once."""

    def __init__(self, root: pathlib.Path) -> None:
        self.root = root

    def workdir(self, trial_id: str) -> pathlib.Path:
        path = self.root / "trials" / trial_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def load(self, trial_id: str) -> TrialRecord | None:
        path = self.root / "trials" / trial_id / "record.json"
        if not path.is_file():
            return None
        try:
            return TrialRecord.model_validate_json(path.read_text(encoding="utf-8"))
        except ValidationError as exc:
            raise StudyError(f"trial record {path} is not a valid record") from exc

    def save(self, record: TrialRecord) -> None:
        path = self.workdir(record.spec.trial_id) / "record.json"
        temporary = path.with_name(f"record.json.{os.getpid()}.tmp")
        temporary.write_text(record.model_dump_json(indent=1), encoding="utf-8")
        try:
            os.link(temporary, path)  # fails if a record exists, so two writers cannot both win
        except FileExistsError as exc:
            raise StudyError(f"trial {record.spec.trial_id} already has a record") from exc
        finally:
            temporary.unlink()
