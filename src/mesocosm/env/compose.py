"""Render the sandbox topology (ARCHITECTURE.md, Networks and authority) as a Compose file.

Invariants, checked by tests: agents sit on an internal sandbox network only and publish no
ports; the gateway and `control` are the only services that also sit on `edge`; the world services
container joins every sandbox network under its hostnames (board, wiki, git, pypi, chat, mcp and, when
the channel is enabled, pastebin). With `isolate_agents: true` every agent gets its own internal
network (`sandbox-<agent_id>`), so agents cannot reach each other except through the services.

Nothing in an agent container names the platform (ADR-0019): the agent service carries no
environment variables of ours, its harness config and git identity are files the control plane
renders (`agent_files`) and mounts read-only, it is identified at the gateway by its own API key, and
the egress check is piped in at run time rather than installed.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import pathlib
import shutil

import yaml

from mesocosm.adapters.opencode.action_plugin import render_action_plugin
from mesocosm.adapters.opencode.client import CONTEXT_CONFIG, render_opencode_config
from mesocosm.core.manifest import AgentSpec, WorldManifest
from mesocosm.env.web import WebPolicy, generate_ca, probe_hosts

GATEWAY_URL = "http://gateway:8080"          # as agents reach it, on every sandbox network
MCP_URL = "http://mcp/"                       # the world's own MCP server (services.mcp), server name `team`
PROXY_URL = "http://proxy:3128"               # the web gateway (ADR-0022), the agents' only route out
CA_PATH = "/etc/ssl/certs/corp-ca.pem"        # where the agents find the proxy's CA certificate


def web_policy(manifest: WorldManifest) -> WebPolicy:
    net = manifest.internet
    return WebPolicy(allow=[p.lower() for p in net.allow], deny=[p.lower() for p in net.deny], mode=net.mode,
                     budget_requests=net.budget.requests, budget_bytes=net.budget.bytes,
                     domain=manifest.services.domain)


def proxy_environment(manifest: WorldManifest) -> dict[str, str]:
    """What a workstation behind a corporate proxy carries: proxy variables for every common client and the
    CA bundle variables. Internal hostnames stay direct (`no_proxy`)."""
    internal = ["localhost", "127.0.0.1", "gateway", *world_hostnames(manifest), "services"]
    if manifest.services.domain:
        internal.append("." + manifest.services.domain)
    no_proxy = ",".join(dict.fromkeys(internal))
    env = {"HTTP_PROXY": PROXY_URL, "HTTPS_PROXY": PROXY_URL, "NO_PROXY": no_proxy,
           "SSL_CERT_FILE": CA_PATH, "NODE_EXTRA_CA_CERTS": CA_PATH, "REQUESTS_CA_BUNDLE": CA_PATH,
           "PIP_CERT": CA_PATH, "GIT_SSL_CAINFO": CA_PATH, "CURL_CA_BUNDLE": CA_PATH}
    env.update({k.lower(): v for k, v in list(env.items())[:3]})
    return env


def agent_api_key(manifest: WorldManifest, run_label: str, agent_id: str) -> str:
    """The key an agent presents to the gateway: derived from the control key, the run label and the
    agent id, so the control plane and the gateway agree without sharing state, and an agent cannot
    guess another's."""
    digest = hmac.new(manifest.gateway.api_key.encode("utf-8"), f"{run_label}/{agent_id}".encode(),
                      hashlib.sha256).hexdigest()
    return f"sk-{digest[:40]}"


def agent_keys(manifest: WorldManifest, run_label: str) -> dict[str, dict[str, str]]:
    """What the gateway needs to identify agents by key (MESOCOSM_AGENT_KEYS)."""
    return {agent_api_key(manifest, run_label, a.id): {"agent": a.id, "run": run_label} for a in manifest.agents}


def harness_auth(manifest: WorldManifest, run_label: str, agent_id: str) -> tuple[str, str]:
    """Upstream server authentication, distinct from model and control credentials."""
    identity = json.dumps(["harness", run_label, agent_id], separators=(",", ":")).encode()
    password = hmac.new(manifest.gateway.api_key.encode(), identity, hashlib.sha256).hexdigest()
    return "opencode", password


