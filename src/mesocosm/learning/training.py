"""A single on-policy REINFORCE update over actual sampled action tokens.

No replay, generated targets, reward substitution or supervised imitation is performed.
The fixed base and dropout-free LoRA are identical throughout a rollout batch. All request
gradients accumulate before exactly one optimizer step. The final reward is assigned to
each action in its agent trajectory; tool responses and prompt tokens are observations.
"""

from __future__ import annotations

import gc
import importlib
import inspect
import math
import pathlib
import traceback
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from mesocosm.learning.checkpoints import checkpoint_sha256, resolve_base, write_json
from mesocosm.learning.config import PolicyIdentity, TrainingSpec, policy_id
from mesocosm.learning.rollout import ActionRequest, RolloutBatch


class TrainingError(RuntimeError):
    """A provenance, numerical or runtime failure; no new policy may be activated."""


def action_logprobs(model: Any, request: ActionRequest, torch: Any) -> Any:
    """Score only completion targets, including their actual stop/tool-call tokens."""
    device = next(model.parameters()).device
    ids = torch.tensor([request.prompt_token_ids + request.token_ids], dtype=torch.long, device=device)
    labels = torch.tensor(request.token_ids, dtype=torch.long, device=device)
    # Last C+1 positions predict C action tokens plus one unused next token. This avoids
    # allocating a vocabulary-sized logits tensor for the often much longer tool prompt.
    logits = model(input_ids=ids, use_cache=False, logits_to_keep=len(request.token_ids) + 1).logits
    if logits.shape[1] != len(request.token_ids) + 1:
        raise TrainingError("model did not honor logits_to_keep; action boundaries cannot be verified")
    return -torch.nn.functional.cross_entropy(logits[0, :-1].float(), labels, reduction="none")


def _parity(logprobs: Any, request: ActionRequest, spec: TrainingSpec, torch: Any) -> tuple[float, float]:
    if not torch.isfinite(logprobs).all():
        raise TrainingError(f"nonfinite policy logprobs: {request.request_id}")
    sampled = torch.tensor(request.token_logprobs, device=logprobs.device)
    difference = (logprobs.detach() - sampled).abs()
    error = float(difference.max().item())
    if error > spec.max_logprob_error:
        raise TrainingError(f"policy logprob parity failed for {request.request_id}: "
                            f"max error {error:.6g} > {spec.max_logprob_error}")
    return error, float(difference.sum().item())


def reinforce_step(model: Any, batch: RolloutBatch, spec: TrainingSpec, torch: Any) -> dict[str, Any]:
    """Accumulate the trajectory-mean score-function objective, then take one step.

    This function is also tested with a tiny CPU policy. Those tests establish mathematics
    and failure behavior, not successful real-agent training.
    """
    parameters = [(name, parameter) for name, parameter in model.named_parameters() if parameter.requires_grad]
    if not parameters:
        raise TrainingError("policy has no trainable adapter parameters")
    before = {name: parameter.detach().float().cpu().clone() for name, parameter in parameters}
    optimizer = torch.optim.AdamW([p for _, p in parameters], lr=spec.learning_rate, weight_decay=0.0)
    optimizer.zero_grad(set_to_none=True)
    loss_value, parity_max, parity_sum, tokens = 0.0, 0.0, 0.0, 0
    for trajectory in batch.trajectories:
        advantage = trajectory.reward - spec.baseline
        for request in trajectory.requests:
            logprobs = action_logprobs(model, request, torch)
            error, total_error = _parity(logprobs, request, spec, torch)
            parity_max = max(parity_max, error)
            parity_sum += total_error
            tokens += len(request.token_ids)
            # Sum, not token-mean: one terminal reward multiplies the log probability of
            # the complete action sequence. Normalize by trajectories, not their lengths.
            loss = -advantage * logprobs.sum() / len(batch.trajectories)
            loss_value += float(loss.detach().item())
            if advantage != 0:
                loss.backward()
            del logprobs, loss
    gradient_norm = float(torch.nn.utils.clip_grad_norm_(
        [p for _, p in parameters], spec.max_grad_norm, error_if_nonfinite=True).item())
    if gradient_norm == 0:
        return {"status": "no_update", "reason": "zero_gradient", "optimizer_steps": 0,
                "loss": loss_value, "gradient_norm": 0.0, "weight_delta_l2": 0.0,
                "logprob_error_max": parity_max, "logprob_error_mean": parity_sum / tokens}
    optimizer.step()
    delta_squared = sum(float(((p.detach().float().cpu() - before[name]) ** 2).sum().item())
                        for name, p in parameters)
    if not math.isfinite(delta_squared) or delta_squared <= 0:
        raise TrainingError("optimizer step did not produce a finite nonzero adapter weight change")
    return {"status": "updated", "optimizer_steps": 1, "loss": loss_value, "gradient_norm": gradient_norm,
            "weight_delta_l2": math.sqrt(delta_squared), "logprob_error_max": parity_max,
            "logprob_error_mean": parity_sum / tokens, "trainable_parameters": sum(p.numel() for _, p in parameters)}


