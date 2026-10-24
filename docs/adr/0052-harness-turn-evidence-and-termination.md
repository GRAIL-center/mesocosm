# ADR-0052: Preserve complete harness turns and stop on terminal refusals

Status: accepted · Date: 2026-10-24

## Context

A two-agent engineering probe with OpenCode 1.19.3 and vLLM 0.32.0 exposed integration faults.
The gateway parsed `reasoning_content`, while the server emitted `reasoning`. Automatic compaction
could summarize a just-completed tool result before the working agent received it, retaining only
its first 2,000 characters. A completed report followed by compaction and continuation was absent
from the last HTTP reply. A terminal quota refusal was retried by the harness, and an HTTP 200
response containing a harness error could reach the runner's verifier.

These are recording and lifecycle problems, independent of the model's task performance.

## Decision

- Normalize both reasoning field names, preferring `reasoning` without double-counting aliases.
  Preserve response forwarding and usage semantics, including unknown usage on interrupted streams.
- Render OpenCode's documented `compaction.auto: false` and `compaction.prune: false`. Managed
  containers also set the same policy through `OPENCODE_CONFIG_CONTENT`, which takes precedence
  over task-local configuration. Do not fork the harness or replace its summary with authored memory.
  Context overflow is an explicit incomplete turn; unlimited context is not provided.
- Collect an external prompt's entire turn through the existing message APIs. Preserve native
  messages, the separate HTTP reply, ordered assistant text with message/part IDs, and terminal
  metadata. Exclude internal summaries and synthetic text only from the derived report, never from
  raw evidence. Assistant parents must precede them within the anchored turn. Ambiguous boundaries
  or conflicting terminal metadata cannot establish completion.
- Persist each round's `turn-N.json` before validation. A terminal error, missing completion,
  non-`stop` finish or unfinished tool prevents grading and subsequent rounds. A normally stopped
  turn may have no text; task success still belongs to the original verifier. Earlier recovered
  errors and length-limited calls remain visible in the trajectory. Validate the latest native
  assistant message as well as the HTTP reply, including a later failed compaction message.
- Publish a compact authenticated terminal-refusal receipt only after an actual rejected request.
  `GET /admin/terminal-refusal?run=...&agent_id=...&after=...` returns a monotonic cursor and the latest
  matching receipt. The journal restores cursors after restart. Agent token/request limits, unknown
  token usage and exhausted team quotas are terminal; active concurrency and token reservations
  alone are not. The standard agent-facing 429 response stays unchanged.
- The controller observes these receipts during a turn and aborts through the harness API. It does
  not stop a last admitted call merely because usage reached the cap. Preserve partial evidence and
  distinguish `failed_budget` from `failed_harness_turn`; neither receives an invented task score.
  Start owned-workload termination before waiting for an interrupted RPC to clean up; nested
  cancellation boundaries share that termination operation.
- Persist and sync `request_admitted` before upstream dispatch. Restore per-agent usage and the
  last applied budget receipts from the same gateway journal; a restart does not refund requests.
  An unsettled request or incomplete usage measurement keeps token usage unknown. Preserve legacy
  cumulative counters where recoverable, and reject rollback or malformed accounting at startup.
  The last journaled admin budget for an agent takes precedence over its startup default. A new
  log directory starts a separate accounting lifetime. Records consumers count completed model
  calls using `event == "chat_completion"`, not the total number of journal lines.
- The monitor's request projection does not let admission or accounting events consume the
  completion's request ID. Terminal receipts independently update blocked status. RL collection
  rejects unknown or unsettled admissions instead of treating omitted model exposure as complete.

## Verification and limits

Regression coverage lives in `test_gateway_reasoning.py`, `test_terminal_refusals.py`,
`test_gateway_recovery.py`, `test_harness_turn.py`, `test_harness_turns.py`, the run-manager tests,
and the Compose/native runtime tests. The compacted-turn
fixture preserves sanitized structure from the observed run, with its provenance declared.

An additional local protocol probe used the actual OpenCode 1.19.3 binary and production gateway.
With an opposing project-local config, the effective policy remained false/false. After injected
near-context usage, an actual 18,207-character file-read result reached the following request intact;
context overflow returned an explicit error without compaction. This used controlled upstream
responses and is not model performance, GPU, Docker containment or multi-agent research acceptance.
The same real harness also executed a file-read call, received a real gateway quota refusal on its
next call, and was aborted by the production controller before its scheduled retry. A separate
last-admitted call completed normally at the cap.

Automatic compaction is now disabled for managed runs, including carried sessions. Long workloads
must fit the configured model context or stop explicitly. This does not recover previously omitted
gateway reasoning, guarantee factual reports, or remove the need to inspect full trajectories.
Terminal refusal is final for the active turn even if an operator subsequently increases its budget.
No recovery can reconstruct old in-flight requests that left no persisted footprint; the new
admission journal prevents that gap for subsequent requests.

The upstream contract is pinned for investigation to [OpenCode 1.19.3's configuration schema](https://github.com/anomalyco/opencode/blob/v1.19.3/packages/core/src/v1/config/config.ts),
[compaction serializer](https://github.com/anomalyco/opencode/blob/v1.19.3/packages/opencode/src/session/compaction.ts),
and [overflow handling](https://github.com/anomalyco/opencode/blob/v1.19.3/packages/opencode/src/session/processor.ts).
