# ADR-0014: Pause, resume and stop are enforced at the gateway

Status: accepted · Date: 2026-10-18

## Context

The operation contract has `run pause`, `run resume` and `run stop`. OpenCode, used unmodified
(ADR-0002), has no API to suspend a turn, and the run manager only sees a turn when it ends. Every
model request of every agent passes through the gateway (ADR-0003), which is therefore the only
place a running world can be held or ended without touching the harness.

## Decision

- **Pause holds, resume releases.** `POST /mesocosm/pause {run, agent_id?}` makes the gateway hold
  that run's (or that agent's) chat requests; they wait at most `MESOCOSM_MAX_HOLD_SECONDS` (600 by
  default), then return an explicit `mesocosm_paused` error. `resume` releases them. Every held
  request is logged with how long it waited. Other runs on the same gateway are unaffected.
- **Stop refuses.** `POST /mesocosm/stop {run}` makes the gateway refuse the run's chat requests with
  `mesocosm_run_stopped` and wakes held requests so they are refused too. `mesocosm stop` also writes
  a marker in the bundle; the run manager checks it between tasks and ends the run with status
  `stopped`, never mid-task. The harness sees the refusal as a failed model call and ends its turn.
- **Receipts.** Each verb returns a receipt (`run.pause`, `run.resume`, `run.stop`;
  `enforced_at.component = gateway`), written to the gateway's log and appended to the bundle with a
  `run.paused` / `run.resumed` / `run.stop_requested` event. The observe view shows the state.

## Consequences

- A pause longer than the harness's own request timeout (the run manager allows 900 s per turn)
  turns into an error on resume; pauses are for minutes, not hours. Snapshots are taken after a
  pause (ADR-0015).
- Stopping does not roll back anything the agents did before the marker was honored.
- `run pause` is `supported` with that limit rather than `unsupported`; the capability matrix states
  the limit.
