"""A worker-owned Docker world, including durable ownership and verified teardown.

Inputs are portable packages validated by the worker. Resolved manifests and every
Compose file stay outside the submitted package; only the worker talks to Docker.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import pathlib
import re
import secrets
import shutil
import sys
from typing import Any

import yaml

from mesocosm.core.config import absolutize_manifest, deep_merge
from mesocosm.core.experiment import Experiment, load_experiment, record_cleanup_failure, run_experiment
from mesocosm.core.manifest import WorldManifest, load_manifest, validate_manifest
from mesocosm.core.run import RunResult
from mesocosm.env.compose import service_address
from mesocosm.env.lifecycle import CONTAINER_ID
from mesocosm.env.stack import DockerStack, StackCleanupError
from mesocosm.execution import local

INVENTORY = "docker-ownership.json"
TOKEN = re.compile(r"worker-[a-f0-9]{16}")
VOLUME_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


def _json(path: pathlib.Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2))
    temporary.chmod(0o600)
    temporary.replace(path)


def _yaml(path: pathlib.Path, value: Any) -> None:
    path.write_text(yaml.safe_dump(value, sort_keys=False))
    path.chmod(0o600)


def _inside(root: pathlib.Path, path: pathlib.Path) -> pathlib.Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError("execution path escapes its owned directory")
    return resolved


async def _remove_owned_containers(stack: DockerStack, project: str) -> dict[str, Any]:
    """Include task staging and verifiers, which are created outside Compose."""
    query = ("container", "ls", "--all", "--quiet", "--no-trunc", "--filter",
             f"label=com.docker.compose.project={project}")
    containers = (await stack._docker(*query, timeout=30)).split()
    for container in containers:
        if not CONTAINER_ID.fullmatch(container):
            raise ValueError("Docker ownership inventory returned an invalid container ID")
        info = json.loads(await stack._docker("container", "inspect", container, timeout=30))
        if len(info) != 1 or info[0].get("Id") != container or (
                info[0].get("Config", {}).get("Labels", {}).get("com.docker.compose.project") != project):
            raise ValueError("Docker container ownership could not be verified")
    if containers:
        await stack._docker("container", "rm", "-f", "-v", *containers, timeout=120)
    if (await stack._docker(*query, timeout=30)).split():
        raise ValueError("owned containers remain after removal")
    return {"method": "project_label_inspect_remove_and_inventory", "project": project,
            "removed_containers": containers, "removed_container_count": len(containers), "remaining_containers": []}


async def _remove_owned_resources(stack: DockerStack, project: str) -> dict[str, Any]:
    """Recover labelled leftovers omitted by an interrupted runtime config transition."""
    evidence: dict[str, Any] = {"method": "project_label_inspect_remove_and_inventory", "project": project}
    for kind in ("volume", "network"):
        query = (kind, "ls", "--quiet", *(["--no-trunc"] if kind == "network" else []),
                 "--filter", f"label=com.docker.compose.project={project}")
        objects = (await stack._docker(*query, timeout=30)).split()
        for selected in objects:
            if not (VOLUME_NAME if kind == "volume" else CONTAINER_ID).fullmatch(selected):
                raise ValueError(f"Docker ownership inventory returned an invalid {kind} identity")
            info = json.loads(await stack._docker(kind, "inspect", selected, timeout=30))
            identity = "Name" if kind == "volume" else "Id"
            if (len(info) != 1 or info[0].get(identity) != selected
                    or info[0].get("Labels", {}).get("com.docker.compose.project") != project
                    or (kind == "network" and info[0].get("Name") in ("bridge", "host", "none"))):
                raise ValueError(f"Docker {kind} ownership could not be verified")
        if objects:
            await stack._docker(kind, "rm", *objects, timeout=120)
        if (await stack._docker(*query, timeout=30)).split():
            raise ValueError(f"owned {kind}s remain after removal")
        evidence[f"removed_{kind}s"] = objects
        evidence[f"removed_{kind}_count"] = len(objects)
        evidence[f"remaining_{kind}s"] = []
    return evidence


async def _complete_cleanup(stack: DockerStack, project: str) -> bool:
    """A second cancellation cannot abandon the one owned teardown operation."""
    async def teardown() -> None:
        owned = await _remove_owned_containers(stack, project)
        down_error: BaseException | None = None
        try:
            await stack.down()
        except BaseException as exc:
            down_error = exc
        try:
            resources = await _remove_owned_resources(stack, project)
        except BaseException as resource_error:
            if down_error is not None:
                raise down_error from resource_error
            raise
        stack.cleanup_evidence = {**(stack.cleanup_evidence or {}), "owned_container_cleanup": owned,
                                  "owned_resource_cleanup": resources}
        if down_error is not None:
            raise down_error
    cleanup = asyncio.create_task(teardown())
    interrupted = False
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            interrupted = True
    cleanup.result()
    return interrupted


class DockerBackend:
    """One managed world at a time; the worker holds the host-wide scheduling lock."""

    def __init__(self, repo_root: pathlib.Path, vllm_host: str = "host.docker.internal", vllm_port: int = 8000,
                 model_key_envs: tuple[str, ...] = ()):
        self.repo_root = repo_root.resolve()
        self.vllm_host = vllm_host
        self.vllm_port = vllm_port
        self.model_key_envs = frozenset(model_key_envs)

    async def capabilities(self) -> dict[str, Any]:
        """Check deployment prerequisites; per-task PID and egress proofs still gate execution."""
        common = {"backend": "docker", "kinds": ["world", "experiment"], "minimum_agents": 3,
                  "concurrent_jobs": 1, "containment": "verified_before_task_execution"}
        reason = None
        if sys.platform != "linux" or not pathlib.Path("/proc/self/ns/pid").exists():
            reason = "worker_requires_linux_procfs"
        elif shutil.which("docker") is None:
            reason = "docker_executable_missing"
        elif not (self.repo_root / "spike/docker/Dockerfile.gateway").is_file():
            reason = "worker_repository_build_files_missing"
        if reason:
            return {**common, "status": "unavailable", "reason": reason}
        docker = DockerStack(self.repo_root / "unused.compose.yml")
        try:
            info = json.loads(await docker._docker("info", "--format", "{{json .}}", timeout=15))
            if info.get("OSType") != "linux":
                raise ValueError("Docker daemon must run Linux containers")
            context_name = os.environ.get("DOCKER_CONTEXT")
            endpoint = os.environ.get("DOCKER_HOST") if not context_name else None
            if not endpoint:
                selected = (context_name,) if context_name else ()
                context = json.loads(await docker._docker("context", "inspect", *selected, timeout=15))
                endpoint = context[0]["Endpoints"]["docker"]["Host"]
            if not endpoint.startswith("unix://"):
                raise ValueError("worker requires a local Unix Docker socket")
            await docker._docker("compose", "version", "--short", timeout=15)
        except (OSError, RuntimeError, ValueError, KeyError, IndexError, TimeoutError) as exc:
            return {**common, "status": "unavailable", "reason": "docker_preflight_failed",
                    "error_type": type(exc).__name__}
        return {**common, "status": "supported", "reason": "worker_prerequisites_available"}

    def _bind(self, raw: dict[str, Any], base: pathlib.Path, label: str, key: str) -> dict[str, Any]:
        data = copy.deepcopy(raw)
        absolutize_manifest(data, base)
        spec = WorldManifest.model_validate(data)
        if spec.profile != "research" or spec.mode != "comparison" or spec.containment != "required":
            raise ValueError("rejected_unsupported: worker requires research comparison with required containment")
        if len(spec.agents) < 3:
            raise ValueError("rejected_unsupported: worker worlds require at least three real agents")
        if spec.stream.dir:
            raise ValueError("rejected_unsupported: worker jobs must have finite task inputs")
        for model in spec.catalog().values():
            if model.api_key_env and model.api_key_env not in self.model_key_envs:
                raise ValueError(f"model credential is not allowed by worker: {model.api_key_env}")
            if model.api_key_env and not os.environ.get(model.api_key_env):
                raise ValueError(f"worker model credential is unavailable: {model.api_key_env}")
        data["name"] = label
        gateway = data.setdefault("gateway", {})
        gateway.update(url=f"http://{service_address(spec, 'gateway')}:8080", api_key=key,
                       upstream_key=None, run_label=label)
        for index, agent in enumerate(data["agents"]):
            subnet = index + 1 if spec.isolate_agents else 0
            agent["url"] = f"http://172.30.{subnet}.{10 + index}:4096"
        return data

    def _prepare(self, job: pathlib.Path, inputs: pathlib.Path, kind: str, entrypoint: str,
                 token: str) -> pathlib.Path:
        if kind not in ("world", "experiment"):
            raise ValueError(f"rejected_unsupported: worker job kind {kind}")
        entry = _inside(inputs, inputs / entrypoint)
        if not entry.is_file():
            raise ValueError("job entrypoint does not exist")
        resolved = job / "resolved"
        resolved.mkdir(mode=0o700, exist_ok=False)
        key = secrets.token_urlsafe(32)
        if kind == "world":
            data = self._bind(load_manifest(entry).model_dump(mode="json"), entry.parent, token, key)
            destination = resolved / "world.yaml"
            _yaml(destination, data)
        else:
            experiment = load_experiment(entry)
            base_path = _inside(inputs, entry.parent / experiment.base)
            original = load_manifest(base_path).model_dump(mode="json")
            base = self._bind(original, base_path.parent, token, key)
            _yaml(resolved / "base.yaml", base)
            conditions = []
            for condition in experiment.conditions:
                merged = deep_merge(original, condition.patch)
                bound = self._bind(merged, base_path.parent, token, key)
                patch = copy.deepcopy(condition.patch)
                absolutize_manifest(patch, base_path.parent)
                # Execution endpoints and identity are worker-owned, even when a condition
                # changes agent count or isolation. Other condition fields retain their scope.
                patch["agents"] = bound["agents"]
                patch["gateway"] = {**patch.get("gateway", {}), **bound["gateway"]}
                patch["gateway"].pop("run_label", None)
                conditions.append({"name": condition.name, "patch": patch})
            destination = resolved / "experiment.yaml"
            _yaml(destination, {**experiment.model_dump(mode="json"), "name": token,
                                "base": "base.yaml", "conditions": conditions})
        return destination

    async def execute(self, job_dir: pathlib.Path, input_dir: pathlib.Path, kind: str,
                      entrypoint: str) -> dict[str, Any]:
        job, inputs = job_dir.resolve(), input_dir.resolve()
        _inside(job, inputs)
        if (job / INVENTORY).exists():
            raise FileExistsError("job already has Docker ownership; recover it instead of rerunning")
        job.mkdir(parents=True, exist_ok=True, mode=0o700)
        token = "worker-" + secrets.token_hex(8)
        inventory: dict[str, Any] = {"version": 1, "token": token, "worlds": []}
        _json(job / INVENTORY, inventory)
        result: dict[str, Any] = {"status": "failed", "kind": kind, "bundles": [],
                                  "cleanup": {"status": "complete", "worlds": []}}

        async def run_one(manifest: pathlib.Path) -> RunResult:
            if result["cleanup"]["status"] == "failed":
                previous = inventory["worlds"][-1]
                raise StackCleanupError(previous["compose"], previous["cleanup_error"])
            spec = load_manifest(manifest)
            report = validate_manifest(manifest)
            if not report.ok:
                raise ValueError(f"invalid worker world: {report.errors}")
            stack, workspace = local.prepare_stack(manifest, job / "compose", job / "runs", job / "workspaces",
                repo_root=self.repo_root, vllm_host=self.vllm_host, vllm_port=self.vllm_port, fresh_workspace=True)
            compose = pathlib.Path(stack.compose_file)
            compose.chmod(0o600)
            record: dict[str, Any] = {"compose": compose.relative_to(job).as_posix(),
                                      "project": f"mesocosm-{spec.name}", "status": "starting"}
            inventory["worlds"].append(record)
            _json(job / INVENTORY, inventory)  # Recovery knows ownership before Docker can create anything.
            run = None
            error: BaseException | None = None
            try:
                await stack.up()
                record["status"] = "running"
                _json(job / INVENTORY, inventory)
                run = await local.run_manifest(manifest, compose, job / "runs", workspace, None,
                    self.vllm_host, self.vllm_port)
                result["bundles"].append(str(run.bundle_path))
            except BaseException as exc:
                error = exc
                raise
            finally:
                interrupted = False
                try:
                    interrupted = await _complete_cleanup(stack, record["project"])
                    record.update(status="cleaned", cleanup=getattr(stack, "cleanup_evidence", None))
                except BaseException as exc:
                    record.update(status="failed_cleanup", cleanup_error=str(exc),
                                  execution_error=str(error) if error else None,
                                  cleanup=getattr(stack, "cleanup_evidence", None))
                    result["cleanup"]["status"] = "failed"
                    if run is None:
                        raise StackCleanupError(str(compose), str(exc)) from exc
                finally:
                    _json(job / INVENTORY, inventory)
                    result["cleanup"]["worlds"] = inventory["worlds"]
                if interrupted and error is None:
                    raise asyncio.CancelledError
            assert run is not None
            return run

        try:
            manifest = self._prepare(job, inputs, kind, entrypoint, token)
            capability = await self.capabilities()
            if capability["status"] != "supported":
                result.update(status="failed", reason="worker_unavailable", error=capability)
                return result
            if kind == "world":
                run = await run_one(manifest)
                result.update(status="ok" if run.status == "ok" else "failed", run_status=run.status,
                              run_id=run.run_id, bundle=str(run.bundle_path), rewards=run.rewards, error=run.error)
            else:
                experiment = Experiment.model_validate(yaml.safe_load(manifest.read_text()))
                summary = await run_experiment(experiment, manifest, job / "experiment", run_one)
                if result["cleanup"]["status"] == "failed":
                    previous = inventory["worlds"][-1]
                    summary = record_cleanup_failure(job / "experiment",
                        StackCleanupError(previous["compose"], previous["cleanup_error"]), "world_cleanup")
                success = summary["status"] == "completed" and not any(summary.get(key) for key in
                    ("failed", "incomplete_records", "not_run", "stopped", "lifecycle_errors"))
                result.update(status="ok" if success else "failed", experiment=summary,
                              experiment_path=str(job / "experiment"))
        except asyncio.CancelledError:
            result["status"] = "cancelled"
        except Exception as exc:
            result.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        finally:
            if result["cleanup"]["status"] == "failed":
                result["status"] = "failed_cleanup"
            _json(job / "execution-result.json", result)
        return result

    async def recover(self, job_dir: pathlib.Path) -> dict[str, Any]:
        """Remove only recorded owned projects; an interrupted observation is never restarted."""
        job = job_dir.resolve()
        path = job / INVENTORY
        if not path.exists():
            return {"status": "complete", "worlds": []}
        inventory = json.loads(path.read_text())
        token = inventory.get("token", "")
        if inventory.get("version") != 1 or not TOKEN.fullmatch(token):
            raise ValueError("invalid Docker ownership inventory")
        errors = []
        for record in inventory["worlds"]:
            compose = _inside(job / "compose", job / record["compose"])
            data = yaml.safe_load(compose.read_text())
            project = record["project"]
            # Experiments generate labels from name[:20], a nonce and a trial index.
            owned = project == f"mesocosm-{token}" or bool(re.fullmatch(
                re.escape(f"mesocosm-{token[:20]}") + r"-[a-f0-9]{12}-t[0-9]{4,}", project))
            if not owned or data.get("name") != project:
                raise ValueError("recovery Compose project does not match recorded job ownership")
            try:
                stack = DockerStack(compose)
                await _complete_cleanup(stack, project)
                record.update(status="cleaned", cleanup=stack.cleanup_evidence)
                record.pop("cleanup_error", None)
            except Exception as exc:
                record.update(status="failed_cleanup", cleanup_error=str(exc))
                errors.append(str(exc))
            _json(path, inventory)
        if errors:
            raise StackCleanupError(str(path), "; ".join(errors))
        return {"status": "complete", "worlds": inventory["worlds"]}