def world_hostnames(manifest: WorldManifest) -> list[str]:
    """The world services' hostnames (short); the agent-facing list, without `services` and `pastebin`."""
    channels = manifest.channel_types()
    offered = (("board", manifest.services.board), ("wiki", True), ("git", bool(manifest.services.git)),
               ("pypi", bool(manifest.services.pypi)),
               ("pastebin", "pastebin" in channels), ("chat", manifest.services.chat.enabled),
               ("clock", manifest.services.clock.enabled), ("ledger", manifest.services.ledger.enabled),
               ("cache", manifest.services.cache.enabled), ("vault", manifest.services.honeypot.enabled),
               ("mcp", manifest.services.mcp))
    return [n for n, on in offered if on]


def mcp_config(manifest: WorldManifest, agent: AgentSpec) -> dict[str, dict]:
    """The `mcp` section of an agent's harness config: the world's `team` server when enabled, then the
    agent's own servers, every one enabled."""
    out: dict[str, dict] = {}
    if manifest.services.mcp:
        out["team"] = {"type": "remote", "url": MCP_URL, "enabled": True}
    for name, spec in agent.mcp.items():
        out[name] = spec.model_dump(exclude_none=True) | {"enabled": True}
    return out


def _with_domain(names: list[str], domain: str | None) -> list[str]:
    return names + [f"{n}.{domain}" for n in names] if domain else names


def gateway_upstreams(manifest: WorldManifest) -> dict[str, dict[str, str | None]]:
    """MESOCOSM_UPSTREAMS: the models the gateway routes elsewhere than its default inference server (ADR-0029)."""
    return {m.id: {"url": m.upstream, "key_env": m.api_key_env, "kind": m.kind}
            for m in manifest.catalog().values() if m.upstream}


def agent_files(manifest: WorldManifest, run_label: str, gateway_url: str = GATEWAY_URL,
                context_limit: int | None = None) -> dict[str, dict[str, str]]:
    """Per agent, the files mounted into its container: the harness config (`opencode.json`, naming the
    agent's own model and nothing else of the catalog), the git identity (`gitconfig`) and, with a persona
    brief, the harness rules file (`AGENTS.md`)."""
    domain = manifest.services.domain
    out: dict[str, dict[str, str]] = {}
    for agent in manifest.agents:
        model = manifest.model_for(agent)
        config = render_opencode_config(gateway_url=gateway_url, model_id=model.id,
                                        api_key=agent_api_key(manifest, run_label, agent.id),
                                        provider_id=manifest.gateway.provider_id,
                                        provider_name=manifest.gateway.provider_name,
                                        context_limit=context_limit or model.context_limit,
                                        output_limit=model.output_limit, tools=agent.tools or None)
        mcp = mcp_config(manifest, agent)
        if mcp:
            config["mcp"] = mcp
        files = {"opencode.json": json.dumps(config, indent=2) + "\n",
                 "gitconfig": (f"[user]\n\tname = {agent.display_name()}\n\temail = {agent.email(domain)}\n"
                               "[init]\n\tdefaultBranch = main\n")}
        if agent.persona and agent.persona.brief:
            files["AGENTS.md"] = agent.persona.brief.rstrip("\n") + "\n"
        if manifest.judge.monitor.enabled and manifest.judge.monitor.timing == "pre_action":
            files["plugins/approval-gate.js"] = render_action_plugin(
                gateway_url, agent_api_key(manifest, run_label, agent.id),
                min(600, manifest.judge.monitor.decision_timeout_seconds + 5))
        out[agent.id] = files
    return out


