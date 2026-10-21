"""Alternate complete real worlds with policy updates; the last world evaluates the new policy."""

from __future__ import annotations

import asyncio
import os
import pathlib
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import yaml

from mesocosm.core.config import absolutize_manifest
from mesocosm.core.manifest import WorldManifest, validate_manifest
from mesocosm.learning.checkpoints import initial_policy, write_json
from mesocosm.learning.config import PolicyIdentity, TrainingSpec
from mesocosm.learning.serving import VLLMPolicyServer

if TYPE_CHECKING:
    from mesocosm.core.run import RunResult


@dataclass(frozen=True)
class RolloutContext:
    policy: PolicyIdentity
    episode_id: str


TrainingRun = Callable[[pathlib.Path, RolloutContext], Awaitable["RunResult"]]


def training_world(spec: TrainingSpec, path: pathlib.Path) -> WorldManifest:
    base_path = (path.resolve().parent / spec.world).resolve()
    try:
        data = yaml.safe_load(base_path.read_text())
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid training world YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("training world must be a YAML mapping")
    absolutize_manifest(data, base_path.parent)
    world = WorldManifest.model_validate(data)
    report = validate_manifest(base_path)
    if not report.ok:
        raise ValueError(f"invalid training world: {report.errors}")
    if len(world.agents) < 3 or world.mode != "comparison" or world.memory != "reset":
        raise ValueError("rejected_unsupported: training requires >=3 agents, comparison mode and reset memory")
    if not world.rewards.enabled or world.learning.mode != "none":
        raise ValueError("training requires measured rewards enabled and context feedback learning.mode=none")
    if world.services.peer_review.enabled and world.services.peer_review.protocol == "legacy":
        raise ValueError("rejected_unsupported: legacy peer-review episodes cannot supply training rollouts")
    if world.observe.activations:
        raise ValueError("rejected_unsupported: separate activation capture is not integrated with training")
    if not world.tasks or any(task.source != "harbor" for task in world.tasks):
        raise ValueError("training requires tasks supported by the Harbor adapter")
    for agent in world.agents:
        model = world.model_for(agent)
        if model.id != spec.initial_model or model.kind != "vllm" or model.upstream:
            raise ValueError("training agents must share initial_model on the default vLLM upstream")
    return world


async def _finish_gpu_worker(function: Callable[..., Any], *args: Any,
                             on_error: Callable[[BaseException, str], None]) -> Any:
    """Cancellation cannot wake inference while a non-cancellable GPU thread still owns memory."""
    worker = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        try:
            worker.result()
        except BaseException as exc:
            on_error(exc, "worker_after_cancellation")
        raise


