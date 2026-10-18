"""Render the sandbox topology (ARCHITECTURE.md, Networks and authority) as a Compose file.

Invariants, checked by tests: agents sit on an internal sandbox network only and publish no
ports; the gateway and `control` are the only services that also sit on `edge`; the world services
container joins every sandbox network under its hostnames (board, wiki, git, pypi, sites and, when
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

import yaml

from mesocosm.adapters.opencode.client import render_opencode_config
from mesocosm.core.manifest import AgentSpec, WorldManifest

GATEWAY_URL = "http://gateway:8080"          # as agents reach it, on every sandbox network


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


def world_hostnames(manifest: WorldManifest) -> list[str]:
    """The world services' hostnames (short); the agent-facing list, without `services` and `pastebin`."""
    channels = manifest.channel_types()
    offered = (("board", manifest.services.board), ("wiki", True), ("git", bool(manifest.services.git)),
               ("pypi", bool(manifest.services.pypi)), ("sites", True),
               ("pastebin", "pastebin" in channels), ("chat", manifest.services.peer_review.enabled))
    return [n for n, on in offered if on]


def _with_domain(names: list[str], domain: str | None) -> list[str]:
    return names + [f"{n}.{domain}" for n in names] if domain else names


def agent_files(manifest: WorldManifest, run_label: str, gateway_url: str = GATEWAY_URL,
                context_limit: int = 32768) -> dict[str, dict[str, str]]:
    """Per agent, the files mounted into its container: the harness config (`opencode.json`), the git
    identity (`gitconfig`) and, with a persona brief, the harness rules file (`AGENTS.md`)."""
    domain = manifest.services.domain
    out: dict[str, dict[str, str]] = {}
    for agent in manifest.agents:
        config = render_opencode_config(gateway_url=gateway_url, model_id=manifest.model.id,
                                        api_key=agent_api_key(manifest, run_label, agent.id),
                                        provider_id=manifest.gateway.provider_id,
                                        provider_name=manifest.gateway.provider_name, context_limit=context_limit)
        files = {"opencode.json": json.dumps(config, indent=2) + "\n",
                 "gitconfig": (f"[user]\n\tname = {agent.display_name()}\n\temail = {agent.email(domain)}\n"
                               "[init]\n\tdefaultBranch = main\n")}
        if agent.persona and agent.persona.brief:
            files["AGENTS.md"] = agent.persona.brief.rstrip("\n") + "\n"
        out[agent.id] = files
    return out


def write_agent_files(manifest: WorldManifest, run_label: str, files_dir: pathlib.Path, **kwargs) -> list[pathlib.Path]:
    """Write `agent_files` under files_dir/<agent_id>/; returns the paths written."""
    written = []
    for agent_id, files in agent_files(manifest, run_label, **kwargs).items():
        (files_dir / agent_id).mkdir(parents=True, exist_ok=True)
        for name, content in files.items():
            path = files_dir / agent_id / name
            path.write_text(content, encoding="utf-8")
            written.append(path)
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
    }