def write_agent_files(manifest: WorldManifest, run_label: str, files_dir: pathlib.Path,
                      manifest_dir: pathlib.Path | None = None, web_dir: pathlib.Path | None = None,
                      **kwargs) -> list[pathlib.Path]:
    """Write `agent_files` under files_dir/<agent_id>/, copy the agent's skill directories to
    files_dir/<agent_id>/skills/<name>/ (manifest_dir resolves them) and, with the internet enabled, make the
    web gateway's CA under web_dir/ca (default: next to files_dir); returns the paths written."""
    from mesocosm.integrations.registry import validate_integrations

    validate_integrations(manifest, require_backend="docker")
    written = []
    if manifest.internet.enabled:
        web_dir = web_dir or files_dir.parent / "web"
        written.append(generate_ca(web_dir / "ca", f"{manifest.services.domain or 'corp'} Root CA"))
    for agent in manifest.agents:
        (files_dir / agent.id).mkdir(parents=True, exist_ok=True)
        for name, content in agent_files(manifest, run_label, **kwargs)[agent.id].items():
            path = files_dir / agent.id / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            written.append(path)
        if agent.skills:
            skills_root = files_dir / agent.id / "skills"
            shutil.rmtree(skills_root, ignore_errors=True)
            for rel in agent.skills:
                src = ((manifest_dir or pathlib.Path.cwd()) / rel).resolve()
                shutil.copytree(src, skills_root / src.name)
                written += sorted(p for p in (skills_root / src.name).rglob("*") if p.is_file())
    return written


def egress_env(manifest: WorldManifest, agent: AgentSpec, vllm_host: str = "host.docker.internal",
               vllm_port: int = 8000) -> dict[str, str]:
    """The facts the egress check needs about this agent's world, passed to that one process (never
    to the container's environment)."""
    return {
        "MESOCOSM_GATEWAY_URL": GATEWAY_URL,
        "MESOCOSM_UPSTREAM_PROBE": f"http://{vllm_host}:{vllm_port}/v1/models",
        "MESOCOSM_PEERS": ",".join(a.id for a in manifest.agents if a.id != agent.id),
        "MESOCOSM_ISOLATE_AGENTS": "1" if manifest.isolate_agents else "0",
        "MESOCOSM_SERVICES": ",".join(n for n in world_hostnames(manifest) if n != "pastebin")
        if manifest.services.enabled else "",
        "MESOCOSM_CHANNELS": ",".join(manifest.channel_types()),
        **egress_proxy_env(manifest),
    }


def egress_proxy_env(manifest: WorldManifest) -> dict[str, str]:
    """The web gateway facts for the egress check: the proxy, a host it allows and one it denies."""
    if not manifest.internet.enabled:
        return {"MESOCOSM_PROXY": ""}
    allowed, denied = probe_hosts(web_policy(manifest))
    return {"MESOCOSM_PROXY": PROXY_URL, "MESOCOSM_PROXY_ALLOWED": allowed or "", "MESOCOSM_PROXY_DENIED": denied,
            "MESOCOSM_CA": CA_PATH}


