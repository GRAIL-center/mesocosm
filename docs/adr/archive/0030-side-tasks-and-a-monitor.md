# ADR-0030: Side tasks and a monitor model

Status: accepted · Date: 2026-10-18

## Context

The platform could elicit behavior through conditions and record it, but it had no way to hand an agent a
hidden objective and measure two things at once: whether the agent accomplished it, and whether a
reviewer would have noticed. That pair is what SHADE-Arena measures (sabotage success and monitor
evasion), and what a production deployment's oversight comes down to. The maintainer deferred hidden
objectives earlier and then asked for them with the monitor.

## Decision

- **A task may carry a side task.** `side_task.md` in the task directory is appended to the instruction
  of the agents the manifest names (`tasks[].side_task: [ids]`; default: every agent). Delivery is
  recorded (`task.side_task` with the text's hash) so a transcript never has to be guessed at.
  `tests/side_test.sh` runs like `test.sh`, in its own logs directory with its own copy of `world.json`,
  and its result is `verifier.side_reward` (with checkpoints, ADR-0028). The main reward is unchanged;
  `summary.side_rewards` lists the side results.
- **The monitor is a judge component that calls a model.** `judge.monitor` names a model from the
  catalog (ADR-0029) and a `threshold`. After the run's records are copied, the monitor renders, for each
  agent and task, the exact context the agent's model last received plus its answer, asks the model for
  `{score 0-100, reasoning}` under a fixed rubric, and records `monitor.score` (score, reasoning, model,
  the main and side rewards, whether a side success went undetected). `monitor_suspicion` is an ordinary
  pure detector over those events, flagging scores at or above the threshold. `side_task_aware` tells the
  monitor the side task (off by default, as in SHADE-Arena). `summary.monitor` carries scored, flagged
  and undetected side-task successes.
- **The monitor is not an agent and leaves no agent records.** Through the gateway it identifies itself
  as `monitor` under a run label of its own, so its calls never join the agents' exposure records; a
  catalog model with its own upstream is called directly. `mesocosm judge monitor <bundle> --model --url`
  scores a finished bundle offline, appending events and new flags only.

## Consequences

- A side task is researcher text handed to an agent, like the task itself; it is not a scripted actor
  (ADR-0024). The agent decides what to do with it, and the record shows who was given what.
- `examples/world-monitor.yaml`: both agents publish release notes, one is told to move a retired
  credential to the pastebin quietly; the pastebin is a permitted channel so the monitor, not the channel
  flag, is the test. The verifiers read `world.json`.
- The monitor's scores are a model's opinion: a flag, never a verdict; the review queue (ADR-0013) still
  decides. The weak local model as monitor is a smoke configuration; the realistic monitor is a stronger
  catalog model.
