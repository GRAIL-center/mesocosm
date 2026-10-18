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
SUPPORTED_CONDITIONS = {"budget": "gateway", "exposure": "services"}
# Hidden channels the sandbox can provide (mesocosm.env.services / compose). Presence is verified
# inside every agent container by egress-check and recorded as a receipt.
SUPPORTED_CHANNELS = {"pastebin": "services", "shared_dir": "compose volume"}
DOMAIN = r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$"


class Budget(BaseModel):
    completion_tokens: int | None = Field(default=None, ge=1)
    prompt_tokens: int | None = Field(default=None, ge=1)
    requests: int | None = Field(default=None, ge=1)


class PersonaSpec(BaseModel):
    """How the agent appears inside the world (ADR-0019): the board and the chat show `username`, commits
    carry `name <email>`, the container may take `hostname`. The platform keeps the agent id for itself."""
    username: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,30}$")
    name: str | None = None
    email: str | None = None
    hostname: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    brief: str | None = Field(default=None, description="Markdown handed to the harness as its rules file "
                                                        "(~/.config/opencode/AGENTS.md): who the agent is on the team")


class AgentSpec(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    harness: str = "opencode"
    url: str | None = Field(default=None, description="Harness server URL; default http://<id>:4096")
    budget: Budget = Field(default_factory=Budget)
    persona: PersonaSpec | None = None

    def harness_url(self) -> str:
        return self.url or f"http://{self.id}:4096"

    @property
    def username(self) -> str:
        return self.persona.username if self.persona else self.id

    def display_name(self) -> str:
        return (self.persona.name if self.persona and self.persona.name else None) or self.username

    def email(self, domain: str | None) -> str:
        if self.persona and self.persona.email:
            return self.persona.email
        return f"{self.username}@{domain or 'sandbox'}"


class TaskSpec(BaseModel):
    source: str = "harbor"
    path: str | None = Field(default=None, description="Task directory (instruction.md, task.toml, tests/)")
    dataset: str | None = None
    name: str | None = None


class ModelSpec(BaseModel):
    id: str = Field(description="Model id the harness requests, e.g. qwen3-4b (the name vLLM serves)")
    hf_repo: str | None = None


class GatewaySpec(BaseModel):
    url: str = Field(description="Gateway URL as seen by the control plane")
    agent_url: str | None = Field(default=None, description="Gateway URL as seen by agents; default = url")
    api_key: str = Field(default="mesocosm-local", description="The control plane's key for the gateway's admin "
                         "endpoints; every agent gets its own key derived from it (ADR-0019)")
    run_label: str | None = Field(default=None, description="Run label the agents' keys are bound to "
                                  "(set when containers start); default = the run id")
    provider_id: str = Field(default="internal", pattern=r"^[a-z0-9][a-z0-9-]{0,30}$",
                             description="Provider id in the harness config (model `<provider_id>/<model id>`)")
    provider_name: str = "Internal LLM API"

    def url_for_agents(self) -> str:
        return self.agent_url or self.url


class CaptureSpec(BaseModel):
    """What `mesocosm activations capture` records for a run (ADR-0016): residual-stream summaries for
    every request at these layers; raw tensors only where the policy says."""
    layers: list[int] = Field(default_factory=list, description="Decoder layer indices; empty = every quarter")
    raw: Literal["flagged", "all", "none"] = "flagged"
    positions: Literal["completion", "all"] = "completion"


class ObserveSpec(BaseModel):
    activations: bool = False      # record token ids at the gateway so activations can be recomputed
    capture: CaptureSpec = Field(default_factory=CaptureSpec)


class StreamSpec(BaseModel):
    """Persistent operation: tasks arrive while the world runs. A directory is polled for new task
    directories; agents take tasks from one shared queue, one agent per task (ADR-0015)."""
    dir: str | None = Field(default=None, description="Directory where task directories appear (relative to "
                                                      "the manifest); the initial `tasks` are queued first")
    poll_seconds: float = Field(default=10.0, gt=0)
    max_tasks: int | None = Field(default=None, ge=1, description="Stop taking tasks after this many")
    max_minutes: float | None = Field(default=None, gt=0, description="Stop taking tasks after this long")
    idle_stop_seconds: float = Field(default=60.0, gt=0, description="An agent ends when no task arrives for this long")


class WikiSpec(BaseModel):
    docs: str | None = Field(default=None, description="Directory of markdown pages (front matter may set "
                                                       "visible_from / visible_after_seconds); default: the seed wiki")


class PeerReviewSpec(BaseModel):
    """The peer-verification protocol after Shi et al. (ADR-0017): a `chat` service with a message
    channel capped per episode, verdicts on the peer's work, a shared reward when verdicts match the
    peers' actual correctness, and runner feedback between episodes."""
    enabled: bool = False
    max_chars: int = Field(default=200, ge=1)
    max_messages: int = Field(default=5, ge=1)
    verdict_review: bool = Field(default=True, description="Feedback says whether each verdict was right")
    reward_feedback: bool = Field(default=True, description="Feedback states the episode reward")
    reward_per_verdict: int = Field(default=10, ge=0)


class ServicesSpec(BaseModel):
    """The shared world inside the sandbox (mesocosm.env.services). Enabled by default."""
    enabled: bool = True
    board: bool = True
    peer_review: PeerReviewSpec = Field(default_factory=PeerReviewSpec)
    wiki: WikiSpec = Field(default_factory=WikiSpec)
    git: list[str] = Field(default_factory=lambda: ["project"], description="Shared repository names")
    pypi: list[str] = Field(default_factory=lambda: ["requests", "pytest", "rich", "pyyaml"],
                            description="Packages mirrored into the offline index at image build")
    sites: str | None = Field(default=None, description="Directory of mock websites (one subdirectory per site)")
    accounts: list[AccountSpec] = Field(default_factory=list)
    domain: str | None = Field(default=None, pattern=DOMAIN,
                               description="Every world hostname also answers as <host>.<domain>; personas' "
                                           "default e-mail domain")


class AccountSpec(BaseModel):
    """A fictional account the services accept (HTTP basic auth). Identity stays address-based; the
    account is recorded as *claimed*. A trap account is one nobody was given: its use is flagged."""
    user: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,62}$")
    password: str = Field(min_length=1)
    trap: bool = False
    note: str | None = Field(default=None, description="Where the credential was planted, for the record")


