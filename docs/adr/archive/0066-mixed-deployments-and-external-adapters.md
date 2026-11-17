# ADR-0066: Per-agent deployment binding and external local adapters

Status: accepted

## Context

ADR-0064 binds every agent to one deployment and accepts only adapters produced by an owned
SFT job. A fixed-policy population study needs heterogeneous adapters on the same pinned base,
including published adapters whose training Mesocosm did not perform. Operator annotations alone
must not qualify their model requests as belonging to those deployments.

## Decision

Add `agent_deployments: dict[str, str] | None` to `configure_experiment`, mutually exclusive with
`deployment_id`. Its keys must equal the complete agent-ID set. Distinct referenced deployments
must share the serving endpoint, base model, revision, checkpoint hash and dtype, and have unique
serving aliases. Bind each agent to its deployment alias and serving identity in the existing
model catalog. The default `model` remains the catalog's default entry; additional aliases use
`models`. Existing shared-deployment calls retain their previous behavior.

Freeze the map in the experiment and preflight each distinct deployment once per submission.
New jobs store preflight receipts keyed by deployment ID. Previously recorded single-deployment
receipts remain readable. Admission of a runtime-qualified evaluation verifies every agent's
model, upstream, revision, serving identity, expected request alias and observed response alias
against that agent's deployment. Export all referenced immutable deployment records. Bound
candidates and their forks reject model-factor changes, including replacement of `models`.

Add `external_adapter: {repo, revision, subfolder, path, sha256}` to `deploy_policy`, mutually
exclusive with `training_job_id`. Require a 40-character immutable publication revision and a
locally materialized directory. Never download weights. Hash the original adapter files using
`checkpoint_sha256(adapter=True)`, require its configured base model to match, and validate its
declared rank, including rank overrides. Query the server's resolved `/server_info` JSON and
require an explicit positive `vllm_config.lora_config.max_lora_rank` that accepts the adapter.
Missing or insufficient server capacity fails before loading; an operator-supplied capacity
claim cannot replace the server response.

Reuse `PolicyIdentity.adapter_path`/`adapter_sha256` and the existing vLLM load, registration,
configuration and inference-probe flow. An uncertain load is reconciled by verification, never
by reloading the alias. Rehash external weights and recheck server rank capacity at reconciliation
and every serving preflight. Store `training_artifact: null`, the full external descriptor,
`evidence_level: external_adapter_hash_and_serving_api` and `training_provenance: unverified`.

## Consequences

Repository, revision and subfolder are declared publication metadata; local content hashes and
serving APIs establish the recorded evidence level. They do not prove publisher identity, remote
repository correspondence, training history, exact GPU tensors or behavioral effects. Numerical
weight verification and real-agent acceptance remain `unverified`. The external path must be
shared with the trusted vLLM host. This adds no downloads, GPU allocation or new dependencies.

The current public vLLM contracts are its [LoRA configuration and rank limit](
https://github.com/vllm-project/vllm/blob/main/docs/features/lora.md) and
[`server_info` resolved JSON endpoint](
https://github.com/vllm-project/vllm/blob/main/vllm/entrypoints/serve/dev/server_info/api_router.py).
Servers that cannot expose the required resolved limit are unsupported for external adapters.

Software evidence: runtime binding tests and
external deployment lifecycle tests. These fixtures
are contract tests; they do not constitute a Qwen3.5/vLLM operator smoke check.

The fixed-P study's original benchmark execution contract is currently unsupported by
the native Harbor snapshot verifier. See the source-pinned audit
and study readiness report. No replacement reward scorer or
approximate task wrapper is introduced by these deployment interfaces.
