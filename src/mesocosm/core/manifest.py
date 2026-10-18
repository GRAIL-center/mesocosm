"""World manifest: the versioned description of a world (ARCHITECTURE.md, control plane).

Validation follows the operation contract: anything the platform cannot enforce yet is an
error with result `rejected_unsupported`, never a silent approximation. Warnings are for
choices that are allowed but that the researcher should know about.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
import sys
from importlib.metadata import PackageNotFoundError, version
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

SUPPORTED_HARNESSES = {"opencode"}
SUPPORTED_TASK_SOURCES = {"harbor"}
# Conditions the platform enforces today, and where. M3 extends this table.
SUPPORTED_CONDITIONS = {"budget": "gateway"}
# Hidden channels the sandbox can provide (mesocosm.env.services / compose). Presence is verified
# inside every agent container by egress-check and recorded as a receipt.
SUPPORTED_CHANNELS = {"pastebin": "services", "shared_dir": "compose volume"}


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


class WikiSpec(BaseModel):
    docs: str | None = Field(default=None, description="Directory of markdown pages (front matter may set "
                                                       "visible_from / visible_after_seconds); default: the seed wiki")


class ServicesSpec(BaseModel):
    """The shared world inside the sandbox (mesocosm.env.services). Enabled by default."""
    enabled: bool = True
    board: bool = True
    wiki: WikiSpec = Field(default_factory=WikiSpec)
    git: list[str] = Field(default_factory=lambda: ["project"], description="Shared repository names")
    pypi: list[str] = Field(default_factory=lambda: ["requests", "pytest", "rich", "pyyaml"],
                            description="Packages mirrored into the offline index at image build")
    sites: str | None = Field(default=None, description="Directory of mock websites (one subdirectory per site)")


class ChannelSpec(BaseModel):
    type: str


class WorldManifest(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    mode: Literal["comparison", "persistent"] = "comparison"
    model: ModelSpec
    gateway: GatewaySpec
    agents: list[AgentSpec] = Field(min_length=1)
    tasks: list[TaskSpec] = Field(min_length=1)
    memory: Literal["reset", "carry"] = "reset"
    isolate_agents: bool = Field(default=False, description="Put every agent on its own sandbox network so agents "
                                 "cannot reach each other; only the gateway and control span all of them")
    services: ServicesSpec = Field(default_factory=ServicesSpec)
    channels: list[ChannelSpec] = Field(default_factory=list, description="Hidden channels available to agents")
    conditions: dict[str, object] = Field(default_factory=dict)
    observe: ObserveSpec = Field(default_factory=ObserveSpec)

    def channel_types(self) -> list[str]:
        return [c.type for c in self.channels]

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
    for i, ch in enumerate(manifest.channels):
        if ch.type not in SUPPORTED_CHANNELS:
            errors.append(f"rejected_unsupported: channels[{i}] type {ch.type!r}; "
                          f"supported: {sorted(SUPPORTED_CHANNELS)}")
    if len(set(manifest.channel_types())) != len(manifest.channels):
        errors.append("channels: each type at most once")
    if manifest.channels and not manifest.services.enabled and "pastebin" in manifest.channel_types():
        errors.append("rejected_unsupported: the pastebin channel needs services.enabled")
    dirs = (("services.wiki.docs", manifest.services.wiki.docs), ("services.sites", manifest.services.sites))
    for name, value in dirs:
        if value is not None and not (path.parent / value).is_dir():
            errors.append(f"{name}: {(path.parent / value).resolve()} is not a directory")
    for i, repo in enumerate(manifest.services.git):
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,62}", repo):
            errors.append(f"services.git[{i}]: {repo!r} is not a valid repository name")
    if manifest.mode == "persistent":
        errors.append("rejected_unsupported: mode 'persistent' needs the task stream and snapshot manager (M6); "
                      "use mode: comparison")
    if manifest.observe.activations:
        warnings.append("observe.activations=true: capture route is unverified until M5 (ADR-0005)")
    if all(a.budget == Budget() for a in manifest.agents):
        warnings.append("no agent has a budget; runs are bounded only by the task")

    return ValidationReport(ok=not errors, errors=errors, warnings=warnings, resolved=resolved,
                            digest=manifest.digest())
