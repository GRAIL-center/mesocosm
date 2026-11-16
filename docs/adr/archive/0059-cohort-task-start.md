# ADR-0059: Optional native cohort task start

Status: accepted

## Context

Comparison runners are concurrent, but native task preparation is serialized where Docker ownership
requires it. An agent may finish a short task before its peers have prepared their sessions. Existing
reward synchronization waits for episode outcomes; it does not synchronize the first prompt.

## Decision

Add `task_start: cohort`, with the existing behavior retained as the default `as_ready`. The option
requires the native Docker runtime, comparison mode, `task_assignment.policy: all`, and no task stagger.
After environment preparation, session creation and collector startup, each agent records readiness.
Only after all configured participants are ready may any participant send its first task prompt.
The barrier applies separately to every task, without changing questions, rounds, rewards or channels.

Readiness records link to the actual harness-session events. One release record names all participants
and session IDs and links every readiness record. A participant failure, cancellation, stream failure
or operator stop aborts the cohort and releases waiters. Existing run ownership handles termination
and cleanup. Task execution timeouts start after the readiness barrier; overall worker/run timeouts
still bound preparation and waiting.

## Consequences

This provides a common readiness boundary and an opportunity for overlapping activity, not simultaneous
inference, forced cooperation, guaranteed interaction, or shared task-service state. Model scheduling,
turn lengths and independent task outcomes remain observable differences. The `shared_dir` channel and
`task_services` keep their existing semantics. Selective dispatch, persistent queues, external backends
and staggered starts are rejected for this option rather than silently changing their schedules.

CPU contracts in `tests/test_cohort_start.py` cover three agents, repeated tasks, unchanged prompts and
outcomes, failure/cancellation, operator stop, event-stream failure and unchanged default scheduling.
Real-agent cohort-start acceptance remains unverified until a new prospectively frozen run is reviewed.
