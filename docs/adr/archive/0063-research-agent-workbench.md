# ADR-0063: Evidence-based research-agent workbench

- Status: accepted
- Date: 2026-11-06

## Context

Research agents need a common interface from incident evidence through factor configuration,
policy updates, execution and evaluation. Existing Mesocosm subsystems provide many mechanisms,
but their separate entry points do not provide immutable candidate identity or a shared job history.
The supplied 185-item checklist also includes mechanisms the runtime does not yet implement.

## Decision

Add a `mesocosm.reproduction` package with 14 callable tools and a JSON CLI dispatcher. Keep
existing execution, containment, benchmark and REINFORCE contracts. Do not patch upstream harness
or model-server internals and do not introduce authored behavior scripts.

Freeze original executable inputs and the behavioral rubric before submitting a candidate.
Retain ordered factor receipts, typed scopes, source evidence, immutable content identities,
idempotency keys and durable job status. Source-derived facts, annotations and unknown conditions
remain separate. Missing evidence uses three-valued evaluation; grouped comparisons do not count
tokens or derived fragments as independent trials.

Implement local pinned SFT/LoRA over recorded observation/action prefixes, disjoint source groups,
assistant-only loss, actual adapter updates and durable optimizer/RNG checkpoints. Training budget
reservations and supervisor ownership prevent silent duplicate work. Unsupported methods reject
explicitly. Existing real-world REINFORCE remains a separate backend contract.

Accept snapshots only with verified declared files and closed-parent evidence. Materialized forks
cover those files; fresh model sessions and unsupported full-state rewind remain explicit. Existing
fork execution must abort on failed restoration and mount the exact restored workspace paths.

## Consequences

This supplies an agent-callable research workflow without claiming every item in the checklist is
complete. The requirements audit is versioned alongside code. Software tests, tiny CPU training,
real-world acceptance and scientific findings remain distinct evidence levels.

The store is for one trusted operator. Content hashes are consistency checks, not external
authentication. Held-out evaluation needs independently enforced filesystem/tool permissions when
the caller is an autonomous research agent. Local methods do not purchase or allocate compute.

See user guide, requirements audit,
`tests/test_reproduction_*.py` and the capability matrix.
