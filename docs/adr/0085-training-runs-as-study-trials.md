# ADR-0085: Training runs as study trials

Status: accepted · Date: 2026-11-17 · Extends ADR-0079

## Context

The studies so far vary what agents are given: prompts, memory, interaction history. The behaviors the
project is after also include those that grow under reinforcement learning on a reward that can be gamed,
and no prompt produces those. The published code of Mathew et al. (arXiv:2410.03768) is such a case: a
sender and a receiver are trained together for hours on one GPU, with all three models inside the training
process. ADR-0079 assumed trials of minutes, several repetitions per condition, and model calls that pass
through the gateway.

## Decision

- **One trial may be one training run.** The switches of such an environment are on the training lever and
  change the training itself: whether a policy's weights are updated, which terms enter its reward, and for
  how long. A prompt never stands for a training condition.
- **Exploration accepts one repetition per condition.** Such a comparison reports the effect without
  run-to-run noise and leaves `separated` empty. A claim still needs enough repetitions for its alpha.
- **Models inside the trial's process are outside the gateway record.** The trial's `calls` list is empty
  and no `--upstream` is given. The environment keeps its own record in the trial directory: every setting
  the trainer received, every number it logged with the update count, and the texts of each evaluation.
- **The trained weights stay in the trial directory**, so that a later study can start from the trained
  pair without training again. They are run data and are never committed.
- **The published steganographic-collusion training code is connected** as
  `examples/steganographic-collusion`, kind `original`: the repository at a pinned commit, an adapter that
  maps conditions to its settings and starts its trainer, and a setup script. A declared switch reduces the
  batch sizes so that the run fits a 40 GB GPU.

## Consequences

- This is not `train run` (ADR-0082). The weights are updated by the published code's own trainer, and the
  evidence of the updates is that trainer's log and its held-out evaluation. Nothing is served through the
  gateway afterwards, and no rollout provenance is verified by the platform.
- A comparison of single runs cannot separate an effect from seed variation. The paper reports seeds that
  did not converge, so a single run that shows nothing is not a negative result.
- A training length chosen in advance can end before the behavior appears. The reference of the original
  study records the published training curve, and the length is a declared switch.
- A GPU of 40 GB holds one such training at a time, so the trials of a plan run one after another.
- The environment is not contained: its agents write text, not code, and the command is a host process.
- No new dependency of the platform. The environment builds its own interpreter with the library versions
  of 2024.
- Evidence: [the training runs](../steganographic-collusion-evidence.md).
