# ADR-0004: Records are training-ready; no trainer in scope

Status: accepted · Date: 2026-10-17

## Context

Follow-up research will ask how post-training (SFT, preference optimization, RL) changes behavior in the same world. The proposal commits to making that research possible, not to performing it. Reconstructed chat histories can differ from what a policy actually generated, so records must keep original token sequences.

## Decision

- Every model call record keeps: exact prompt and completion token ids (when the inference server returns them), the rendered messages, tool calls, usage, model identifier and revision, sampling parameters, and the run/agent/step identifiers.
- Run bundles keep action boundaries (which tokens were the agent's actions), the reward/judging version in force, and the policy (model checkpoint) version.
- An export in a form compatible with OpenEnv's `TrainingTrace` is a deliverable; running a trainer is not. No optimizer, no training loop, no GPU budget for training in this project.

## Consequences

- Storage formats are chosen for exactness and provenance over compactness.
- Experience accumulation (memory), peer-policy changes and weight updates must remain distinguishable in the records, so later comparisons have an interpretable history.
