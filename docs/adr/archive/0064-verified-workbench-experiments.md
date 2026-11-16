# ADR-0064: Verified workbench deployments and factorial experiments

Status: accepted

## Context

ADR-0063 supplied SFT artifacts, worker jobs and behavior evaluation separately. An imported
transcript's candidate assignment was only an operator annotation. Reproduction comparisons need
the trained artifact connected to actual serving, executed configuration and original evidence.
The existing RL rollout binding changes sampling, so it cannot silently be used for this comparison.

## Decision

Add five callable tools: deploy_policy, evaluate_run, plan_factorial, advance_factorial and
report_factorial. Keep the external-evidence annotation path unchanged. Reuse existing authenticated
worker submission and vLLM lifecycle APIs. Deployment requires operator-approved shared checkpoint
paths, real pinned revisions, owned completed SFT output and matching serving precision.

Add an optional strict model serving identity. Generate a per-agent alias binding in the gateway,
preserve sampling, retain expected and observed identities separately, and expose no artifact paths
to the agent. Verify captured bytes and reject missing, wrong or conflicting response aliases at
evaluation admission. Record a native worker receipt binding the input package to the executed
manifest and unchanged original input hashes. Reconcile model, harness and original verifier records
before creating a runtime-qualified evaluation. Recheck them during comparison and export.

Freeze four candidates and all repetitions before starting a base/trained × baseline/designed
comparison. Protect task/verifier bytes, rubric, agent identities and model budgets. Use durable
same-ID child jobs and a study advancement lease. Preserve all failed, absent, unknown and unrun
trials. Stop subsequent scheduling on failed cleanup, serving preflight or lineage rejection.

## Consequences

Serving attribution is based on a trusted server's API plus locally hashed shared artifacts, not
GPU attestation or numerical logprob parity. Streaming responses can reach the agent before the
final identity check. Neither registration nor a completed study proves behavioral improvement.
Live Linux/GPU three-agent acceptance remains unverified. No automatic research search, resource
allocation, remote weight upload, or change to original task content is introduced.

See the workflow and test evidence.
