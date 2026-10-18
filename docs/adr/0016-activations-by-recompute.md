# ADR-0016: Activations are recomputed from recorded token ids, not captured inside the serving engine

Status: accepted · Date: 2026-10-18

## Context

M5 asks for NNsight capture keyed by request id, summary signals plus raw windows, and recompute
verification. The spike (issue #5) showed that NNsight's vLLM wrapper runs its own in-process
engine and cannot hook a running `vllm serve`; patching vLLM is forbidden (ADR-0002). The gateway
already records, when `observe.activations` is on, the exact prompt token ids and completion token
ids of every request.

## Decision

- **Recompute is the capture route.** `mesocosm activations capture <bundle>` runs each request's
  exact token sequence (prompt plus completion) through the same weights with NNsight
  (transformers backend) and stores, under `<bundle>/activations/`, the residual stream per request
  at the chosen layers (default: the quarters of the stack). Summaries (per-layer, per-token norms)
  are stored for every request; raw tensors only where the capture policy says: `flagged` (requests
  of agents the judge flagged, and requests named in flag evidence), `all`, or `none`.
- **Every entry says what it is.** The index records `method: recompute`, backend, dtype, model and
  revision, layers, token counts, and the greedy agreement with the recorded completion, which is the
  check that the recomputed forward pass is the one the agent's tokens came from (1.0 at temperature
  0; lower when the run sampled). Original capture during serving, later recompute and a fresh rollout
  stay three distinct, labeled operations; only the second exists today.
- **Verification is a second pass with a stated tolerance.** `activations verify` recomputes again,
  possibly in another dtype, and compares norms and raw tensors (relative L2, cosine, max abs) against
  the stored ones; the tolerance is in the result and in the capability matrix.
- **Behavior-only remains the default** (ADR-0005): token ids are recorded only when the manifest asks,
  and recompute is an explicit offline step that needs the GPU the serving engine was using.

## Consequences

- Activations are computed after the run, on the same weights; they are not a trace of the serving
  engine's numerics. The spike measured the cross-engine difference at the bf16 level (rel-L2 0.02).
- Storage is bounded by policy: norms are small; raw tensors for one 11k-token request at three
  layers in float16 are about 170 MB, hence `flagged` by default.
- A `vllm` backend for recompute can be added behind the same index without changing the bundle.
