"""Conditions as explicit points in the seven-lever space that a substrate declares.

A substrate lists every switch it can set and the values each switch accepts. A condition assigns one
accepted value to every switch. Anything else is refused with ``rejected_unsupported``; a study never
approximates a switch the environment does not have.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

Lever = Literal["model", "training", "structure", "environment", "experience", "scale", "instructions"]
LEVERS: tuple[Lever, ...] = get_args(Lever)


class StudyError(ValueError):
    """A study, condition or record that the study layer refuses."""


def canonical(value: object) -> str:
    """Stable JSON text, used to compare and hash setting values (``True`` and ``1`` stay distinct)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class Setting(BaseModel):
    """One switch of a substrate.

    ``removal`` names the accepted value that takes this ingredient away (no memory, no feedback, one
    agent), when the environment has one. JSON null is reserved for "no removal value".
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    lever: Lever
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,40}$")
    description: str = Field(min_length=1)
    values: tuple[JsonValue, ...] = Field(min_length=1)
    default: JsonValue
    removal: JsonValue = None

    @model_validator(mode="after")
    def _consistent(self) -> Setting:
        texts = [canonical(value) for value in self.values]
        if any(value is None for value in self.values):
            raise ValueError(f"setting {self.key}: null cannot be a setting value")
        if len(set(texts)) != len(texts):
            raise ValueError(f"setting {self.key}: a value is listed twice")
        if canonical(self.default) not in texts:
            raise ValueError(f"setting {self.key}: default is not an accepted value")
        if self.removal is not None and canonical(self.removal) not in texts:
            raise ValueError(f"setting {self.key}: removal is not an accepted value")
        return self

    @property
    def key(self) -> str:
        return f"{self.lever}.{self.name}"

    def accepts(self, value: JsonValue) -> bool:
        return canonical(value) in {canonical(item) for item in self.values}

    def removes(self, value: JsonValue) -> bool:
        return self.removal is not None and canonical(value) == canonical(self.removal)


class Condition(BaseModel):
    """A complete assignment of values to a substrate's settings, keyed ``lever.name``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    settings: dict[str, JsonValue]

    def replace(self, changes: dict[str, JsonValue]) -> Condition:
        return Condition(settings={**self.settings, **changes})

    @property
    def digest(self) -> str:
        return hashlib.sha256(canonical(self.settings).encode()).hexdigest()


class LeverSpace(BaseModel):
    """Every switch a substrate can set. Levers without a switch here cannot be tested in it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    settings: tuple[Setting, ...]

    @model_validator(mode="after")
    def _unique(self) -> LeverSpace:
        keys = [setting.key for setting in self.settings]
        if len(set(keys)) != len(keys):
            raise ValueError("a setting key is declared twice")
        return self

    def setting(self, key: str) -> Setting:
        for setting in self.settings:
            if setting.key == key:
                return setting
        raise StudyError(f"rejected_unsupported: the substrate has no setting {key!r}")

    def check(self, key: str, value: JsonValue) -> None:
        if not self.setting(key).accepts(value):
            raise StudyError(f"rejected_unsupported: setting {key!r} does not accept {canonical(value)}")

    def condition(self, overrides: dict[str, JsonValue] | None = None) -> Condition:
        """The substrate's defaults with the given overrides, validated."""
        chosen = {setting.key: setting.default for setting in self.settings}
        for key, value in (overrides or {}).items():
            self.check(key, value)
            chosen[key] = value
        return Condition(settings=chosen)

    def validate_condition(self, condition: Condition) -> None:
        declared = {setting.key for setting in self.settings}
        if set(condition.settings) != declared:
            extra, missing = sorted(set(condition.settings) - declared), sorted(declared - set(condition.settings))
            raise StudyError(f"rejected_unsupported: condition settings do not match the substrate "
                             f"(unknown {extra}, missing {missing})")
        for key, value in condition.settings.items():
            self.check(key, value)
