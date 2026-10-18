"""Render the sandbox topology (ARCHITECTURE.md, Networks and authority) as a Compose file.

Invariants, checked by tests: agents sit on an internal sandbox network only and publish no
ports; the gateway and `control` are the only services that also sit on `edge`. With
`isolate_agents: true` every agent gets its own internal network (`sandbox-<agent_id>`), so agents
cannot reach each other; the gateway and `control` join all of them.
"""

from __future__ import annotations

import pathlib

import yaml

from mesocosm.core.manifest import WorldManifest


def render_compose(manifest: WorldManifest, *, repo_root: pathlib.Path, run_label: str,
                   vllm_port: int = 8000, runs_dir: str = "runs", opencode_version: str = "latest") -> dict:
    spike = str((repo_root / "spike").resolve())
    runs = str((repo_root / runs_dir).resolve())
    network_of = {a.id: f"sandbox-{a.id}" if manifest.isolate_agents else "sandbox" for a in manifest.agents}
    sandboxes = list(dict.fromkeys(network_of.values()))
    services: dict = {
        "gateway": {
            "build": {"context": str(repo_root.resolve()), "dockerfile": "spike/docker/Dockerfile.gateway"},
            "networks": [*sandboxes, "edge"],
            "extra_hosts": ["host.docker.internal:host-gateway"],
            "environment": {
                "MESOCOSM_UPSTREAM": f"http://host.docker.internal:{vllm_port}",
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
            "networks": [*sandboxes, "edge"],
            "volumes": [f"{spike}:/spike", f"{runs}:/runs"],
            "working_dir": "/spike",
            "command": ["sleep", "infinity"],
        },
    }
    for agent in manifest.agents:
        services[agent.id] = {
            "build": {"context": spike, "dockerfile": "docker/Dockerfile.agent",
                      "args": {"OPENCODE_VERSION": opencode_version}},
            "networks": [network_of[agent.id]],
            "environment": {
                "MESOCOSM_AGENT": agent.id,
                "MESOCOSM_RUN": run_label,
                "MESOCOSM_PEERS": ",".join(a.id for a in manifest.agents if a.id != agent.id),
                "MESOCOSM_ISOLATE_AGENTS": "1" if manifest.isolate_agents else "0",
                "MESOCOSM_GATEWAY_URL": "http://gateway:8080",
                "MESOCOSM_GATEWAY_KEY": manifest.gateway.api_key,
                "MESOCOSM_MODEL_ID": manifest.model.id,
                "MESOCOSM_UPSTREAM_PROBE": f"http://host.docker.internal:{vllm_port}/v1/models",
            },
            "volumes": [f"{spike}/workspace/{agent.id}:/work"],
            "depends_on": {"gateway": {"condition": "service_healthy"}},
        }
    return {
        "name": f"mesocosm-{manifest.name}",
        "services": services,
        "networks": {**{n: {"internal": True} for n in sandboxes}, "edge": {}},
    }


def compose_yaml(manifest: WorldManifest, **kwargs) -> str:
    return yaml.safe_dump(render_compose(manifest, **kwargs), sort_keys=False)
