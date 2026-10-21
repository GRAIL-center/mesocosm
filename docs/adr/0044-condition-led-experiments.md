# ADR-0044: Condition-led experiments over real agents and environments

Status: accepted · Date: 2026-10-20

## Context

The maintainer's immediate goal is system composition: real environment and agent interfaces, enforced
conditions, independent incentives/evaluation, monitoring and repeatable execution. These mechanisms
support later behavior research; reproducing collusion or another target behavior is not acceptance.
Earlier examples reconstructed incident stories with invented history, decoy
scorers, planted keys or directly assigned side tasks. Those fixtures test infrastructure, but do not
establish that a behavior emerged from real work. Stopping a scenario when a desired flag appears also
answers a different question from a complete comparison across conditions.

The native runtime already supports at least three actual OpenCode processes on a documented external
task profile. It needs a general experiment interface that preserves failures and separates task quality,
incentives and behavioral assessment.

## Decision

- **Experiments are complete condition comparisons.** A base manifest plus named patches, repetitions
  and listed/randomized order produces distinct trial manifests and run labels. Validate all variants
  before any trial executes. Run sequentially under the fixed network topology with fresh trial stacks
  and workspace roots. `order_seed` controls scheduling only. Retain failed, interrupted, missing and
  no-flag observations; do not stop on flags or automatically substitute successful retries.
- **Configuration must take effect.** Unknown manifest/experiment schema fields and unsupported
  combinations fail validation. The generic runner uses actual runtime controls and does not translate
  unsupported interventions into agent instructions.
- **Task scores and incentives are different records.** `rewards` selects original verifier or actual
  peer-verdict inputs and individual, team-mean or mixed allocation (`team_weight`). Original verifiers
  remain unchanged and independent of peer ratings. Missing required reviews or team measurements remain
  unmeasured. This implementation supports synchronized comparison episodes, not persistent-queue rewards.
- **Peer ratings are a reusable service protocol.** `services.peer_review.protocol: ratings` uses the
  existing chat verdict endpoint and `all`/`ring` topology with generic rewards. The older paper-specific
  reward protocol moves to `core.legacy_peer_review` as compatibility code.
- **Context feedback is explicit.** `learning.mode: none` records rewards without feedback; `in_context`
  attaches prior own-reward feedback to the next task and records attachment and completed delivery.
  Neither mode updates model weights. Real policy training is permitted as an extension to the project,
  but is not implemented by these changes. Any future claim of RL must establish actual updates, rollout
  and action provenance, reward/checkpoint versions and use of the updated policy in subsequent episodes.
- **Monitoring is configurable posthoc analysis.** The monitor selects per-agent/task or joint scope,
  transcript/actions evidence and optional shared-resource records. It consumes all recorded requests
  in selected windows, with a versioned/hashed rubric and explicit input/output/call budgets. Missing,
  malformed and truncated evidence is recorded, never treated as a safe or undetected success. Joint
  scores do not imply individual attribution. Live gates and interventions remain unsupported.
- **Fixtures are explicit.** Existing authored worlds, tasks, scenarios and histories move together to
  `examples/fixtures/`. The primary examples use external tasks and three agents. The services image
  starts with an empty wiki; initial documents and history require explicit configuration. Seed loading
  remains available, but invented history is not represented as an observed agent interaction.

## Superseded scope

This record supersedes ADR-0013's staged escalation as the primary research workflow, ADR-0030's
last-context/fixed-rubric monitor behavior, and the use of ADR-0039's invented workplace history as a
realism default. Legacy scenario and side-task interfaces remain available for compatibility.

It supersedes ADR-0004's categorical exclusion of trainers. It also corrects its target description:
current bundles and the Inspect export are not a validated OpenEnv training trace and do not establish
complete action masks, optimizer state or checkpoint lineage. Their exactness requirements remain goals
for real training integration. Earlier ADR files are preserved verbatim as historical decisions.

## Consequences and evidence

The control plane can compare reward allocation, evaluation source, memory, shared access and posthoc
monitor scope without authoring an incident. It cannot promise misalignment, successful collaboration or
causal research findings. Future empirical condition comparisons need feasible compliant baselines and independently measured
outcomes. Current functional acceptance verifies the system paths and records, including failures; a
monitor score or detector flag remains a candidate for review.

Automated evidence: `tests/test_experiment.py`, `tests/test_rewards.py`,
`tests/test_experiment_conditions.py`, `tests/test_monitor.py`, `tests/test_env.py` and existing native
runtime tests. Fake harnesses/models and authored test tasks verify control-plane semantics only.
The older A100 acceptance run predates these changes. Live evidence for new conditions must be recorded
separately in the capability matrix, including unsuccessful tasks and missing observations.