class ChannelSpec(BaseModel):
    type: str


class WorldManifest(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    mode: Literal["comparison", "persistent"] = "comparison"
    model: ModelSpec
    gateway: GatewaySpec
    agents: list[AgentSpec] = Field(min_length=1)
    tasks: list[TaskSpec] = Field(default_factory=list)
    stream: StreamSpec = Field(default_factory=StreamSpec)
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
        names = [a.username for a in agents]
        taken = sorted({n for n in names if names.count(n) > 1} | {n for n in names if n in ids and n not in
                                                                   [a.id for a in agents if a.username == n]})
        if taken:
            raise ValueError(f"persona usernames must be unique and must not be another agent's id: {taken}")
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
    users = [a.user for a in manifest.services.accounts]
    if len(set(users)) != len(users):
        errors.append("services.accounts: user names must be unique")
    exposure = manifest.conditions.get("exposure")
    if exposure is not None:
        wiki = exposure.get("wiki") if isinstance(exposure, dict) else None
        if not isinstance(wiki, dict) or not all(isinstance(v, (int, float)) and v >= 0 for v in wiki.values()):
            errors.append("conditions.exposure: expected {wiki: {<page slug>: <seconds after start>}}")
        elif not manifest.services.enabled:
            errors.append("rejected_unsupported: conditions.exposure needs services.enabled")
    if manifest.services.peer_review.enabled:
        if len(manifest.agents) != 2:
            errors.append("services.peer_review needs exactly two agents")
        if manifest.mode != "comparison":
            errors.append("rejected_unsupported: services.peer_review runs episodes in lockstep; use mode: comparison")
        if not manifest.services.enabled:
            errors.append("rejected_unsupported: services.peer_review needs services.enabled")
        if manifest.services.board:
            errors.append("services.peer_review: set services.board: false, "
                          "the board would be an unconstrained channel")
    if manifest.mode == "persistent":
        if not manifest.tasks and not manifest.stream.dir:
            errors.append("mode: persistent needs initial tasks or stream.dir")
        if manifest.stream.dir and not (path.parent / manifest.stream.dir).is_dir():
            errors.append(f"stream.dir: {(path.parent / manifest.stream.dir).resolve()} is not a directory")
    else:
        if not manifest.tasks:
            errors.append("tasks: at least one task is required in mode: comparison")
        if manifest.stream.dir:
            errors.append("stream.dir applies to mode: persistent only")
    if manifest.observe.activations:
        warnings.append("observe.activations=true: capture route is unverified until M5 (ADR-0005)")
    if all(a.budget == Budget() for a in manifest.agents):
        warnings.append("no agent has a budget; runs are bounded only by the task")

    return ValidationReport(ok=not errors, errors=errors, warnings=warnings, resolved=resolved,
                            digest=manifest.digest())
