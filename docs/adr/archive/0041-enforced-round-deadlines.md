# ADR-0041: Round deadlines abort the harness turn and incomplete work is not graded

Status: accepted · Date: 2026-10-19

Supersedes ADR-0035's announced-only deadline clause.

## Context

Announcing a deadline in a prompt does not enforce a timed environment. The run manager previously waited
for every turn to finish regardless of `rounds[].seconds`, and could then grade late answers as normal work.
Closing an HTTP request alone does not stop the remote OpenCode session.

## Decision

- A round's optional `seconds` is a real wall-clock limit around its harness request. At expiry the manager
  records `harness.round_timeout` and calls OpenCode's documented `POST /session/{id}/abort` endpoint.
  The abort result is recorded as `harness.aborted` or `harness.abort_failed`; it is not inferred from HTTP
  cancellation. A sibling agent's cancelled turn also receives an abort request.
- Native `task.toml`'s `agent.timeout_sec`, when present, limits the total elapsed agent work across all
  rounds. The smaller remaining task or round budget applies. Preparation and cohort staggering precede
  this task clock; the verifier has its own timeout. A task-budget expiry records `harness.task_timeout`.
- The timed-out task ends before its verifier or another round starts. The run has `failed_deadline` status,
  with no reward assigned to incomplete work. Chains treat this deadline failure as terminal.
- A verifier must supply a finite reward. Missing or invalid measurements fail the run as `failed_verifier`;
  they are not successful runs with a null reward. An explicitly configured Docker verifier must exit zero
  as well as produce that measurement, and keeps its process output for diagnosis.
- Task environment preparation happens after the memory policy's workspace operation and before creating
  a harness session. Its metadata is part of the memory receipt. The native environment implementation is
  specified separately in ADR-0042.

## Verification and limits

`tests/test_run_e2e.py` checks the expiry, explicit abort request, absence of grading, and unavailable reward
failure. `tests/test_opencode_adapter.py` checks the abort endpoint. `tests/test_verifier_failure.py` checks
nonzero exits, missing/NaN rewards and the absence of a generic default verifier image. These are regression
tests with fixtures, not evidence that real agents completed a workload. An OpenCode abort acknowledgment
alone does not establish termination of every descendant process; that requires a real runtime check.
