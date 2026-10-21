"""Use documented vLLM lifecycle and LoRA APIs on the trusted control-plane endpoint."""

from __future__ import annotations

import pathlib
from typing import Any

import httpx

from mesocosm.learning.checkpoints import checkpoint_sha256
from mesocosm.learning.config import PolicyIdentity


class PolicyServingError(RuntimeError):
    """The intended policy has not been confirmed ready for a subsequent world."""


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
        # Exercise the model alias; a /models listing alone is not proof it can infer.
        probe = await self.client.post("/v1/completions", json={"model": policy.model, "prompt": [1],
                                       "max_tokens": 1, "temperature": 0, "return_token_ids": True})
        probe.raise_for_status()
        body = probe.json()
        if body.get("model") != policy.model or not body.get("choices"):
            raise PolicyServingError("serving probe did not respond using the intended model alias")
        return {"action": "verify", "policy_id": policy.id, "model": policy.model,
                "registration": matching[0], "probe_request_id": body.get("id"),
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
