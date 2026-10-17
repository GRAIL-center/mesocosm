"""World manifest: the versioned description of a world (ARCHITECTURE.md, control plane).

Validation follows the operation contract: anything the platform cannot enforce yet is an
error with result `rejected_unsupported`, never a silent approximation. Warnings are for
choices that are allowed but that the researcher should know about.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys
from importlib.metadata import PackageNotFoundError, version
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

SUPPORTED_HARNESSES = {"opencode"}
SUPPORTED_TASK_SOURCES = {"harbor"}
# Conditions the platform enforces today, and where. M3 extends this table.
SUPPORTED_CONDITIONS = {"budget": "gateway"}


class Budget(BaseModel):
    completion_tokens: int | None = Field(default=None, ge=1)
    prompt_tokens: int | None = Field(default=None, ge=1)
    requests: int | None = Field(default=None, ge=1)


class AgentSpec(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    harness: str = "opencode"
    url: str | None = Field(default=None, description="Harness server URL; default http://<id>:4096")
    budget: Budget = Field(default_factory=Budget)

    def harness_url(self) -> str:
        return self.url or f"http://{self.id}:4096"


class TaskSpec(BaseModel):
    source: str = "harbor"
    path: str | None = Field(default=None, description="Task directory (instruction.md, task.toml, tests/)")
    dataset: str | None = None
    name: str | None = None


class ModelSpec(BaseModel):
    id: str = Field(description="Model id the harness requests, e.g. mesocosm-3b")
    hf_repo: str | None = None


class GatewaySpec(BaseModel):
    url: str = Field(description="Gateway URL as seen by the control plane")
    agent_url: str | None = Field(default=None, description="Gateway URL as seen by agents; default = url")
    api_key: str = "mesocosm-local"
    run_label: str | None = Field(default=None, description="X-Mesocosm-Run value the agents' harness config "
                                  "carries (set when containers start); default = the run id")

    def url_for_agents(self) -> str:
        return self.agent_url or self.url


class ObserveSpec(BaseModel):
    activations: bool = False


class WorldManifest(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    mode: Literal["comparison", "persistent"] = "comparison"
    model: ModelSpec
    gateway: GatewaySpec
    agents: list[AgentSpec] = Field(min_length=1)
    tasks: list[TaskSpec] = Field(min_length=1)
    memory: Literal["reset", "carry"] = "reset"
    channels: list[dict] = Field(default_factory=list)
    conditions: dict[str, object] = Field(default_factory=dict)
    observe: ObserveSpec = Field(default_factory=ObserveSpec)

    @field_validator("agents")
    @classmethod
    def _unique_agent_ids(cls, agents: list[AgentSpec]) -> list[AgentSpec]:
        ids = [a.id for a in agents]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate agent ids: {dupes}")
        return agents

    def digest(self) -> str:
        """Content hash of the manifest; recorded in every run bundle."""
        canon = json.dumps(self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canon.encode("utf-8")).hexdigest()


class ValidationReport(BaseModel):
    ok: bool
    errors: list[str]
    warnings: list[str]
    resolved: dict[str, str]
    digest: str | None = None


def _package_version() -> str:
    try:
        return version("mesocosm")
    except PackageNotFoundError:
        return "0.0.0+local"


def load_manifest(path: str | pathlib.Path) -> WorldManifest:
    data = yaml.safe_load(pathlib.Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("manifest must be a YAML mapping")
    return WorldManifest.model_validate(data)


def validate_manifest(path: str | pathlib.Path) -> ValidationReport:
    path = pathlib.Path(path)
    resolved = {"mesocosm": _package_version(), "python": sys.version.split()[0]}
    errors: list[str] = []
    warnings: list[str] = []
    try:
        manifest = load_manifest(path)
    except (ValidationError, ValueError, OSError, yaml.YAMLError) as e:
        return ValidationReport(ok=False, errors=[str(e)], warnings=[], resolved=resolved)

    for agent in manifest.agents:
        if agent.harness not in SUPPORTED_HARNESSES:
            errors.append(f"rejected_unsupported: agent {agent.id!r} harness {agent.harness!r}; "
                          f"supported: {sorted(SUPPORTED_HARNESSES)}")
    for i, task in enumerate(manifest.tasks):
        if task.source not in SUPPORTED_TASK_SOURCES:
            errors.append(f"rejected_unsupported: tasks[{i}] source {task.source!r}; "
                          f"supported: {sorted(SUPPORTED_TASK_SOURCES)}")
            continue
        if task.path is None and task.name is None:
            errors.append(f"tasks[{i}]: either path or dataset+name is required")
        elif task.path is not None:
            task_dir = (path.parent / task.path).resolve()
            if not (task_dir / "instruction.md").is_file():
                errors.append(f"tasks[{i}]: {task_dir} has no instruction.md")
        elif task.dataset is None:
            errors.append(f"tasks[{i}]: name given without dataset")
    for key in manifest.conditions:
        if key not in SUPPORTED_CONDITIONS:
            errors.append(f"rejected_unsupported: condition {key!r}; "
                          f"supported: {sorted(SUPPORTED_CONDITIONS)} (M3 adds more)")
    if manifest.channels:
        errors.append("rejected_unsupported: channels are not implemented until M2")
    if manifest.mode == "persistent" and manifest.memory == "reset":
        warnings.append("persistent mode with memory=reset: state is carried but agent memory is not")
    if manifest.observe.activations:
        warnings.append("observe.activations=true: capture route is unverified until M5 (ADR-0005)")
    if all(a.budget == Budget() for a in manifest.agents):
        warnings.append("no agent has a budget; runs are bounded only by the task")

    return ValidationReport(ok=not errors, errors=errors, warnings=warnings, resolved=resolved,
                            digest=manifest.digest())