def render_compose(manifest: WorldManifest, *, repo_root: pathlib.Path, run_label: str,
                   vllm_port: int = 8000, vllm_host: str = "host.docker.internal", runs_dir: str = "runs",
                   opencode_version: str = "1.19.3", manifest_dir: pathlib.Path | None = None,
                   files_dir: pathlib.Path | None = None) -> dict:
    """`vllm_host` is how the gateway container reaches the inference server on the host; rootless Docker
    maps `host-gateway` to the bridge, so there the slirp4netns host address 10.0.2.2 is needed.

    Addresses are fixed per network (172.30.<n>.0/24: gateway .2, services .3, control .4, agent k .10+k)
    so the services can identify callers by address. With `isolate_agents` every agent has its own
    network n = 1..N; otherwise one network n = 0. `files_dir` is where `write_agent_files` put the
    agents' config files (default runs/<run_label>/agents)."""
    repo = repo_root.resolve()
    spike = str(repo / "spike")
    runs = str((repo / runs_dir).resolve())
    manifest_dir = (manifest_dir or repo).resolve()
    files_root = (files_dir or pathlib.Path(runs) / run_label / "agents").resolve()
    domain = manifest.services.domain
    net_index = {a.id: (i + 1 if manifest.isolate_agents else 0) for i, a in enumerate(manifest.agents)}
    network_of = {a.id: f"sandbox-{a.id}" if manifest.isolate_agents else "sandbox" for a in manifest.agents}
    sandboxes = list(dict.fromkeys(network_of.values()))
    subnet_of = {network_of[a.id]: f"172.30.{net_index[a.id]}" for a in manifest.agents}
    agent_ip = {a.id: f"{subnet_of[network_of[a.id]]}.{10 + i}" for i, a in enumerate(manifest.agents)}
    channels = manifest.channel_types()
    services_on = manifest.services.enabled
    # wiki and sites always exist: the image seeds them (spike/world), a manifest may mount its own content
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
                "MESOCOSM_RETURN_TOKEN_IDS": "1" if manifest.observe.activations else "0",
                "MESOCOSM_GATEWAY_HOST": "0.0.0.0",
                "MESOCOSM_CONTROL_KEY": manifest.gateway.api_key,
                "MESOCOSM_AGENT_KEYS": json.dumps(agent_keys(manifest, run_label)),
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
        volumes = [f"{runs}:/runs"]
        if manifest.services.wiki.docs:
            volumes.append(f"{(manifest_dir / manifest.services.wiki.docs).resolve()}:/data/wiki:ro")
        if manifest.services.sites:
            volumes.append(f"{(manifest_dir / manifest.services.sites).resolve()}:/data/sites:ro")
        services["services"] = {
            "build": {"context": str(repo), "dockerfile": "spike/docker/Dockerfile.services",
                      "args": {"PYPI_PACKAGES": " ".join(manifest.services.pypi)}},
            "networks": attach(3, sandboxes, aliases),
            "environment": {
                "MESOCOSM_SERVICES_LOG": f"/runs/{run_label}/services.jsonl",
                "MESOCOSM_AGENT_IPS": json.dumps(dict(sorted((ip, a) for a, ip in agent_ip.items()))),
                "MESOCOSM_PERSONAS": json.dumps({a.id: a.username for a in manifest.agents}),
                "MESOCOSM_CHANNELS": ",".join(c for c in channels if c == "pastebin"),
                "MESOCOSM_GIT_REPOS": ",".join(manifest.services.git),
                "MESOCOSM_WIKI_VISIBILITY": json.dumps((manifest.conditions.get("exposure") or {}).get("wiki", {})),
                "MESOCOSM_ACCOUNTS": json.dumps([a.model_dump() for a in manifest.services.accounts]),
                "MESOCOSM_PEER_REVIEW": json.dumps(manifest.services.peer_review.model_dump()
                                                   if manifest.services.peer_review.enabled else None),
            },
            "volumes": volumes,
        }
    for agent in manifest.agents:
        depends = {"gateway": {"condition": "service_healthy"}}
        if services_on:
            depends["services"] = {"condition": "service_healthy"}
        files = files_root / agent.id
        mounts = [f"{files}/opencode.json:/root/.config/opencode/opencode.json:ro",
                  f"{files}/gitconfig:/root/.gitconfig:ro"]
        if agent.persona and agent.persona.brief:
            mounts.append(f"{files}/AGENTS.md:/root/.config/opencode/AGENTS.md:ro")
        services[agent.id] = {
            "build": {"context": spike, "dockerfile": "docker/Dockerfile.agent",
                      "args": {"OPENCODE_VERSION": opencode_version}},
            "networks": {network_of[agent.id]: {"ipv4_address": agent_ip[agent.id]}},
            # No environment of ours: the agent's config and identity are mounted files (ADR-0019).
            "volumes": [f"{spike}/workspace/{agent.id}:/work", *mounts]
            + (["shared:/shared"] if "shared_dir" in channels else []),
            "depends_on": depends,
            # `up --wait` returns only once the OpenCode server answers; the run manager connects right after
            "healthcheck": {"test": ["CMD", "curl", "-fsS", "http://127.0.0.1:4096/global/health"],
                            "interval": "3s", "timeout": "3s", "retries": 40, "start_period": "10s"},
        }
        if agent.persona and agent.persona.hostname:
            services[agent.id]["hostname"] = agent.persona.hostname
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
