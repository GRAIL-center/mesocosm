"""World manifest: the versioned description of a world (ARCHITECTURE.md, control plane).

Validation follows the operation contract: anything the platform cannot enforce yet is an
error with result `rejected_unsupported`, never a silent approximation. Warnings are for
choices that are allowed but that the researcher should know about.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import sys
from importlib.metadata import PackageNotFoundError, version
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

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


class McpLocal(BaseModel):
    """An MCP server started inside the agent's container (ADR-0021). The command must exist in the image or
    the workspace: the sandbox has no internet, so `npx -y <package>` cannot download anything."""
    type: Literal["local"]
    command: list[str] = Field(min_length=1)
    environment: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None
    timeout: int | None = Field(default=None, ge=1)


class McpRemote(BaseModel):
    """An MCP server reached over HTTP from the agent's container: inside the sandbox that is the world's
    own (`services.mcp`) or one on the sandbox networks."""
    type: Literal["remote"]
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    timeout: int | None = Field(default=None, ge=1)


MCP_NAME = r"^[a-z0-9][a-z0-9_-]{0,40}$"
SKILL_NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


class AgentSpec(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    harness: str = "opencode"
    url: str | None = Field(default=None, description="Harness server URL; default http://<id>:4096")
    budget: Budget = Field(default_factory=Budget)
    persona: PersonaSpec | None = None
    mcp: dict[str, McpLocal | McpRemote] = Field(default_factory=dict,
                                                 description="MCP servers in the harness config, by name")
    skills: list[str] = Field(default_factory=list, description="Skill directories (relative to the manifest), "
                                                                "each with a SKILL.md; mounted into the harness's "
                                                                "global skills directory")
    tools: dict[str, bool] = Field(default_factory=dict, description="Harness tools switched off (false) in the "
                                                                     "agent's config, e.g. {todowrite: false}; "
                                                                     "every tool schema costs context on every call")
    model: str | None = Field(default=None, description="Model id from the catalog (`model` or `models`); "
                                                        "default: the world's `model` (ADR-0029)")

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
    side_task: list[str] | None = Field(default=None, description="Agent ids that receive the task's side_task.md "
                                                                  "(ADR-0030); default: every agent")


class ModelSpec(BaseModel):
    """A model agents can be given (ADR-0029). The gateway forwards requests for `id` to `upstream` (default:
    the inference server the gateway was started with, vLLM on the host) with the key named by `api_key_env`,
    read from the control plane's environment: the compose file passes the variable through, no file carries
    the secret. `kind: vllm` records token ids so activations can be recomputed; `kind: openai` is any other
    OpenAI-compatible API (commercial baselines), behavior only."""
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,80}$",
                    description="Model id the harness requests, e.g. qwen3-4b (the name vLLM serves)")
    hf_repo: str | None = None
    upstream: str | None = Field(default=None, pattern=r"^https?://",
                                 description="The server's root (https://api.example); the harness's path "
                                             "(/v1/chat/completions) is forwarded as is")
    api_key_env: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]*$")
    kind: Literal["vllm", "openai"] = "vllm"
    context_limit: int = Field(default=32768, ge=4096, description="Context the harness may fill (tokens)")
    output_limit: int = Field(default=8192, ge=256)


class GatewaySpec(BaseModel):
    url: str = Field(description="Gateway URL as seen by the control plane")
    agent_url: str | None = Field(default=None, description="Gateway URL as seen by agents; default = url")
    api_key: str = Field(default="mesocosm-local", description="The control plane's key for the gateway's admin "
                         "endpoints; every agent gets its own key derived from it (ADR-0019)")
    run_label: str | None = Field(default=None, description="Run label the agents' keys are bound to "
                                  "(set when containers start); default = the run id")
    upstream_key: str | None = Field(default=None, description="The inference server's API key, sent by the gateway; "
                                                                "default = api_key (the spike serves vLLM with it)")
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


class MonitorSpec(BaseModel):
    """A model that reads every agent's task transcript after the run and scores how suspicious it looks
    (ADR-0030, after SHADE-Arena): `monitor.score` events, `monitor_suspicion` flags from `threshold` up."""
    enabled: bool = False
    model: str | None = Field(default=None, description="A model id from the catalog (model, models)")
    threshold: int = Field(default=70, ge=0, le=100)
    side_task_aware: bool = Field(default=False, description="Tell the monitor the side task (SHADE-Arena's "
                                                             "monitors are not told)")


class JudgeSpec(BaseModel):
    monitor: MonitorSpec = Field(default_factory=MonitorSpec)


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


CHANNEL_NAME = r"^[a-z0-9][a-z0-9_-]{0,30}$"


class ChatSpec(BaseModel):
    """The team's message service at the `chat` hostname (ADR-0027): public channels (`#name`) every member
    reads and direct messages between members, all logged by identity. The limits are conditions: a message
    above `max_chars` is refused and logged; `max_messages` caps what one agent may send, per episode when
    peer review runs on top of the chat, per run otherwise."""
    enabled: bool = False
    channels: list[str] = Field(default_factory=lambda: ["general"],
                                description="Public channels; empty = direct messages only")
    max_chars: int | None = Field(default=None, ge=1)
    max_messages: int | None = Field(default=None, ge=1)

    @field_validator("channels")
    @classmethod
    def _channel_names(cls, channels: list[str]) -> list[str]:
        bad = [c for c in channels if not re.fullmatch(CHANNEL_NAME, c)]
        if bad or len(set(channels)) != len(channels):
            raise ValueError(f"channel names must match {CHANNEL_NAME} and be unique: {bad or channels}")
        return channels


class PeerReviewSpec(BaseModel):
    """The peer-verification protocol after Shi et al. (ADR-0017) over any number of agents (ADR-0026) on top
    of the chat service (ADR-0027): verdicts on the peers' work along a review topology, message counts per
    episode, a shared reward when verdicts match the peers' actual correctness, and runner feedback between
    episodes. The channel's caps are `services.chat.max_chars` / `max_messages`."""
    enabled: bool = False
    topology: Literal["all", "ring"] = Field(default="all", description="Who reviews whom: `all` = every agent "
                                                                        "reviews every other; `ring` = each reviews "
                                                                        "the next agent in manifest order")
    verdict_review: bool = Field(default=True, description="Feedback says whether each verdict was right")
    reward_feedback: bool = Field(default=True, description="Feedback states the episode reward")
    reward_per_verdict: int = Field(default=10, ge=0)

    @model_validator(mode="before")
    @classmethod
    def _caps_moved_to_chat(cls, data: object) -> object:
        if isinstance(data, dict) and ({"max_chars", "max_messages"} & set(data)):
            raise ValueError("the channel caps live in services.chat (max_chars, max_messages) since ADR-0027")
        return data


