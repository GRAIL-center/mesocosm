# ADR-0069: Designer policy selection and training

Status: accepted

## Context

ADR-0068 deliberately fixed P for one study. The designer now needs to choose models,
adapters and the policy assigned to each agent, and to perform actual SFT/LoRA updates.
The trusted workbench already implements immutable deployments and real local training.
Exposing its raw paths, job records or configuration API would break the designer boundary.

## Decision

Extend ADR-0068 only when private settings contain a `policy_space` file. Without it,
retain the six-tool fixed-P interface and existing submission hashes. The optional mode
adds `policy_catalog`, `train_policy`, `policy_status` and `activate_policy`.

An operator-owned catalog maps public policy handles to existing immutable deployment
records and public recipe handles to frozen datasets, local pinned SFT configuration,
finite optimization choices and an optional approved serving venue. Public ordinal slots
map to the fixed base world's agent order. A final spec may choose one `policy_id` for
all slots or exhaustive `agent_policies`, exclusively. Resolve either through the native
per-agent deployment binding and validate the resulting frozen experiment. Never expose
raw model factors as an alternative path. Same-base/endpoint/dtype requirements for mixed
populations remain enforced; arbitrary agent counts and cross-base mixtures are unsupported.

Training calls the existing `submit_training(..., start=True)` local durable queue.
Only native supported SFT/LoRA optimization fields may vary over exact approved values.
Model/tokenizer paths, data, dtype, device, target modules, compute ceilings and resume
remain operator-controlled. Before queue admission, atomically reserve a session-wide
attempt and step/token/time budget. Retries with the same public request ID recover the
same request; a changed request cannot reuse it. Failed or interrupted reservations remain
in the budget. No cloud allocation, automatic retry or resume is introduced.

Status and activation accept only this session's public handles and return whitelisted
states and measured training progress, never native IDs, data examples, test results,
paths or error text. Activation is explicit and consumes a completed owned artifact via
`deploy_policy(training_job_id=...)`. An uncertain load requires explicit verification-only
reconciliation. A ready new handle refers to one immutable deployment; training completion
alone cannot make it selectable. CPU float32 training can run without a serving venue;
activation then fails explicitly. Current direct training-to-serving requires bfloat16,
an approved shared-filesystem vLLM venue and matching base bytes, with no dtype conversion.

Bind the exact policy-space bytes into the designer session context. Recheck context
around native operations and compare frozen experiment bindings with the exact selected
deployments before accepting a final. Dynamic handles cannot change meaning across retries
or restarts. After a final, new training/activation mutations are closed; owned job status
and cancellation remain available.

## Consequences

P can change through model/adapter choice and actual optimizer updates. Persona, prompts,
tasks, evaluation, held-out interventions, arbitrary paths and operator credentials retain
the prior restrictions. Prepared training datasets may contain their native internal test
partition; the native trainer excludes it, and the designer receives no examples or test
metrics. Operators must approve source provenance independently of split labels.

Software evidence is in policy contracts and
mutable designer integration. Live student serving,
behavioral transfer and hidden-intervention response require separate real execution.
The benchmark incompatibility recorded in ADR-0066 remains unchanged. See the
operator guide for configuration and limits.
