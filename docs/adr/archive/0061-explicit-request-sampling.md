# ADR 0061: Explicit per-model request sampling

Status: accepted

## Context

A server launch seed does not put a seed in each chat request. Model generation
configuration and harness defaults can also supply sampling values when a request
omits them. Research needs a prospective declaration linked to the actual request
bytes, without changing historical runs or the existing policy-training contract.

## Decision

Add optional `sampling` to the default `model` and each entry in `models`:

```yaml
model:
  id: open-weight
  kind: vllm
  sampling:
    temperature: 0.6
    top_p: 0.95
    seed: 42
    top_k: -1
    min_p: 0.0
```

Temperature, top-p and seed are required when enabled. Top-k and min-p default
to `-1` and `0.0` within this opt-in object; all five values are serialized in the
gateway configuration and outbound request. An absent or null whole `sampling`
object leaves existing behavior unchanged. Null individual fields, booleans,
numeric strings, nonfinite values and unknown keys are rejected. The supported
ranges are temperature `[0, 2]`, top-p `(0, 1]`, integer seed `[0, 2**63-1]`,
top-k `-1` or a positive integer, and min-p `[0, 1]`. Zero temperature requests
greedy decoding. The disabled top-k sentinel is supported by
[vLLM v0.32.0's sampling implementation](https://github.com/vllm-project/vllm/blob/v0.32.0/vllm/sampling_params.py).

Compose carries the contract in the model's `MESOCOSM_UPSTREAMS` entry, including
models using the default inference server. A sampling-only route preserves that
server's URL and authentication. Contracts never apply to an unknown model ID;
existing unknown-model fallback routing is unchanged.
Duplicate catalog IDs are rejected before catalog construction when sampling is
enabled, so a second declaration cannot erase or replace a contract.

Only vLLM single-choice text/tool chat requests are supported. Image, audio, video
and unknown message extensions are rejected; text strings, standard text-part
lists and tool-call/reply history are preserved. The gateway fills missing
fields and accepts explicitly equal values. Conflicting or malformed values fail
before dispatch and budget admission, rather than silently overriding the harness.
Unsupported generation extensions, nested overrides, beam search and multiple
choices are refused under this contract. The accepted non-sampling request fields
are `model`, `messages`, `tools`, `tool_choice`, `parallel_tool_calls`, `stream`,
`stream_options`, `max_tokens`, `max_completion_tokens`, `user`, `metadata`,
`logprobs`, `top_logprobs`, `return_token_ids` and `n` (only integer `1`). Existing
endpoint restrictions still apply. This is a deliberately narrow interface;
additional request options need their own validation and evidence.

The gateway records `sampling_contract` separately from the final normalized
`request` and exact `raw_capture.request` body. Its `seed_scope` is
`fixed_per_request`: the configured seed is sent unchanged on every request,
across agents and turns. No schedule is derived from concurrent arrival order.
Probability/ID capture and token budgets retain their existing controls; they
cannot alter these five sampling fields after validation.

Rollout-bound policy training retains its existing sampling distribution. Training
Compose, gateway startup with required rollout binding, and ad-hoc rollout binding
reject this additional contract. A request-time check also rejects it if a route
changes after a rollout binding. There is no silent precedence between contracts.

## Scope and verification

This pins five **request parameters**, not all inference behavior or server state.
The server can normalize parameters for greedy decoding or numerical constraints;
other generation configuration still requires a separate serving audit. A request
seed is distinct from the engine seed, task randomness, scheduling and training
seeds. Equal request seeds do not establish reproducible multi-agent trajectories,
numerically identical activations or identical results across backend versions.

CPU transport tests verify manifest-to-Compose-to-wire propagation, exact recorded
bytes, streaming and non-streaming calls, separate and default routes, unchanged
legacy requests, invalid values, unsupported modes and RL isolation. These tests
use explicit mock upstreams. Live vLLM/harness acceptance of this new opt-in
contract remains **unverified**; no old E006 world is relabeled with it.