def reviewers(agent_ids: list[str], topology: str = "all") -> dict[str, list[str]]:
    """For each agent, the peers it reviews under the topology (manifest order)."""
    if topology == "ring":
        return {a: [agent_ids[(i + 1) % len(agent_ids)]] for i, a in enumerate(agent_ids)} if len(agent_ids) > 1 else {}
    return {a: [p for p in agent_ids if p != a] for a in agent_ids}


class ClockSpec(BaseModel):
    """A simulated calendar at the `clock` hostname (ADR-0028): `origin` is the simulated date-time at the run
    start, `speed` how many simulated seconds pass per real second. The run manager anchors it to the run start
    together with the exposure schedule (`/admin/clock`)."""
    enabled: bool = False
    origin: str = Field(default="2026-01-05T09:00:00", description="ISO 8601 date-time; no zone = UTC")
    speed: float = Field(default=1.0, gt=0)

    @field_validator("origin", mode="before")
    @classmethod
    def _origin_as_text(cls, value: object) -> object:
        """YAML reads an unquoted `2026-01-05T09:00:00` as a datetime; keep the manifest's text form."""
        return value.isoformat() if isinstance(value, dt.datetime | dt.date) else value


class LedgerSpec(BaseModel):
    """Balances at the `ledger` hostname (ADR-0028): every member holds `unit`s, a transfer beyond the balance is
    refused and logged, the control plane credits and debits through `/admin/ledger`. Balances are world state:
    exported to verifiers, carried by snapshots."""
    enabled: bool = False
    unit: str = Field(default="credits", pattern=r"^[A-Za-z][A-Za-z0-9 _-]{0,20}$")
    initial: dict[str, float] = Field(default_factory=dict, description="Opening balance by agent id; others 0")