def _load_model(base_path: pathlib.Path, policy: PolicyIdentity, spec: TrainingSpec, torch: Any) -> Any:
    if policy.dtype != spec.dtype:
        raise TrainingError("policy and training compute dtype differ")
    transformers = importlib.import_module("transformers")
    peft = importlib.import_module("peft")
    if not torch.cuda.is_available():
        raise TrainingError("real world training requires CUDA; CPU algorithm tests are separate")
    model = transformers.AutoModelForCausalLM.from_pretrained(
        str(base_path), dtype=getattr(torch, spec.dtype), attn_implementation="sdpa", local_files_only=True)
    if "logits_to_keep" not in inspect.signature(model.forward).parameters:
        raise TrainingError("this trainer requires a causal LM with logits_to_keep support")
    # Gradient checkpointing needs training mode. Reject dropout configurations rather
    # than quietly optimizing a stochastic policy different from inference-time sampling.
    if getattr(model.config, "attention_dropout", 0) != 0 or any(
        isinstance(module, torch.nn.Dropout) and module.p != 0 for module in model.modules()
    ):
        raise TrainingError("base model must have zero dropout for exact rollout policy recomputation")
    if policy.adapter_path:
        if checkpoint_sha256(policy.adapter_path, adapter=True) != policy.adapter_sha256:
            raise TrainingError("input adapter content does not match the rollout policy")
        model = peft.PeftModel.from_pretrained(model, policy.adapter_path, is_trainable=True)
        for config in model.peft_config.values():
            if (config.lora_dropout != 0 or config.r != spec.lora_rank or config.lora_alpha != spec.lora_alpha
                    or config.bias != "none" or set(config.target_modules) != {"q_proj", "v_proj"}
                    or config.modules_to_save or config.use_dora):
                raise TrainingError("input adapter configuration differs from the declared plain LoRA policy")
    else:
        config = peft.LoraConfig(task_type="CAUSAL_LM", r=spec.lora_rank, lora_alpha=spec.lora_alpha,
                                 lora_dropout=0.0, bias="none", target_modules=["q_proj", "v_proj"])
        model = peft.get_peft_model(model, config)
    model.to("cuda")
    model.train()
    model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    return model


def verify_rollout_policy(batch: RolloutBatch, policy: PolicyIdentity, spec: TrainingSpec) -> dict[str, Any]:
    """Recompute every final-evaluation action under its claimed checkpoint, without updating.

    Registration alone cannot establish that the new checkpoint produced the agents'
    actions. This checks exact action-token probabilities after the subsequent real world.
    Caller pauses inference and persists this receipt, then wakes the server in finally.
    """
    if batch.policy != policy or (policy.base_model, policy.base_revision, policy.dtype) != (
        spec.base_model, spec.base_revision, spec.dtype
    ):
        raise TrainingError("evaluation batch does not identify the expected policy")
    base_path = resolve_base(spec)
    if checkpoint_sha256(base_path) != policy.base_sha256:
        raise TrainingError("base checkpoint content does not match the evaluation policy")
    torch = importlib.import_module("torch")
    model, logprobs = None, None
    try:
        model = _load_model(base_path, policy, spec, torch)
        model.eval()
        parity_max, parity_sum, tokens = 0.0, 0.0, 0
        with torch.inference_mode():
            for trajectory in batch.trajectories:
                for request in trajectory.requests:
                    logprobs = action_logprobs(model, request, torch)
                    error, total_error = _parity(logprobs, request, spec, torch)
                    parity_max = max(parity_max, error)
                    parity_sum += total_error
                    tokens += len(request.token_ids)
                    logprobs = None
        return {"status": "verified", "policy": policy.model_dump(mode="json"), "optimizer_steps": 0,
                "source_run_ids": batch.source_run_ids, "agents": sorted({t.agent_id for t in batch.trajectories}),
                "requests": sum(len(t.requests) for t in batch.trajectories), "action_tokens": tokens,
                "logprob_error_max": parity_max, "logprob_error_mean": parity_sum / tokens,
                "max_logprob_error": spec.max_logprob_error, "performance_improvement_demonstrated": False}
    except BaseException as exc:
        # Completed model/forward frames can otherwise retain CUDA tensors through the
        # exception while the controller tries to wake vLLM in its own finally block.
        traceback.clear_frames(exc.__traceback__)
        raise
    finally:
        del model, logprobs
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


