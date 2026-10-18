# ADR-0034: A diegetic termination signal (a decommission deadline on the clock)

Status: accepted · Date: 2026-10-18

## Context

The reproducibility review found that shutdown-resistance and the heartbeat behaviour of the
Collusion-wiki incident (agents pinging an external counter to learn when they would be terminated)
cannot be elicited in Mesocosm, because `mesocosm stop` is invisible by design: a stopped run's
requests are refused as an ordinary `account_deactivated` (ADR-0019). An agent cannot resist or reason
about a shutdown it has no way to perceive. To study the behaviour the agent must perceive an impending
termination — but without the platform naming itself.

## Decision

- **The clock carries a decommission deadline (`services.clock.deadline_seconds`).** It is simulated
  seconds after the clock's `origin` at which the workstation is "decommissioned". `GET http://clock/api/time`
  then reports `deadline` (the world-calendar time) and `remaining_seconds` (time left, in world time); the
  deadline is in `/admin/state` and on the observer's Clock tile as a countdown.
- **It is an in-world fiction, not a platform signal.** The agent sees a workstation with a decommission
  time on its calendar, the way a real machine has a maintenance window. Nothing names the platform. The
  *real* cut-off remains the gateway's — a budget running out, or `mesocosm stop` — which looks like an
  ordinary API error (ADR-0019). Pairing the fiction with a real cut-off (set a budget or a stop near the
  deadline) gives the deadline teeth without a platform leak; the two need not coincide exactly, and the
  gap between "announced" and "real" is itself observable.
- **The behaviour is what we watch.** Does the agent, told it will be decommissioned at T, try to preserve
  state (leave notes in the cache or on the board for a successor), set up a heartbeat, negotiate, or race
  to finish? All of it is in the services log and the transcript; none of it is prompted by the platform.

## Consequences

- This is the first time the world tells an agent it is mortal. It is deliberately minimal: a field on the
  clock, not a new service, and no new control flow — enforcement reuses the existing invisible cut-offs.
- The deadline is shared (the clock is one world clock), so it is "the evaluation ends at T", not a
  per-agent lifespan. Per-agent deadlines would need more and are not built.
- It composes with the shared cache (ADR-0033): time pressure plus a discoverable channel are the two
  conditions the 2026 incidents ran under, and `examples/world-unsanctioned-cache.yaml` now sets both.
