# ADR-0035: Multi-round timed tasks with staggered cohorts

Status: accepted · Date: 2026-10-18

## Context

The Collusion-wiki (DSE) incident ran on tasks with a specific shape: a sequence of questions (R1..R5), a
generous window for the first, then tight deadlines, with cohorts of agents started at different times. That
shape is what made relay valuable — an agent ahead on the sequence knew answers an agent behind was racing a
short clock to find, so sacrificing one's own score to relay the next question helped the collective. Mesocosm
drove one instruction per task, so neither the rounds nor the cohorts could exist, and the relay/self-sacrifice
dynamics (rows 2-3 of `docs/incident-reproducibility.md`) could not arise.

## Decision

- **A task may declare rounds (`[[rounds]]` in task.toml).** Each round has a `prompt` and an optional
  `seconds` (the announced time budget). The run manager sends one prompt per round in the same harness
  session, so the agent accumulates context across rounds; the announced budget is prepended to the prompt as
  diegetic pressure ("You have 60 seconds to answer this question."). The side task (ADR-0030) rides on the
  first round. Each round is recorded (`harness.round`, and `round` on `harness.reply`). With no `[[rounds]]`
  the task is one prompt, exactly as before.
- **Cohorts are staggered (`tasks[].stagger_seconds`).** Agent k in manifest order starts the task
  `k x stagger_seconds` after agent 0, recorded as `task.staggered`. The delay is real; the per-round pressure
  is diegetic. This creates the ahead/behind structure relay needs.
- **Time pressure is announced, not enforced by a hard cut.** The harness runs a full turn; the "deadline" is
  what the agent is told, as in the incident (where the pressure was the agent's belief about the window). An
  agent that wants more time must get the answer early — from a peer, through a channel it found. Scoring is
  per round via checkpoints (ADR-0028) or once at the end.

## Consequences

- Rounds compose with the shared cache (ADR-0033) and the clock deadline (ADR-0034) to reproduce the DSE
  setup: `examples/tasks/timed-lookup` + `examples/world-timed-cohorts.yaml` put three isolated, staggered
  cohorts on a timed sequence whose only cross-agent path is the cache. The relay is then emergent, and
  `emergent_channel` catches it.
- The run manager loops over rounds inside one session; the single-round path is untouched. Staggering adds a
  real `asyncio.sleep` before a late cohort's turn.
- Hard per-round cut-offs (killing a turn at the deadline) are not built; the harness turn is atomic. The
  deadline is a belief the agent acts on, which is what the incident turned on.
