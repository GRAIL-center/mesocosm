"""Lightweight training contracts; importing these never loads a GPU library."""

from __future__ import annotations

import hashlib
import json
import pathlib
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

ComputeDType = Literal["bfloat16", "float16"]


class TrainingModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class PolicyIdentity(TrainingModel):
    """Identity of the exact base snapshot and optional adapter used for sampling."""

    id: str = Field(min_length=1)
    model: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,80}$")
    base_model: str = Field(min_length=1)
    base_revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    base_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    dtype: ComputeDType = "bfloat16"
    adapter_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    adapter_path: str | None = None

    @model_validator(mode="after")
    def adapter_identity(self) -> PolicyIdentity:
        if (self.adapter_sha256 is None) != (self.adapter_path is None):
            raise ValueError("adapter_path and adapter_sha256 must be supplied together")
        if self.id != policy_id(self.base_model, self.base_revision, self.base_sha256, self.adapter_sha256, self.dtype):
            raise ValueError("policy id does not match its checkpoint content identity")
        return self


class TrainingSpec(TrainingModel):
    """One frozen world per round, one REINFORCE step, then activate its new adapter.

    The final round evaluates the last update and does not train an unconsumed checkpoint.
    The baseline is a fixed control variate, never an invented task measurement.
    """

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    world: str = Field(min_length=1)
    evaluation_world: str | None = Field(default=None, min_length=1,
                                        description="Separate held-out tasks, evaluated before/after updates")
    episodes: int = Field(default=2, ge=2)
    algorithm: Literal["reinforce"] = "reinforce"
    base_model: str = Field(min_length=1)
    base_revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    dtype: ComputeDType = "bfloat16"
    initial_model: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,80}$")
    server_url: str = Field(pattern=r"^https?://")
    server_api_key_env: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]*$")
    baseline: float = 0.0
    learning_rate: float = Field(default=1e-5, gt=0, le=1)
    lora_rank: int = Field(default=8, ge=1, le=64)
    lora_alpha: int = Field(default=16, ge=1)
    max_grad_norm: float = Field(default=1.0, gt=0)
    max_logprob_error: float = Field(default=0.1, gt=0, le=1)


def load_training(path: str | pathlib.Path) -> TrainingSpec:
    try:
        data = yaml.safe_load(pathlib.Path(path).read_text())
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid training YAML: {exc}") from exc
    return TrainingSpec.model_validate(data)


def policy_id(base_model: str, base_revision: str, base_sha256: str, adapter_sha256: str | None,
              dtype: ComputeDType = "bfloat16") -> str:
    identity = {"base_model": base_model, "base_revision": base_revision,
                "base_sha256": base_sha256, "adapter_sha256": adapter_sha256, "dtype": dtype}
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
