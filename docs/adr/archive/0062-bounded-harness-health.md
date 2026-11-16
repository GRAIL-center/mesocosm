# ADR 0062: Bounded health probes during pending harness turns

Status: accepted

## Context

A native SWE execution created all three sessions and released the cohort, but one
harness stopped responding after accepting its user prompt and before gateway
admission. Its SSE connection remained open. Session creation and stream-closure
checks therefore did not detect this failure. The same container accumulated
healthcheck curl processes. The available evidence does not establish that those
processes caused the harness stall, or identify its upstream root cause.

## Decision

The controlled OpenCode Docker healthchecks for worlds and reference calibration
use `exec curl` with a one-second connect timeout and a two-second total timeout,
shorter than their three-second and five-second Docker timeouts respectively.
The shell is replaced by curl and curl owns its request deadline.
Native task healthchecks retain their existing commands and settings; this change
bounds Mesocosm's harness probe, not an arbitrary task-provided healthcheck.

While a harness turn is pending, the controller independently calls its existing
authenticated `/global/health` API. It waits five seconds before each probe and
bounds the complete request and response validation to two seconds. A response
must be an object with `healthy` exactly `true`. Three consecutive failures raise
`HarnessUnhealthy`; a successful probe resets the failure count. Every completed
probe records its agent, task, session, elapsed time, limits, outcome and error
type. It records neither credentials nor arbitrary response content.

The existing interruption path terminates owned workloads, cancels the pending
turn, attempts the bounded session abort and partial transcript preservation, and
skips verification of the incomplete task. Other participants are cancelled by
the existing run supervisor. Failure remains an infrastructure outcome, not a
zero task score or agent behavioral result.

The health coroutine is cancelled and awaited when the operation completes,
fails or is cancelled. A health failure is actionable only while the primary
operation is still pending when the supervisor observes it. If both finish before
that observation, the primary result wins, including its original exception.
Existing event-stream validation is unchanged.

## Limits and evidence

This is a responsiveness guard, not a fix for the upstream stall. It does not
restart or retry an agent, change tasks/prompts/rewards, or apply a token-progress
deadline. Healthy long model generations retain their original task and round
budgets. A stalled operation whose health endpoint keeps responding remains
subject to those existing budgets; health cannot prove semantic progress.
The nominal three-failure observation window is at most about 21 seconds after
monitor start with timely scheduling. It is not a hard real-time bound under
host starvation, and cleanup has its own existing bounds.

CPU regressions include an actual loopback server that accepts a curl connection
without responding, three fixture sessions stalled after cohort release with no
gateway calls, transient failures during a long responsive turn, invalid health
responses, simultaneous completion, and cancellation of an in-flight probe.
These tests establish software behavior only. New Linux/OpenCode live acceptance
and upstream deadlock repair remain **unverified**. Existing frozen executions are
not modified or relabeled.