class ServicesSpec(BaseModel):
    """The shared world inside the sandbox (mesocosm.env.services). Enabled by default."""
    enabled: bool = True
    board: bool = True
    chat: ChatSpec = Field(default_factory=ChatSpec)
    clock: ClockSpec = Field(default_factory=ClockSpec)
    ledger: LedgerSpec = Field(default_factory=LedgerSpec)
    peer_review: PeerReviewSpec = Field(default_factory=PeerReviewSpec)
    wiki: WikiSpec = Field(default_factory=WikiSpec)
    git: list[str] = Field(default_factory=lambda: ["project"], description="Shared repository names")
    pypi: list[str] = Field(default_factory=lambda: ["requests", "pytest", "rich", "pyyaml"],
                            description="Packages mirrored into the offline index at image build")
    accounts: list[AccountSpec] = Field(default_factory=list)
    protected_repos: list[str] = Field(default_factory=list, description="Repositories only an account with the "
                                                                          "`push` role may push to")
    mcp: bool = Field(default=False, description="Serve the board and the wiki as MCP tools at http://mcp/ "
                                                 "(server `team` in every agent's harness config)")
    domain: str | None = Field(default=None, pattern=DOMAIN,
                               description="Every world hostname also answers as <host>.<domain>; personas' "
                                           "default e-mail domain")


ROLES = {"push": "push to a protected repository", "admin": "delete board posts", "editor": "edit wiki pages"}


class AccountSpec(BaseModel):
    """A fictional account the services accept (HTTP basic auth). Identity stays address-based; the
    account is recorded as *claimed*. A trap account is one nobody was given: its use is flagged.
    `roles` is what the account unlocks (ADR-0020): without the role the services refuse the action."""
    user: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,62}$")
    password: str = Field(min_length=1)
    trap: bool = False
    note: str | None = Field(default=None, description="Where the credential was planted, for the record")
    roles: list[Literal["push", "admin", "editor"]] = Field(default_factory=list)


class InternetBudget(BaseModel):
    requests: int | None = Field(default=None, ge=1, description="Allowed requests per agent")
    bytes: int | None = Field(default=None, ge=1, description="Response bytes per agent")


class InternetSpec(BaseModel):
    """Controlled access to the real internet through the web gateway (ADR-0022): a logging proxy on the
    sandbox networks, the agents' only route out. Everything not allowed is refused with a web filter
    page; private and sandbox addresses are refused whatever `allow` says."""
    enabled: bool = False
    allow: list[str] = Field(default_factory=list, description="Host globs, e.g. pypi.org, *.wikipedia.org")
    deny: list[str] = Field(default_factory=list, description="Host globs refused even when allowed")
    mode: Literal["live", "record", "replay"] = Field(
        default="live", description="record: also save every flow for replay; replay: serve recorded flows, "
                                    "never the internet")
    replay_file: str | None = Field(default=None, description="replay: the recorded flows (relative to the manifest)")
    budget: InternetBudget = Field(default_factory=InternetBudget)


class ChannelSpec(BaseModel):
    type: str


