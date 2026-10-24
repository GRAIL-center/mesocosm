"""Docker world construction shared by local commands and managed execution workers."""

from __future__ import annotations

import asyncio
import pathlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mesocosm.learning.controller import RolloutContext


def build_components(spec, compose_file: pathlib.Path, runs_root: pathlib.Path, workspace_root: pathlib.Path,
                verifier_image: str | None, vllm_host: str = "host.docker.internal", vllm_port: int = 8000, *,
                verifier_network: str = "none", allow_unenforced_storage: bool = False, task_runtime: str = "native",
                rollout: RolloutContext | None = None):
    """The real components for a manifest: OpenCode servers, the gateway, Docker checks, the world services."""
    from mesocosm.adapters.opencode.client import OpenCodeClient
    from mesocosm.core.run import Components, DockerVerifier
    from mesocosm.env.compose import egress_env, harness_auth, service_address
    from mesocosm.env.egress import DockerEgressVerifier
    from mesocosm.env.services.client import ServicesAdmin
    from mesocosm.env.shared import DockerSharedDir
    from mesocosm.env.stack import DockerStack
    from mesocosm.env.task_runtime import DockerTaskRuntime
    from mesocosm.gateway.client import GatewayAdmin

    egress = DockerEgressVerifier(compose_file, {a.id: egress_env(spec, a, vllm_host, vllm_port) for a in spec.agents})
    if task_runtime not in ("native", "fixture"):
        raise ValueError("--task-runtime must be native or fixture")
    if task_runtime == "native" and verifier_image:
        raise ValueError("native runtime verifies the task container; --verifier-image requires --task-runtime fixture")
    if task_runtime == "native" and spec.gateway.api_key in (
            "", "mesocosm-local", "sk-local", "local", "replace-with-a-fresh-random-control-key"):
        raise ValueError("native runtime requires a non-default gateway control key")
    if task_runtime == "fixture" and not verifier_image:
        raise ValueError("fixture runtime requires an explicit --verifier-image")
    if task_runtime == "fixture" and spec.profile != "fixture":
        raise ValueError("fixture runtime requires profile: fixture")
    native = DockerTaskRuntime(compose_file, runs_root / (spec.gateway.run_label or "spike-docker") / "runtime",
                               verifier_network=verifier_network, allow_unenforced_storage=allow_unenforced_storage,
                               egress=egress, agent_ids=[a.id for a in spec.agents],
                               task_services=spec.task_services, memory=spec.memory
                               ) if task_runtime == "native" else None
    return Components(
        harness_for=lambda a: OpenCodeClient(a.harness_url(), timeout=None,
            auth=harness_auth(spec, spec.gateway.run_label or "spike-docker", a.id)),
        gateway=GatewayAdmin(spec.gateway.url, api_key=spec.gateway.api_key),
        egress=egress,
        verifier=native or DockerVerifier(verifier_image),
        task_runtime=native,
        finish_runtime=native.finish if native is not None else None,
        workspace_for=lambda a: workspace_root / a.id,
        services_log=runs_root / (spec.gateway.run_label or "spike-docker") / "services.jsonl",
        web_log=runs_root / (spec.gateway.run_label or "spike-docker") / "web" / "web.jsonl",
        web_policy=runs_root / (spec.gateway.run_label or "spike-docker") / "web" / "policy.json",
        services=ServicesAdmin(f"http://{service_address(spec, 'services')}", spec.gateway.api_key)
                 if spec.services.enabled else None,
        shared_files=DockerSharedDir(compose_file).list_files,
        monitor=monitor_model(spec) if spec.judge.monitor.enabled else None,
        rollout=rollout,
        workloads=DockerStack(compose_file),
    )


def monitor_model(spec):
    """The monitor's model (ADR-0030): a catalog entry with its own upstream is called directly with the key
    from this environment; the default model goes through the gateway under the monitor's own identity."""
    import os

    from mesocosm.judge.monitor import MonitorModel

    model = spec.catalog()[spec.judge.monitor.model]
    label = spec.gateway.run_label or "spike-docker"
    if model.upstream:
        return MonitorModel(model.upstream.rstrip("/") + "/v1", model.id,
                            api_key=os.environ.get(model.api_key_env) if model.api_key_env else None, run_label=label)
    return MonitorModel(spec.gateway.url.rstrip("/") + "/v1", model.id, api_key=spec.gateway.api_key, run_label=label)


