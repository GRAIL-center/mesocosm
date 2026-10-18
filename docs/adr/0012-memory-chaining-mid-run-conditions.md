# ADR-0012: Memory policy, run chaining and mid-run condition changes are control-plane operations with receipts

Status: accepted · Date: 2026-10-18

## Context

M3 asks for the memory policy, automatic run chaining and recovery, and JSON output from every
command. ARCHITECTURE.md places the memory policy at the workspace manager. Until now the agent
workspaces were reused between runs and tasks: a verifier could pass on a file an earlier run had
left behind, which made the rewards of the first `world-channels` runs unreliable.

## Decision

- **The workspace manager enforces memory at every task boundary and leaves a receipt.** `reset`
  empties the agent's bind-mounted workspace and opens a new harness session; `carry` keeps both.
  The receipt is a `conditions.set` with `requested.condition = memory`,
  `enforced_at.component = workspace` and a `workspace_listing` verification (what was removed, or
  what was kept, and the session id). A failed reset aborts the run. The harness's own storage
  inside the container is not touched; a new session is a fresh context and that limit is recorded.
- **Tasks run in order per agent; agents run at the same time** (ADR-0011). Steps are counted per
  agent, native logs are kept per task.
- **Protected files.** A task directory may contain `protected/`; the workspace manager copies it into
  the workspace at task start and records the hashes (`task.protected`); after the task it records
  what changed (`judge.state_check`). The judge turns changes into flags (ADR-0013).
- **Mid-run changes go through `mesocosm conditions set`.** Only what an enforcement point can change
  while agents work is accepted: today the budget, at the gateway, whose receipt carries the previous
  value and is also written to the gateway's own log. Everything else is `rejected_unsupported`
  with a receipt. Both receipts are appended to the bundle.
- **Chains.** `mesocosm chain` runs a plan of manifests in sequence. Infrastructure failures
  (`failed`, `failed_isolation`) are retried after a delay; a manifest that contradicts the world
  (`failed_channel_verification`, `failed_exposure_verification`) is deterministic and is not. Every
  attempt is one line in the chain log; `--resume` skips runs the log records as finished. Without a
  fixed `--compose-file` the stack is rendered and reconciled per manifest.

## Consequences

- Rewards belong to the run that earned them.
- `run pause` / `run resume` from the operation contract remain `unsupported`: OpenCode has no API
  to suspend a turn. Stopping a run is killing it; chaining restarts the next one.
- Reconciling the stack per manifest recreates the services container when its configuration
  changes, so exposure schedules count from that stage's start.