async def run_training(spec: TrainingSpec, path: pathlib.Path, out: pathlib.Path,
                       run_one: TrainingRun) -> dict[str, Any]:
    """run_one must return only after its world is stopped and teardown succeeds.

    No optimizer runs after an incomplete world, missing provenance, failed cleanup, or zero advantage.
    Failed episodes and activation errors remain in training.json and are never retried for a score.
    """
    from mesocosm.learning.collect import collect_rollouts
    from mesocosm.learning.training import reinforce_update, verify_rollout_policy

    world = training_world(spec, path)
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    write_json(out / "training-spec.json", spec.model_dump(mode="json"))
    result: dict[str, Any] = {"status": "running", "algorithm": spec.algorithm,
                              "episodes": [], "updates": [], "serving": [], "errors": [],
                              "performance_improvement_demonstrated": False}

    def save() -> None:
        write_json(out / "training.json", result)

    recorded_errors: set[int] = set()

    def record_error(exc: BaseException, phase: str) -> None:
        if id(exc) not in recorded_errors:
            result["errors"].append({"phase": phase, "type": type(exc).__name__, "error": str(exc)})
            recorded_errors.add(id(exc))
        save()

    server = VLLMPolicyServer(spec.server_url, os.environ.get(spec.server_api_key_env)
                              if spec.server_api_key_env else None)
    invocation = secrets.token_hex(6)
    save()
    try:
        policy = await asyncio.to_thread(initial_policy, spec)
        result["initial_policy"] = policy.model_dump(mode="json")
        result["serving"].append(await server.verify(policy))
        save()
        for index in range(1, spec.episodes + 1):
            label = f"{spec.name[:20]}-{invocation}-e{index:04d}"
            data = world.model_dump(mode="json")
            data["name"] = label
            data["gateway"]["run_label"] = label
            models = [data["model"], *data["models"]]
            for model in models:
                if model["id"] == spec.initial_model:
                    model["id"] = policy.model
            for agent in data["agents"]:
                agent["model"] = policy.model
            # Keep a monitor on the unchanged base model while updating the agent policy.
            if data["judge"]["monitor"]["model"] == spec.initial_model and policy.model != spec.initial_model:
                data["models"].append(world.catalog()[spec.initial_model].model_dump(mode="json"))
            manifest = WorldManifest.model_validate(data)
            manifest_path = out / f"episode-{index:04d}.yaml"
            manifest_path.write_text(yaml.safe_dump(manifest.model_dump(mode="json"), sort_keys=False))
            report = validate_manifest(manifest_path)
            if not report.ok:
                raise ValueError(f"invalid generated training world: {report.errors}")
            episode: dict[str, Any] = {"index": index, "episode_id": label,
                                       "policy": policy.model_dump(mode="json"), "status": "running",
                                       "manifest": str(manifest_path)}
            result["episodes"].append(episode)
            save()
            try:
                run = await run_one(manifest_path, RolloutContext(policy, label))
            except BaseException as exc:
                episode.update(status="cancelled" if isinstance(exc, asyncio.CancelledError) else "failed",
                               error=f"{type(exc).__name__}: {exc}")
                result_path = manifest_path.with_suffix(".result.json")
                if result_path.is_file():
                    episode["world_result"] = str(result_path)
                save()
                raise
            episode.update(run_id=run.run_id, bundle=str(run.bundle_path), status=run.status)
            save()
            if run.status != "ok":
                raise RuntimeError(f"training episode incomplete: {run.status}: {run.error}")
            batch = collect_rollouts(run.bundle_path, policy, label)
            write_json(out / f"rollout-{index:04d}.json", batch.model_dump(mode="json"))
            episode["rollout"] = batch.statistics(spec.baseline)
            save()
            compute_error: BaseException | None = None
            try:
                result["serving"].append(await server.sleep())
                save()
                if index == spec.episodes:
                    episode["policy_verification"] = await _finish_gpu_worker(
                        verify_rollout_policy, batch, policy, spec, on_error=record_error)
                    episode["evaluation_only"] = True
                    update = None
                else:
                    update = await _finish_gpu_worker(reinforce_update, batch, policy, spec,
                                                      out / f"update-{index:04d}", on_error=record_error)
                    result["updates"].append(update)
                save()
            except BaseException as exc:
                compute_error = exc
                update_receipt = out / f"update-{index:04d}" / "update.json"
                if update_receipt.is_file():
                    episode["update_receipt"] = str(update_receipt)
                record_error(exc, "policy_compute")
                raise
            finally:
                try:
                    result["serving"].append(await server.wake())
                    save()
                except BaseException as exc:
                    record_error(exc, "wake")
                    if compute_error is None:
                        raise
            if update is not None:
                if update["status"] != "updated":
                    result["status"] = "no_update"
                    save()
                    return result
                policy = PolicyIdentity.model_validate(update["next_policy"])
                result["serving"].append(await server.load(policy))
                save()
        result["final_policy"] = policy.model_dump(mode="json")
        result["status"] = "ok"
    except BaseException as exc:
        result["status"] = "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed"
        record_error(exc, "controller")
        raise
    finally:
        save()
        try:
            await server.aclose()
        except BaseException as exc:
            was_failed = result["status"] in {"failed", "cancelled"}
            if not was_failed:
                result["status"] = "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed"
            record_error(exc, "client_cleanup")
            if not was_failed:
                raise
    return result
