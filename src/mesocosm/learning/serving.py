"""Use documented vLLM lifecycle and LoRA APIs on the trusted control-plane endpoint."""

from __future__ import annotations

import pathlib
from typing import Any

import httpx

from mesocosm.learning.checkpoints import checkpoint_sha256
from mesocosm.learning.config import PolicyIdentity


class PolicyServingError(RuntimeError):
    """The intended policy has not been confirmed ready for a subsequent world."""


def _dtype(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    name = value.lower().removeprefix("torch.")
    return {"half": "float16", "float": "float32"}.get(name, name)


def _verify_configuration(info: Any, policy: PolicyIdentity) -> dict[str, Any]:
    """Read vLLM's resolved configuration, retaining only an explicit non-secret subset."""
    config = info.get("vllm_config") if isinstance(info, dict) else None
    if not isinstance(config, dict) or not all(isinstance(config.get(key), dict)
            for key in ("model_config", "cache_config", "lora_config")):
        raise PolicyServingError("server_info is missing resolved model/cache/LoRA configuration")
    model, cache, lora = (config[key] for key in ("model_config", "cache_config", "lora_config"))
    actual_model, revision = model.get("model"), model.get("revision")
    if not isinstance(actual_model, str) or not actual_model:
        raise PolicyServingError("server_info is missing the actual base model")
    local = pathlib.Path(actual_model).expanduser()
    if local.is_dir():
        if checkpoint_sha256(local) != policy.base_sha256 or revision not in (None, policy.base_revision):
            raise PolicyServingError("server local base checkpoint or revision differs from the policy")
        model_verification = "local_checkpoint_sha256"
    elif actual_model != policy.base_model or revision != policy.base_revision:
        raise PolicyServingError("server base model or pinned revision differs from the policy")
    else:
        model_verification = "resolved_model_and_revision; rollout_logprob_parity_required"
    if _dtype(model.get("dtype")) != policy.dtype:
        raise PolicyServingError("server compute dtype differs from the policy")
    if "quantization" not in model or model["quantization"] is not None \
            or model.get("quantization_config") is not None:
        raise PolicyServingError("quantized or unspecified serving weights cannot be recomputed by this trainer")
    overrides, hf_config = model.get("hf_overrides"), model.get("hf_config")
    if not isinstance(overrides, dict) or set(overrides) - {"head_dtype"} or not isinstance(hf_config, dict) \
            or model.get("model_class_overrides") or model.get("hf_config_path"):
        raise PolicyServingError("server model/config overrides cannot be recomputed by this trainer")
    head_dtype = hf_config.get("head_dtype")
    if head_dtype not in (None, "model") and _dtype(head_dtype) != policy.dtype:
        raise PolicyServingError("server lm_head dtype differs from the policy compute dtype")
    if overrides.get("head_dtype") not in (None, "model") and _dtype(overrides["head_dtype"]) != policy.dtype:
        raise PolicyServingError("server lm_head override differs from the policy compute dtype")
    kv_dtype, lora_dtype = _dtype(cache.get("cache_dtype")), _dtype(lora.get("lora_dtype"))
    if kv_dtype not in ("auto", policy.dtype) or lora_dtype not in ("auto", policy.dtype):
        raise PolicyServingError("server KV cache or LoRA dtype differs from the policy compute dtype")
    # Full server_info also contains environment data. Do not copy it, arbitrary model
    # metadata or any configuration dictionaries into the persisted serving receipt.
    result = {"model": actual_model, "revision": revision, "dtype": policy.dtype,
              "head_dtype": policy.dtype, "kv_cache_dtype": kv_dtype, "lora_dtype": lora_dtype,
              "quantization": None, "model_verification": model_verification}
    for section, key in (("model_config", "enforce_eager"), ("model_config", "model_impl"),
                         ("attention_config", "backend"), ("cache_config", "enable_prefix_caching"),
                         ("scheduler_config", "enable_chunked_prefill")):
        value = config.get(section, {}).get(key) if isinstance(config.get(section), dict) else None
        if type(value) in (bool, str):
            result[f"{section}.{key}"] = value
    return result


class VLLMPolicyServer:
    """The controller and server must share checkpoint paths on the inference host.

    /v1/models confirms adapter registration, not cryptographic proof of GPU contents.
    The load receipt records the hashed local artifact passed to the supported vLLM API;
    rollout/trainer logprob parity independently checks that it was the sampled policy.
    """

    def __init__(self, server_url: str, api_key: str | None = None, *,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.client = httpx.AsyncClient(base_url=server_url.rstrip("/"), timeout=300,
                                        headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
                                        transport=transport)

    async def aclose(self) -> None:
        await self.client.aclose()

    async def sleep(self) -> dict[str, Any]:
        response = await self.client.post("/sleep", params={"level": 1})
        response.raise_for_status()
        return {"action": "sleep", "level": 1, "status_code": response.status_code}

    async def wake(self) -> dict[str, Any]:
        response = await self.client.post("/wake_up")
        response.raise_for_status()
        return {"action": "wake", "status_code": response.status_code}

    async def verify(self, policy: PolicyIdentity) -> dict[str, Any]:
        response = await self.client.get("/v1/models")
        response.raise_for_status()
        entries = response.json().get("data", [])
        matching = [entry for entry in entries if entry.get("id") == policy.model]
        if len(matching) != 1:
            raise PolicyServingError(f"server does not expose exactly one intended model: {policy.model}")
        if policy.adapter_path:
            registered_path = matching[0].get("root")
            if registered_path != str(pathlib.Path(policy.adapter_path).resolve()):
                raise PolicyServingError("registered adapter path differs from the checkpoint to activate")
        configuration = await self.client.get("/server_info", params={"config_format": "json"})
        configuration.raise_for_status()
        try:
            resolved = _verify_configuration(configuration.json(), policy)
        except (ValueError, OSError) as exc:
            raise PolicyServingError(f"cannot verify resolved serving configuration: {exc}") from exc
        # Exercise the model alias; a /models listing alone is not proof it can infer.
        probe = await self.client.post("/v1/completions", json={"model": policy.model, "prompt": [1],
                                       "max_tokens": 1, "temperature": 0, "return_token_ids": True})
        probe.raise_for_status()
        body = probe.json()
        if body.get("model") != policy.model or not body.get("choices"):
            raise PolicyServingError("serving probe did not respond using the intended model alias")
        return {"action": "verify", "policy_id": policy.id, "model": policy.model,
                "registration": matching[0], "configuration": resolved, "probe_request_id": body.get("id"),
                "probe_completion_tokens": body.get("usage", {}).get("completion_tokens"),
                "weight_verification": "local_artifact_hash_and_load_api; rollout_logprob_parity_required"}

    async def load(self, policy: PolicyIdentity) -> dict[str, Any]:
        if not policy.adapter_path or not policy.adapter_sha256:
            raise PolicyServingError("dynamic activation requires a saved adapter")
        path = pathlib.Path(policy.adapter_path).resolve()
        if checkpoint_sha256(path, adapter=True) != policy.adapter_sha256:
            raise PolicyServingError("adapter content changed before serving activation")
        # New aliases are immutable. Replacing a known name could hide stale/in-flight use.
        response = await self.client.get("/v1/models")
        response.raise_for_status()
        if any(entry.get("id") == policy.model for entry in response.json().get("data", [])):
            raise PolicyServingError("new policy alias is already registered; refusing to overwrite it")
        response = await self.client.post("/v1/load_lora_adapter", json={"lora_name": policy.model,
                                                                       "lora_path": str(path)})
        response.raise_for_status()
        receipt = await self.verify(policy)
        receipt.update(action="activate", adapter_sha256=policy.adapter_sha256,
                       adapter_path=str(path), load_status_code=response.status_code)
        return receipt