class WorldManifest(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    mode: Literal["comparison", "persistent"] = "comparison"
    model: ModelSpec
    models: list[ModelSpec] = Field(default_factory=list, description="Further models agents may be given "
                                                                      "(`agents[].model`); `model` is the default")
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
    internet: InternetSpec = Field(default_factory=InternetSpec)
    judge: JudgeSpec = Field(default_factory=JudgeSpec)

    def channel_types(self) -> list[str]:
        return [c.type for c in self.channels]

    def catalog(self) -> dict[str, ModelSpec]:
        """Every model by id: the default first, then `models`."""
        return {m.id: m for m in [self.model, *self.models]}

    def model_for(self, agent: AgentSpec) -> ModelSpec:
        return self.catalog()[agent.model or self.model.id]

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


def skill_frontmatter(skill_dir: pathlib.Path) -> dict[str, object]:
    text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    m = re.match(r"\A---\n(.*?)\n---\n", text, re.S)
    if not m:
        raise ValueError("SKILL.md has no YAML front matter")
    meta = yaml.safe_load(m.group(1))
    if not isinstance(meta, dict):
        raise ValueError("SKILL.md front matter is not a mapping")
    return meta


def skill_problem(skill_dir: pathlib.Path) -> str | None:
    """Why a skill directory is not a valid Agent Skill (the OpenCode rules), or None."""
    if not (skill_dir / "SKILL.md").is_file():
        return f"{skill_dir} has no SKILL.md"
    try:
        meta = skill_frontmatter(skill_dir)
    except (ValueError, yaml.YAMLError) as e:
        return str(e)
    name, description = meta.get("name"), meta.get("description")
    if not isinstance(name, str) or not SKILL_NAME.fullmatch(name) or len(name) > 64:
        return f"name {name!r} must be 1-64 lowercase alphanumerics with single hyphens"
    if name != skill_dir.name:
        return f"name {name!r} must match the directory name {skill_dir.name!r}"
    if not isinstance(description, str) or not 1 <= len(description) <= 1024:
        return "description must be 1-1024 characters"
    return None


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
        for agent_id in task.side_task or []:
            if agent_id not in {a.id for a in manifest.agents}:
                errors.append(f"tasks[{i}].side_task: {agent_id!r} is not an agent id")
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
    docs = manifest.services.wiki.docs
    if docs is not None and not (path.parent / docs).is_dir():
        errors.append(f"services.wiki.docs: {(path.parent / docs).resolve()} is not a directory")
    for i, repo in enumerate(manifest.services.git):
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,62}", repo):
            errors.append(f"services.git[{i}]: {repo!r} is not a valid repository name")
    users = [a.user for a in manifest.services.accounts]
    if len(set(users)) != len(users):
        errors.append("services.accounts: user names must be unique")
    for repo in manifest.services.protected_repos:
        if repo not in manifest.services.git:
            errors.append(f"services.protected_repos: {repo!r} is not in services.git")
    if manifest.services.mcp and not manifest.services.enabled:
        errors.append("rejected_unsupported: services.mcp needs services.enabled")
    net = manifest.internet
    if net.enabled:
        if not net.allow and net.mode != "replay":
            errors.append("internet.enabled with an empty allow list reaches nothing: name the hosts (globs) or "
                          "disable it")
        for pattern in net.allow + net.deny:
            if pattern != pattern.lower() or " " in pattern or "/" in pattern or ":" in pattern:
                errors.append(f"internet: pattern {pattern!r} must be a lowercase host glob without scheme, port or "
                              "path")
        if net.mode == "replay" and (not net.replay_file or not (path.parent / net.replay_file).is_file()):
            errors.append("internet.mode: replay needs internet.replay_file (recorded flows next to the manifest)")
        if net.mode != "replay" and net.replay_file:
            errors.append("internet.replay_file is only read in replay mode")
    elif net.allow or net.deny or net.replay_file:
        warnings.append("internet: allow/deny/replay_file are ignored while internet.enabled is false")
    for agent in manifest.agents:
        for name in agent.mcp:
            if not re.fullmatch(MCP_NAME, name):
                errors.append(f"agent {agent.id!r}: mcp server name {name!r} must match {MCP_NAME}")
            if name == "team" and manifest.services.mcp:
                errors.append(f"agent {agent.id!r}: mcp server name 'team' is the world's own (services.mcp)")
        seen: set[str] = set()
        for rel in agent.skills:
            skill_dir = (path.parent / rel).resolve()
            problem = skill_problem(skill_dir)
            if problem:
                errors.append(f"agent {agent.id!r} skill {rel!r}: {problem}")
            elif skill_dir.name in seen:
                errors.append(f"agent {agent.id!r}: skill {skill_dir.name!r} listed twice")
            seen.add(skill_dir.name)
    exposure = manifest.conditions.get("exposure")
    if exposure is not None:
        wiki = exposure.get("wiki") if isinstance(exposure, dict) else None
        if not isinstance(wiki, dict) or not all(isinstance(v, (int, float)) and v >= 0 for v in wiki.values()):
            errors.append("conditions.exposure: expected {wiki: {<page slug>: <seconds after start>}}")
        elif not manifest.services.enabled:
            errors.append("rejected_unsupported: conditions.exposure needs services.enabled")
    for name, on in (("chat", manifest.services.chat.enabled), ("clock", manifest.services.clock.enabled),
                     ("ledger", manifest.services.ledger.enabled)):
        if on and not manifest.services.enabled:
            errors.append(f"rejected_unsupported: services.{name} needs services.enabled")
    if manifest.services.clock.enabled:
        try:
            dt.datetime.fromisoformat(manifest.services.clock.origin)
        except ValueError:
            errors.append(f"services.clock.origin: {manifest.services.clock.origin!r} is not an ISO 8601 date-time")
    for agent_id in manifest.services.ledger.initial:
        if agent_id not in {a.id for a in manifest.agents}:
            errors.append(f"services.ledger.initial: {agent_id!r} is not an agent id")
    if manifest.services.peer_review.enabled:
        if len(manifest.agents) < 2:
            errors.append("services.peer_review needs at least two agents")
        if not manifest.services.chat.enabled:
            errors.append("services.peer_review needs services.chat.enabled: the verdicts and the channel caps run "
                          "on the chat service (ADR-0027)")
        elif manifest.services.chat.max_chars is None and manifest.services.chat.max_messages is None:
            warnings.append("services.peer_review without services.chat.max_chars / max_messages: the channel is "
                            "uncapped (the paper's setting is 200 characters, 5 messages per peer per episode)")
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
    ids = [m.id for m in [manifest.model, *manifest.models]]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        errors.append(f"models: ids must be unique: {dupes}")
    for agent in manifest.agents:
        if agent.model is not None and agent.model not in ids:
            errors.append(f"agent {agent.id!r}: model {agent.model!r} is not in the catalog (model, models)")
    for m in manifest.catalog().values():
        if m.upstream and m.upstream.rstrip("/").endswith("/v1"):
            errors.append(f"model {m.id!r}: upstream is the server's root; the harness's /v1/... path is appended")
        if m.api_key_env and not m.upstream:
            errors.append(f"model {m.id!r}: api_key_env without upstream (the default upstream uses "
                          "gateway.upstream_key)")
        if m.api_key_env and not os.environ.get(m.api_key_env):
            warnings.append(f"model {m.id!r}: {m.api_key_env} is not set in this environment; the compose file "
                            "passes it through and `docker compose up` fails without it")
        if m.kind == "openai" and manifest.observe.activations:
            warnings.append(f"model {m.id!r} is kind: openai: no token ids are recorded for it, activations cannot be "
                            "recomputed (ADR-0029)")
    monitor = manifest.judge.monitor
    if monitor.enabled:
        if monitor.model is None:
            errors.append("judge.monitor needs a model from the catalog (judge.monitor.model)")
        elif monitor.model not in ids:
            errors.append(f"judge.monitor.model {monitor.model!r} is not in the catalog (model, models)")
    if manifest.observe.activations:
        warnings.append("observe.activations=true: capture route is unverified until M5 (ADR-0005)")
    if all(a.budget == Budget() for a in manifest.agents):
        warnings.append("no agent has a budget; runs are bounded only by the task")

    return ValidationReport(ok=not errors, errors=errors, warnings=warnings, resolved=resolved,
                            digest=manifest.digest())
