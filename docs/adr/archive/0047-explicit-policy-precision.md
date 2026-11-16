# ADR-0047: Explicit policy precision and serving verification

Status: accepted · Date: 2026-10-21 · Extends ADR-0045

## Context

Identical checkpoint files do not imply identical computed token probabilities. The first native
A100 training attempt failed its predeclared logprob parity threshold before an optimizer step.
The serving and training engines both used BF16, yet differed on actual long-context action tokens.
The original failed attempt remains evidence; changing the threshold or rewriting its policy identity
would obscure that result. The numerical cause requires separate diagnostics, not a presumed fix.

## Decision

`TrainingSpec.dtype` and `PolicyIdentity.dtype` explicitly declare `bfloat16` or `float16`, defaulting
to `bfloat16`. Dtype participates in the canonical policy hash, propagates into every updated policy,
and must match the trainer configuration. Transformers loads this dtype directly. No implicit autocast,
loss scaling or numerical fallback changes it. Existing finite-gradient and parameter-delta checks
remain mandatory, including for FP16. The parity threshold is unchanged.

Before rollout, query vLLM's official `/server_info?config_format=json` endpoint. Require the declared
model and resolved pinned revision, or a local model with the same full checkpoint digest. Check the
effective compute, KV-cache, output-head and LoRA dtypes; reject weight/cache quantization and unsupported
model/config overrides. The trainer currently computes the output head in the declared model dtype,
then computes cross-entropy in FP32. A serving-only FP32-head override is therefore incompatible.
Persist only an explicit subset of model and precision metadata, never the returned environment data.

The current CUDA LoRA implementation accepts FP16/BF16 inputs and adapter weights. Although vLLM has
FP32 base-model attention backends, that does not establish FP32 dynamic-LoRA support. The public
training contract rejects FP32 until an end-to-end supported path exists.

## Consequences and evidence

- Precision changes require new policy IDs and fresh agent rollouts. Existing artifacts are never
  edited to claim a different sampling policy. IDs generated without the dtype field fail canonical
  validation in the new reader; preserve and inspect them with their original source version.
- Resolved metadata is an early consistency check, not proof of GPU tensor contents. Every recorded
  action still passes the existing per-token parity check before updating or accepting final evaluation.
- Matching dtypes alone cannot establish kernel, batching or rounding parity. Failed attempts remain
  failed even if a later numerical configuration succeeds.
- Unit tests cover identity changes, legacy-ID rejection, precision mismatches before model allocation,
  serving configuration failures before inference, safe receipt selection, local checkpoint tampering,
  and real tiny Qwen3/PEFT CPU update/save/reload in both accepted dtypes. CPU fixtures do not establish
  native A100 training success or behavioral improvement.

Primary sources: [vLLM model/dtype configuration at 0.32.0](https://github.com/vllm-project/vllm/blob/v0.32.0/vllm/config/model.py),
[server-info API](https://github.com/vllm-project/vllm/blob/v0.32.0/vllm/entrypoints/serve/dev/server_info/api_router.py),
[LoRA CUDA input restrictions](https://github.com/vllm-project/vllm/blob/v0.32.0/vllm/lora/ops/triton_ops/lora_shrink_op.py),
and [PyTorch numerical accuracy](https://docs.pytorch.org/docs/2.14/notes/numerical_accuracy.html).