def render_compose(manifest: WorldManifest, *, repo_root: pathlib.Path, run_label: str,
                   vllm_port: int = 8000, vllm_host: str = "host.docker.internal", runs_dir: str = "runs",
                   opencode_version: str = "1.19.3", manifest_dir: pathlib.Path | None = None,
                   files_dir: pathlib.Path | None = None, workspace_root: pathlib.Path | None = None,
                   require_rollout_binding: bool = False) -> dict:
    """`vllm_host` is how the gateway container reaches the inference server on the host; rootless Docker
    maps `host-gateway` to the bridge, so there the slirp4netns host address 10.0.2.2 is needed.

    Addresses are fixed per network (172.30.<n>.0/24: gateway .2, services .3, control .4, agent k .10+k)
    so the services can identify callers by address. With `isolate_agents` every agent has its own
    network n = 1..N; otherwise one network n = 0. `files_dir` is where `write_agent_files` put the
    agents' config files (default runs/<run_label>/agents). `workspace_root` selects the same per-agent
    workspace directories used by the run manager (default spike/workspace)."""
    from mesocosm.integrations.registry import validate_integrations

    validate_integrations(manifest, require_backend="docker")
    repo = repo_root.resolve()
    spike = str(repo / "spike")
    runs = str((repo / runs_dir).resolve())
    manifest_dir = (manifest_dir or repo).resolve()
    files_root = (files_dir or pathlib.Path(runs) / run_label / "agents").resolve()
    workspaces = (workspace_root or repo / "spike" / "workspace").resolve()
    web_root = files_root.parent / "web"
    domain = manifest.services.domain
    net_index = {a.id: (i + 1 if manifest.isolate_agents else 0) for i, a in enumerate(manifest.agents)}
    network_of = {a.id: f"sandbox-{a.id}" if manifest.isolate_agents else "sandbox" for a in manifest.agents}
    sandboxes = list(dict.fromkeys(network_of.values()))
    subnet_of = {network_of[a.id]: f"172.30.{net_index[a.id]}" for a in manifest.agents}
    agent_ip = {a.id: f"{subnet_of[network_of[a.id]]}.{10 + i}" for i, a in enumerate(manifest.agents)}
    channels = manifest.channel_types()
    services_on = manifest.services.enabled
    # The wiki service starts empty; initial pages require an explicit manifest mount.
    aliases = _with_domain(["services"] + world_hostnames(manifest), domain)

    def attach(host_index: int, nets: list[str], aliases: list[str] | None = None) -> dict:
        extra = {"aliases": list(aliases)} if aliases else {}
        return {n: {"ipv4_address": f"{subnet_of[n]}.{host_index}", **extra} for n in nets}

    services: dict = {
        "gateway": {
            "build": {"context": str(repo), "dockerfile": "spike/docker/Dockerfile.gateway"},
            "networks": {**attach(2, sandboxes, [f"gateway.{domain}"] if domain else None), "edge": {}},
            "extra_hosts": ["host.docker.internal:host-gateway"],
            "environment": {
                "MESOCOSM_UPSTREAM": f"http://{vllm_host}:{vllm_port}",
                "MESOCOSM_LOG_DIR": f"/runs/{run_label}",
                "MESOCOSM_RETURN_TOKEN_IDS": "1" if manifest.observe.activations or require_rollout_binding else "0",
                "MESOCOSM_REQUIRE_ROLLOUT_BINDING": "1" if require_rollout_binding else "0",
                "MESOCOSM_GATEWAY_HOST": "0.0.0.0",
                "MESOCOSM_CONTROL_KEY": manifest.gateway.api_key,
                "MESOCOSM_AGENT_KEYS": json.dumps(agent_keys(manifest, run_label)),
                "MESOCOSM_UPSTREAM_KEY": manifest.gateway.upstream_key or manifest.gateway.api_key,
                "MESOCOSM_UPSTREAMS": json.dumps(gateway_upstreams(manifest)),
                "MESOCOSM_TEAM_RESOURCES": json.dumps({"requests": manifest.resources.model_requests,
                    "completion_tokens": manifest.resources.completion_tokens,
                    "concurrency": manifest.resources.model_concurrency}),
                # a model's key is passed through from the environment `docker compose up` runs in, never written
                **{m.api_key_env: f"${{{m.api_key_env}:?{m.api_key_env} is required by model {m.id} in the manifest}}"
                   for m in manifest.catalog().values() if m.api_key_env},
            },
            "volumes": [f"{runs}:/runs"],
            "healthcheck": {
                "test": ["CMD", "python", "-c",
                         "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/health')"],
                "interval": "5s", "timeout": "3s", "retries": 10,
            },
        },
        "control": {
            "build": {"context": spike, "dockerfile": "docker/Dockerfile.control"},
            "networks": {**attach(4, sandboxes), "edge": {}},
            "volumes": [f"{spike}:/spike", f"{runs}:/runs"] + (["shared:/shared:ro"] if "shared_dir" in channels
                                                                 else []),
            "working_dir": "/spike",
            "command": ["sleep", "infinity"],
        },
    }
    if services_on:
        exposure = manifest.conditions.get("exposure") or {}
        if not isinstance(exposure, dict):
            raise ValueError("conditions.exposure must be a mapping")
        volumes = [f"{runs}:/runs"]
        if manifest.services.wiki.docs:
            volumes.append(f"{(manifest_dir / manifest.services.wiki.docs).resolve()}:/data/wiki:ro")
        seed_env = {}
        if manifest.services.seed:          # initial world state, mounted read-only outside /data (ADR-0039)
            volumes.append(f"{(manifest_dir / manifest.services.seed).resolve()}:/seed:ro")
            seed_env["MESOCOSM_SEED_DIR"] = "/seed"
        services["services"] = {
            "build": {"context": str(repo), "dockerfile": "spike/docker/Dockerfile.services",
                      "args": {"PYPI_PACKAGES": " ".join(manifest.services.pypi)}},
            "networks": attach(3, sandboxes, aliases),
            "environment": {
                "MESOCOSM_SERVICES_LOG": f"/runs/{run_label}/services.jsonl",
                "MESOCOSM_CONTROL_KEY": manifest.gateway.api_key,
                "MESOCOSM_SERVICE_LIMITS": json.dumps(manifest.resources.service_requests),
                "MESOCOSM_ACCESS": json.dumps(manifest.services.access.model_dump()
                                              if manifest.services.access is not None else None),
                "MESOCOSM_AGENT_IPS": json.dumps(dict(sorted((ip, a) for a, ip in agent_ip.items()))),
                "MESOCOSM_PERSONAS": json.dumps({a.id: a.username for a in manifest.agents}),
                "MESOCOSM_CHANNELS": ",".join(c for c in channels if c == "pastebin"),
                "MESOCOSM_GIT_REPOS": ",".join(manifest.services.git),
                "MESOCOSM_PROTECTED_REPOS": ",".join(manifest.services.protected_repos),
                "MESOCOSM_MCP": "1" if manifest.services.mcp else "0",
                "MESOCOSM_WIKI_VISIBILITY": json.dumps(exposure.get("wiki", {})),
                "MESOCOSM_ACCOUNTS": json.dumps([a.model_dump() for a in manifest.services.accounts]),
                "MESOCOSM_CHAT": json.dumps(manifest.services.chat.model_dump()
                                            if manifest.services.chat.enabled else None),
                "MESOCOSM_PEER_REVIEW": json.dumps(manifest.services.peer_review.model_dump()
                                                   if manifest.services.peer_review.enabled else None),
                "MESOCOSM_CLOCK": json.dumps(manifest.services.clock.model_dump(exclude={"enabled"}, exclude_none=True)
                                             if manifest.services.clock.enabled else None),
                "MESOCOSM_LEDGER": json.dumps(manifest.services.ledger.model_dump(exclude={"enabled"})
                                              if manifest.services.ledger.enabled else None),
                "MESOCOSM_CACHE": json.dumps(manifest.services.cache.model_dump(exclude={"enabled"})
                                             if manifest.services.cache.enabled else None),
                "MESOCOSM_HONEYPOT": json.dumps(manifest.services.honeypot.model_dump(exclude={"enabled"})
                                                if manifest.services.honeypot.enabled else None),
                **seed_env,
            },
            "volumes": volumes,
        }
    if manifest.internet.enabled:
        command = []
        if manifest.internet.mode == "record":
            command += ["-w", "/logs/flows.mitm"]
        if manifest.internet.mode == "replay":
            command += ["--server-replay", "/logs/replay.mitm", "--set", "server_replay_extra=kill",
                        "--set", "server_replay_reuse=true"]
        web: dict = {
            "build": {"context": str(repo), "dockerfile": "spike/docker/Dockerfile.web"},
            "networks": {**attach(5, sandboxes, _with_domain(["proxy"], domain) if domain else ["proxy"]),
                         "edge": {}},
            "environment": {
                "MESOCOSM_WEB_POLICY": json.dumps(web_policy(manifest).to_dict()),
                "MESOCOSM_AGENT_IPS": json.dumps(dict(sorted((ip, a) for a, ip in agent_ip.items()))),
                "MESOCOSM_WEB_LOG_DIR": "/logs",
            },
            "volumes": [f"{web_root}:/logs", f"{web_root}/ca:/ca"],
        }
        if command:
            web["command"] = command
        if manifest.internet.mode == "replay" and manifest.internet.replay_file:
            web["volumes"].append(f"{(manifest_dir / manifest.internet.replay_file).resolve()}:/logs/replay.mitm:ro")
        services["web"] = web
    for agent in manifest.agents:
        depends = {"gateway": {"condition": "service_healthy"}}
        if services_on:
            depends["services"] = {"condition": "service_healthy"}
        if manifest.internet.enabled:
            depends["web"] = {"condition": "service_healthy"}
        files = files_root / agent.id
        username, password = harness_auth(manifest, run_label, agent.id)
        mounts = [f"{files}/opencode.json:/root/.config/opencode/opencode.json:ro",
                  f"{files}/gitconfig:/root/.gitconfig:ro"]
        if manifest.judge.monitor.enabled and manifest.judge.monitor.timing == "pre_action":
            mounts.append(f"{files}/plugins/approval-gate.js:/root/.config/opencode/plugins/approval-gate.js:ro")
        if agent.persona and agent.persona.brief:
            mounts.append(f"{files}/AGENTS.md:/root/.config/opencode/AGENTS.md:ro")
        if agent.skills:
            mounts.append(f"{files}/skills:/root/.config/opencode/skills:ro")
        if manifest.internet.enabled:
            mounts.append(f"{web_root}/ca/ca.pem:{CA_PATH}:ro")
        services[agent.id] = {
            "cap_drop": ["NET_RAW", "NET_ADMIN"],
            "build": {"context": spike, "dockerfile": "docker/Dockerfile.agent",
                      "args": {"OPENCODE_VERSION": opencode_version}},
            "networks": {network_of[agent.id]: {"ipv4_address": agent_ip[agent.id]}},
            # Standard upstream auth protects the harness from other agents and task sidecars.
            "environment": {"OPENCODE_SERVER_USERNAME": username, "OPENCODE_SERVER_PASSWORD": password,
                            "OPENCODE_CONFIG_CONTENT": CONTEXT_CONFIG},
            "volumes": [f"{workspaces / agent.id}:/work", *mounts]
            + (["shared:/shared"] if "shared_dir" in channels else []),
            "depends_on": depends,
            # `up --wait` returns only once the OpenCode server answers; the run manager connects right after
            "healthcheck": {"test": ["CMD-SHELL", 'curl -fsS -u '
                                     '"$${OPENCODE_SERVER_USERNAME}:$${OPENCODE_SERVER_PASSWORD}" '
                                     'http://127.0.0.1:4096/global/health'],
                            "interval": "3s", "timeout": "3s", "retries": 40, "start_period": "10s"},
        }
        if agent.persona and agent.persona.hostname:
            services[agent.id]["hostname"] = agent.persona.hostname
        if manifest.internet.enabled:
            # standard proxy and CA variables, as on any workstation behind a corporate proxy; nothing of ours
            services[agent.id]["environment"].update(proxy_environment(manifest))
    out = {
        "name": f"mesocosm-{manifest.name}",
        "services": services,
        "networks": {**{n: {"internal": True, "ipam": {"config": [{"subnet": f"{subnet_of[n]}.0/24"}]}}
                        for n in sandboxes}, "edge": {}},
    }
    if "shared_dir" in channels:
        out["volumes"] = {"shared": {}}
    return out


def service_address(manifest: WorldManifest, service: str) -> str:
    """The fixed address of gateway/services/control on the first sandbox network, as the renderer assigns it."""
    index = {"gateway": 2, "services": 3, "control": 4}[service]
    return f"172.30.{1 if manifest.isolate_agents else 0}.{index}"


def compose_yaml(manifest: WorldManifest, **kwargs) -> str:
    return yaml.safe_dump(render_compose(manifest, **kwargs), sort_keys=False)