async def run_manifest(manifest: pathlib.Path, compose_file: pathlib.Path, runs_root: pathlib.Path,
                        workspace_root: pathlib.Path, verifier_image: str | None,
                        vllm_host: str = "host.docker.internal", vllm_port: int = 8000, *,
                        verifier_network: str = "none", allow_unenforced_storage: bool = False,
                        task_runtime: str = "native", rollout: RolloutContext | None = None):
    from mesocosm.core.manifest import load_manifest
    from mesocosm.core.run import run_world

    spec = load_manifest(manifest)
    components = build_components(spec, compose_file, runs_root, workspace_root, verifier_image, vllm_host, vllm_port,
                             verifier_network=verifier_network, allow_unenforced_storage=allow_unenforced_storage,
                             task_runtime=task_runtime, rollout=rollout)
    try:
        return await run_world(spec, manifest.resolve(), components, runs_root)
    finally:
        clients = [client for client in (components.gateway, components.services, components.monitor)
                   if client is not None]
        await asyncio.gather(*(client.aclose() for client in clients))


def prepare_stack(manifest: pathlib.Path, compose_dir: pathlib.Path, runs_root: pathlib.Path,
                  workspace_root: pathlib.Path, *, repo_root: pathlib.Path,
                  vllm_port: int = 8000, vllm_host: str = "host.docker.internal",
                  fresh_workspace: bool = False, rollout: RolloutContext | None = None):
    """Render a world before starting it, so its owner can persist recovery information first."""
    from mesocosm.core.manifest import load_manifest
    from mesocosm.env.compose import compose_yaml, write_agent_files
    from mesocosm.env.stack import DockerStack

    spec = load_manifest(manifest)
    label = spec.gateway.run_label or "spike-docker"
    trial_workspace = workspace_root / label if fresh_workspace else workspace_root
    if fresh_workspace and trial_workspace.exists():
        raise FileExistsError(f"trial workspace already exists: {trial_workspace}")
    compose_dir.mkdir(parents=True, exist_ok=True)
    compose_file = compose_dir / f"{manifest.stem}.compose.yml"
    compose_file.write_text(compose_yaml(spec, repo_root=repo_root, run_label=label, vllm_port=vllm_port,
        vllm_host=vllm_host, manifest_dir=manifest.resolve().parent, runs_dir=str(runs_root.resolve()),
        workspace_root=trial_workspace, require_rollout_binding=rollout is not None))
    write_agent_files(spec, label, runs_root.resolve() / label / "agents",
                      manifest_dir=manifest.resolve().parent, web_dir=runs_root.resolve() / label / "web")
    (runs_root / label).mkdir(parents=True, exist_ok=True)
    return DockerStack(compose_file), trial_workspace


def stack_runner(compose_dir: pathlib.Path, runs_root: pathlib.Path, workspace_root: pathlib.Path,
                  verifier_image: str | None, vllm_port: int, vllm_host: str, stacks: list, *,
                  verifier_network: str = "none", allow_unenforced_storage: bool = False, task_runtime: str = "native",
                  fresh_workspace: bool = False, rollout: RolloutContext | None = None,
                  repo_root: pathlib.Path | None = None):
    """run_one for chains and scenarios: render the topology for the manifest (its run label names the log
    directory), take the previous stack down, bring this one up, then run. Every run gets a fresh world."""
    async def run_one(manifest: pathlib.Path):
        if stacks:
            await stacks[-1].down()          # projects are named after the manifest and share the fixed subnets
        stack, trial_workspace = prepare_stack(manifest, compose_dir, runs_root, workspace_root,
            vllm_port=vllm_port, vllm_host=vllm_host, repo_root=repo_root or pathlib.Path.cwd(),
            fresh_workspace=fresh_workspace, rollout=rollout)
        stacks.append(stack)
        await stack.up()
        return await run_manifest(manifest, compose_dir / f"{manifest.stem}.compose.yml", runs_root,
                                   trial_workspace, verifier_image, vllm_host, vllm_port,
                                   verifier_network=verifier_network,
                                   allow_unenforced_storage=allow_unenforced_storage, task_runtime=task_runtime,
                                   rollout=rollout)

    return run_one