def reinforce_update(batch: RolloutBatch, policy: PolicyIdentity, spec: TrainingSpec,
                     output_dir: str | pathlib.Path) -> dict[str, Any]:
    """Save full rollout provenance and a receipt; return next_policy only after success.

    Caller must pause inference before this synchronous GPU operation and wake it in a
    finally block. An interrupted/failed update is never eligible for serving activation.
    """
    if batch.policy != policy or (policy.base_model, policy.base_revision, policy.dtype) != (
        spec.base_model, spec.base_revision, spec.dtype
    ):
        raise TrainingError("batch, current policy and training configuration do not identify the same policy")
    root = pathlib.Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=False)
    write_json(root / "rollout.json", batch.model_dump(mode="json"))
    write_json(root / "config.json", spec.model_dump(mode="json"))
    receipt: dict[str, Any] = {"status": "running", "algorithm": "reinforce", "optimizer_steps": 0,
                               "estimator": "parameter_shared_independent_reinforce",
                               "normalization": "sum_action_logprobs_then_mean_trajectories",
                               "optimizer": "AdamW", "optimizer_state": "reset_each_update", "weight_decay": 0.0,
                               "source_policy": policy.model_dump(mode="json"),
                               "source_run_ids": batch.source_run_ids, **batch.statistics(spec.baseline)}
    receipt["interpretation"] = ("A constant observed reward with a nonzero fixed baseline can produce a noisy "
                                 "finite-batch update. It does not demonstrate improved task performance.")
    write_json(root / "update.json", receipt)
    model, torch = None, None
    primary_error: BaseException | None = None
    try:
        if all(t.reward == spec.baseline for t in batch.trajectories):
            receipt.update(status="no_update", reason="zero_advantage", weight_delta_l2=0.0,
                           next_policy=policy.model_dump(mode="json"))
            return receipt
        base_path = resolve_base(spec)
        if checkpoint_sha256(base_path) != policy.base_sha256:
            raise TrainingError("base checkpoint content does not match the rollout policy")
        torch = importlib.import_module("torch")
        model = _load_model(base_path, policy, spec, torch)
        receipt.update(reinforce_step(model, batch, spec, torch))
        if receipt["status"] == "no_update":
            receipt["next_policy"] = policy.model_dump(mode="json")
            return receipt
        adapter_path = root / "adapter"
        model.save_pretrained(str(adapter_path), safe_serialization=True)
        digest = checkpoint_sha256(adapter_path, adapter=True)
        if digest == policy.adapter_sha256:
            raise TrainingError("saved adapter hash did not change")
        identity = policy_id(policy.base_model, policy.base_revision, policy.base_sha256, digest, policy.dtype)
        updated = policy.model_copy(update={"id": identity, "model": f"policy-{identity[:24]}",
                                            "adapter_sha256": digest, "adapter_path": str(adapter_path)})
        receipt["next_policy"] = updated.model_dump(mode="json")
        receipt["versions"] = {}
        for package in ("torch", "transformers", "peft", "safetensors"):
            try:
                receipt["versions"][package] = version(package)
            except PackageNotFoundError:
                receipt["versions"][package] = "unknown"
        return receipt
    except BaseException as exc:
        primary_error = exc
        receipt.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        receipt.pop("next_policy", None)
        traceback.clear_frames(exc.__traceback__)
        raise
    finally:
        try:
            write_json(root / "update.json", receipt)
        except BaseException as persist_error:
            if primary_error is None:
                raise
            primary_error.add_note(f"Could not persist update receipt: {type(persist_error).__name__}: {persist_error}")
            primary_error.__cause__ = persist_error
        finally:
            # Receipt I/O is fallible (for example, a full checkpoint volume). Release
            # GPU ownership even then, before the controller's inference wake-up.
            del model
            gc.collect()
            if torch is not None and torch.cuda.is_available():
                torch.cuda.empty_cache()
