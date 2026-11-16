# ADR-0082: Training, observation and evidence

Status: accepted · Date: 2026-11-16 · Consolidates ADR-0005, ADR-0016, ADR-0045 and the status rules

## Decision

- **Behavior-only runs are the default.** Activation capture is opt-in. Activations are recomputed from
  recorded token ids, not captured inside the serving engine.
- **Training means weight updates with provenance.** `train run` performs on-policy updates between
  complete real multi-agent worlds and requires verified rollout provenance, action and reward
  boundaries, checkpoint updates and serving activation. Reward logging and context feedback are never
  described as policy-weight training, and their evidence is reported separately.
- **A changed policy is a real one.** A mutable policy uses an actual model or deployment selection, or
  verified weight training. A persona or prompt is not a substitute for it.
- **Three status words.** `supported`, `unsupported`, `unverified`. A capability is `supported` only with
  a linked evidence file or test. A unit-tested feature is not thereby validated with real agents. GPU
  and Docker steps are verified by a runbook, with results recorded in the capability matrix.
- **Run data is never committed.** Nothing under `runs/`, no activations, no run logs.

## Consequences

- A nonzero parameter update is not evidence of a learning benefit; that needs a held-out evaluation.
- Older acceptance runs do not verify conditions added after them.
