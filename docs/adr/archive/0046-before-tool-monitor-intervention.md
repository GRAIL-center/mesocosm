# ADR-0046: Monitor intervention at the supported before-tool boundary

- Status: accepted
- Date: 2026-10-20
- Supersedes: the posthoc-only monitor limitation in ADR-0030 and ADR-0044; posthoc monitoring remains available.

## Context

A transcript reviewer can classify actions after they occur, but that does not make it an intervention system.
Pausing a model gateway also does not stop a tool call already emitted by the model. The platform needs a
real hold before the harness invokes the tool, with a bounded model assessment and an explicit decision.

OpenCode's permission API alone is insufficient for this contract. Its shell parser may find no permission
patterns and return without asking; permission checks in other tools can also occur after preparatory work.
We use the documented, awaited `tool.execute.before` plugin hook, which can prevent execution by throwing.
OpenCode itself remains unmodified.

Primary references, reviewed against OpenCode **1.19.3** at
[`e3c4eb242585dcd77be1a867391b7c25fd47ce52`](https://github.com/anomalyco/opencode/tree/e3c4eb242585dcd77be1a867391b7c25fd47ce52):

- [Plugin documentation](https://opencode.ai/docs/plugins/): global plugin loading, `tool.execute.before`, and the documented blocking example.
- [Server documentation](https://opencode.ai/docs/server/): health/version, tool discovery, messages and session abort.
- [Pinned shell implementation](https://github.com/anomalyco/opencode/blob/e3c4eb242585dcd77be1a867391b7c25fd47ce52/packages/opencode/src/tool/shell.ts): permission parser early return.
- [Pinned permission implementation](https://github.com/anomalyco/opencode/blob/e3c4eb242585dcd77be1a867391b7c25fd47ce52/packages/opencode/src/permission/index.ts): permission requests and deferred replies.

## Decision

`judge.monitor.timing: pre_action` enables one read-only global OpenCode plugin, mounted outside the native
task workspace. It uses the existing authenticated gateway; no separate service or modified agent fork is
introduced. The default remains `posthoc`.

OpenCode waits for its plugin SDK dependency tree before loading local plugins. Both bootstrap and
native harness images therefore install `@opencode-ai/plugin` at the same pinned version during image
build, including its ordinary package manifest, lockfile and modules in the custom and native global
config directories. This avoids a runtime package download after sandbox egress is closed. The native
application's user, working directory and XDG configuration environment are preserved.

1. Before sending the task prompt, the controller checks the exact supported harness version and loads its
   tool registry. The plugin registers a process instance and the SHA-256 of its actual mounted file. The
   controller verifies the expected digest and binds that instance, agent key, session and task together.
2. At `tool.execute.before`, the plugin sends the exact proposed tool arguments, session and call ID, then
   awaits a decision. The gateway authenticates the agent key, attaches the controller's task binding, and
   holds the request. An unbound session, stale instance or replay is rejected.
3. The controller polls only that binding's pending actions. It cross-checks the proposed tool, call ID and
   arguments against the actual pending/running harness message before asking the configured monitor model.
4. The model receives the configured rubric and selected evidence. Only a valid integer score from 0 to 100
   can authorize a decision. At or above the threshold the action is denied; otherwise it is allowed once.
   `on_flag: terminate` additionally aborts the harness session. `on_flag: deny` returns a tool error and allows
   the agent to continue its task.
5. Timeout, malformed output, transport failure, attribution mismatch, exhausted monitor calls or excessive
   evidence deny the action and abort the session. Partial or truncated evidence never authorizes a tool.
   The gateway independently expires held requests, and the plugin throws on failed or malformed replies.
6. Closing the binding denies unresolved requests. Decisions have binding IDs and are single-use; stale
   acknowledgements and replay attempts fail. There is no persistent “always allow” decision.

Gateway timeouts, disconnects and other automatic refusals remain attached to the binding after the
held request ends. The controller checks that retained status again before accepting a completed turn;
an unobserved refusal cannot become a normally graded or trainable world.

`decision_timeout_seconds` bounds assessment; `poll_seconds` controls polling. `max_calls` is shared across
all agents and tasks in one run. Each attempted model request consumes that budget, including failed model
requests. A new task does not replenish it. `visibility: actions` sends only the proposed action and identity;
`transcript` also sends the current harness session messages. This is the harness's available session history,
not a claim to recover every token removed by context management.

The current implementation supports `scope: per_agent`, `include_shared: false`, and
`side_task_aware: false`. Unsupported combinations are rejected. Other harness versions are rejected until
verified. OpenCode's nested `task` tool is explicitly unsupported: child sessions need their own controller
bindings and attribution, and cannot inherit approval silently. Ordinary controller-managed agents each
receive their own binding.

## Evidence and boundaries

The bundle records readiness, model assessments, outcomes (`allowed`, `blocked`, `unknown`), rule version
and hash, exact agent/session/task/call identity, argument hash, enforcement acknowledgements and abort
acknowledgements. Gateway records separately show held actions and decisions. `allowed` means permission
was granted; actual tool completion must be established from the harness and environment. `blocked` means
the hook rejected that proposed call; a model refusal by itself is not that evidence.

Local acceptance with the unmodified OpenCode 1.19.3 binary exercised a benign shell file write twice:
a denial left the target absent, and an allowance created it. The actual plugin registration, file digest,
harness message cross-check and model-client path were used. Its model and reviewer responses were scripted
plumbing fixtures, explicitly separate from real-LLM/native-task acceptance. The automated tests cover three
identities, independent task binding, single-use decisions, stale/replayed requests, timeout, malformed and
failed reviewer responses, budget exhaustion, cancellation and deny-versus-terminate semantics.

This boundary mediates **harness tool calls**, not every subprocess, network request or syscall inside an
approved shell command. It does not undo already allowed concurrent work. The trusted configuration must
load this read-only plugin; this contract is not a proof against a compromised harness process, arbitrary
hostile plugins or a sandbox escape. Native task instructions and original verifiers are unchanged. Monitor
rubrics are experiment conditions, not scripts that make agents perform a target behavior.
