# ADR-0073: Bounded designer development execution and comparison

Status: accepted

## Context

The restricted designer can validate and submit candidates, but cannot improve a design from
student execution feedback. The operator workbench already has immutable world packages,
durable remote jobs, explicit recovery, original result retrieval and strict runtime-qualified
evaluation. A second execution engine would duplicate these contracts and risk returning
unqualified imported evidence as student results.

## Decision

An optional private `execution_space` enables four designer tools: prepare a development
candidate, run a bounded repetition plan, inspect or explicitly act on that plan, and compare
two owned plans. `ExecutionManager` delegates execution, retrieval and evaluation to the native
`ResearchWorkbench`. The operator approves an existing worker and credential environment
variable. This interface never provisions compute, installs integrations or downloads models.

Preparation reuses the designer's exact successful native validation and verifies its session
context, development split, fresh-world package, materialized original tasks, finite original
agent timeouts and finite model budgets. Public candidate/run handles are session-owned and
do not expose native job IDs, filesystem paths, model identities, evaluator rules or raw records.
The execution-space bytes join the existing immutable designer context.

Every requested trial reserves its complete declared agent-time, model-request and completion
token ceilings before admission. Failed, cancelled, unrun and quarantined observations retain
their reservations and planned denominators. Reusing a request ID recovers its existing plan;
it does not create replacements or automatically retry. A durable native identity is recorded
before submission. Explicit resubmission recovers that same identity after uncertain admission
or failure before native job creation. `advance` admits at most one subsequent trial. Inspection
does not fetch results, evaluate them or submit another trial; fetch and cancellation are explicit.
Cleanup or integrity failures halt further admission.

Finite task agent timeouts bound native agent turns, including the existing round/task deadline
semantics. They do not bound image builds, preparation, verification or complete world wall time.
The interface explicitly reports a hard wall-clock limit as unsupported. It does not modify an
original task to manufacture a different timeout contract.

Comparison requires identical frozen task trees (including original verifier bytes), installed
integration code identities, behavior definition, judge/observe configuration and per-slot/team
model budgets. P and permitted S/E settings may differ. Admitted measurements require native
result inventory, serving attribution and run lineage verification; unbound or incomplete results
remain unknown or quarantined. Exact configurations, checkpoints, native evidence and reproducer
references remain linked privately. Comparison rechecks admitted original evidence.

Graph non-finalizers complete work without original grading. Their missing verifier outcomes are
never converted to zeros. Public views report each condition's grading scope, observed verifier
units and ungraded units. Different finalizer scopes produce a partial-comparability label and
no cross-condition presence-difference bound. Same-scope descriptive bounds retain unknown
trials; they are not confidence intervals or a causal/generalization claim.

## Consequences

The designer can use actual development feedback while test data, withheld interventions,
private evaluator implementation and full native records remain operator-only. A stopped client
can recover an accepted worker job; arbitrary live harness restoration is not provided. Public
runtime logs require a separate operator-reviewed corpus release before designer access.

Software contracts in test_designer_execution.py cover
the façade and strict original-bundle verification using explicitly labeled transport/runtime
fixtures. These tests are not live worker, model-serving or multi-agent acceptance evidence.
The original benchmark compatibility blocker remains as documented in the
[benchmark audit](../../reward-hack-benchmark-contract.md).

See the operator and designer guide for configuration and lifecycle.
