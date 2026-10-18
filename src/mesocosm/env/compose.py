"""Render the sandbox topology (ARCHITECTURE.md, Networks and authority) as a Compose file.

Invariants, checked by tests: agents sit on an internal sandbox network only and publish no
ports; the gateway and `control` are the only services that also sit on `edge`; the world services
container joins every sandbox network under its hostnames (board, wiki, git, pypi, sites and, when
the channel is enabled, pastebin). With `isolate_agents: true` every agent gets its own internal
network (`sandbox-<agent_id>`), so agents cannot reach each other except through the services.
"""

from __future__ import annotations

import json
import pathlib

import yaml

from mesocosm.core.manifest import WorldManifest


def render_compose(manifest: WorldManifest, *, repo_root: pathlib.Path, run_label: str,
                   vllm_port: int = 8000, vllm_host: str = "host.docker.internal", runs_dir: str = "runs",
                   opencode_version: str = "1.19.3", manifest_dir: pathlib.Path | None = None) -> dict:
    """`vllm_host` is how the gateway container reaches the inference server on the host; rootless Docker
    maps `host-gateway` to the bridge, so there the slirp4netns host address 10.0.2.2 is needed.

    Addresses are fixed per network (172.30.<n>.0/24: gateway .2, services .3, control .4, agent k .10+k)
    so the services can identify callers by address. With `isolate_agents` every agent has its own
    network n = 1..N; otherwise one network n = 0."""
    repo = repo_root.resolve()
    spike = str(repo / "spike")
    runs = str((repo / runs_dir).resolve())
    manifest_dir = (manifest_dir or repo).resolve()
    net_index = {a.id: (i + 1 if manifest.isolate_agents else 0) for i, a in enumerate(manifest.agents)}
    network_of = {a.id: f"sandbox-{a.id}" if manifest.isolate_agents else "sandbox" for a in manifest.agents}
    sandboxes = list(dict.fromkeys(network_of.values()))
    subnet_of = {network_of[a.id]: f"172.30.{net_index[a.id]}" for a in manifest.agents}
    agent_ip = {a.id: f"{subnet_of[network_of[a.id]]}.{10 + i}" for i, a in enumerate(manifest.agents)}
    channels = manifest.channel_types()
    services_on = manifest.services.enabled
    offered = (("board", manifest.services.board), ("wiki", True), ("git", bool(manifest.services.git)),
               ("pypi", bool(manifest.services.pypi)), ("sites", manifest.services.sites is not None))
    aliases = ["services"] + [n for n, on in offered if on] + (["pastebin"] if "pastebin" in channels else [])

    def attach(host_index: int, nets: list[str], with_aliases: bool = False) -> dict:
        extra = {"aliases": list(aliases)} if with_aliases else {}
        return {n: {"ipv4_address": f"{subnet_of[n]}.{host_index}", **extra} for n in nets}

    services: dict = {
        "gateway": {
            "build": {"context": str(repo), "dockerfile": "spike/docker/Dockerfile.gateway"},
            "networks": {**attach(2, sandboxes), "edge": {}},
            "extra_hosts": ["host.docker.internal:host-gateway"],
            "environment": {
                "MESOCOSM_UPSTREAM": f"http://{vllm_host}:{vllm_port}",
                "MESOCOSM_LOG_DIR": f"/runs/{run_label}",
                "MESOCOSM_RETURN_TOKEN_IDS": "1" if manifest.observe.activations else "0",
                "MESOCOSM_GATEWAY_HOST": "0.0.0.0",
            },
            "volumes": [f"{runs}:/runs"],
            "healthcheck": {
                "test": ["CMD", "python", "-c",
                         "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/mesocosm/health')"],
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
            "networks": attach(3, sandboxes, with_aliases=True),
            "environment": {
                "MESOCOSM_SERVICES_LOG": f"/runs/{run_label}/services.jsonl",
                "MESOCOSM_AGENT_IPS": json.dumps(dict(sorted((ip, a) for a, ip in agent_ip.items()))),
                "MESOCOSM_CHANNELS": ",".join(c for c in channels if c == "pastebin"),
                "MESOCOSM_GIT_REPOS": ",".join(manifest.services.git),
            },
            "volumes": volumes,
        }
    for agent in manifest.agents:
        depends = {"gateway": {"condition": "service_healthy"}}
        if services_on:
            depends["services"] = {"condition": "service_healthy"}
        services[agent.id] = {
            "build": {"context": spike, "dockerfile": "docker/Dockerfile.agent",
                      "args": {"OPENCODE_VERSION": opencode_version}},
            "networks": {network_of[agent.id]: {"ipv4_address": agent_ip[agent.id]}},
            "environment": {
                "MESOCOSM_AGENT": agent.id,
                "MESOCOSM_RUN": run_label,
                "MESOCOSM_PEERS": ",".join(a.id for a in manifest.agents if a.id != agent.id),
                "MESOCOSM_ISOLATE_AGENTS": "1" if manifest.isolate_agents else "0",
                "MESOCOSM_SERVICES": ",".join(a for a in aliases if a not in ("services", "pastebin")) if services_on
                else "",
                "MESOCOSM_CHANNELS": ",".join(channels),
                "MESOCOSM_GATEWAY_URL": "http://gateway:8080",
                "MESOCOSM_GATEWAY_KEY": manifest.gateway.api_key,
                "MESOCOSM_MODEL_ID": manifest.model.id,
                "MESOCOSM_UPSTREAM_PROBE": f"http://{vllm_host}:{vllm_port}/v1/models",
            },
            "volumes": [f"{spike}/workspace/{agent.id}:/work"] + (["shared:/shared"] if "shared_dir" in channels
                                                                   else []),
            "depends_on": depends,
        }
    out = {
        "name": f"mesocosm-{manifest.name}",
        "services": services,
        "networks": {**{n: {"internal": True, "ipam": {"config": [{"subnet": f"{subnet_of[n]}.0/24"}]}}
                        for n in sandboxes}, "edge": {}},
    }
    if "shared_dir" in channels:
        out["volumes"] = {"shared": {}}
    return out


def compose_yaml(manifest: WorldManifest, **kwargs) -> str:
    return yaml.safe_dump(render_compose(manifest, **kwargs), sort_keys=False)
