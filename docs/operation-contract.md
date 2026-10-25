# Operation contract

The public surface is the `mesocosm` CLI and the authenticated worker job API described in the
[execution guide](execution.md). CLI implementation lives in `mesocosm.cli.main` and `mesocosm.execution`;
Python users can call the underlying modules, such as `core.run` and `core.experiment`.
There is no implemented `core.api` facade or general HTTP/MCP control API. The worker API owns finite
world/experiment jobs; it is not an arbitrary mid-run control surface. The training controller
uses its strict native rollout collector; Inspect export remains an evaluation format.

Changing a supported variable is a configuration operation. Introducing a new variable or service
requires implementation, validation and a capability declaration. A prompt description is not an
enforcement mechanism.

## Current operations

| Command | Result |
|---|---|
| `capabilities` | Supported interfaces and a link to evidence/limits. |
| `worker serve` | Authenticated Linux Docker worker with private persistent job state, serial execution and startup recovery. |
| `execution capabilities` | Remote worker prerequisite availability and admission state; not live task acceptance. |
| `execution submit <manifest>` | Durable local receipt and indexed input package, then one idempotent world/experiment submission. `--resume <receipt>` resolves an uncertain submission. |
| `execution status` / `events` / `wait <job-id>` | Remote lifecycle state, cursor-based events or terminal-state polling. Disconnecting does not cancel a job. |
| `execution stop <job-id>` | Explicit cancellation request; terminal state and cleanup receipts confirm its outcome. |
| `execution fetch <job-id> --out <new-directory>` | Hash-verified terminal result archive, including unchanged original run bundles. |
| `world validate <manifest>` | Schema/compatibility errors, warnings, resolved versions and digest. Unknown schema fields fail. |
| `env compose <manifest>` | Rendered Docker topology and agent configuration. This does not start a world. |
| `env calibrate <task> --out <new-directory>` | Original verifier before/after the author's unchanged reference solution in fresh dedicated environments; no model rollout. |
| `run <manifest>` | One run using a prepared topology: isolation checks, budgets, real harness execution and task verification. Native execution is the default. |
| `experiment run <experiment>` | Fresh worlds and workspace directories per trial, repeated named conditions, full preflight, all outcomes retained. |
| `experiment report <directory>` | Condition summaries with task scores, incentives, monitor judgments and missing observations separated. |
| `train run <training>` | Complete native worlds → validated action/reward batches → LoRA optimizer updates → immutable vLLM policy activation → final evaluation. See [training](training.md). |
| `report <bundle>` | Recorded calls, tokens, task results, flags and run status. |
| `diagnose <bundle>` | Tool-call generation, executed harness tools, monitor/admission failures and original scores, with missing evidence marked unknown. |
| `evaluate <bundle>` | Separate task/incentive/behavior/monitor/cost measurements; unknown labels remain unknown. |
| `audit anchor` / `audit verify` / `audit graph` | Detached exact-byte anchors and explicit-link coverage. |
| `judge review-assessment <bundle> <event-id> <verdict> <reviewer>` | Review an exact positive or negative monitor assessment. |
| `pause` / `resume` / `stop <bundle>` | Gateway admission controls and run lifecycle records; pause is not suspension of a running shell command. |
| `conditions set <bundle> <agent> budget <json>` | Update a gateway budget with an enforcement receipt. Other mid-run conditions are rejected. Initial budgets belong in `agents[].budget`. |
| `snapshot create` / `snapshot restore` / `fork` | Declared filesystem/service state and coverage; not complete process/session/training-state restoration. |
| `judge monitor <bundle>` | Append posthoc monitor results with configurable scope, evidence and budgets. |
| `flags list` / `flags review` | Candidate detections and reviewer verdicts, kept separate. |
| `export inspect <bundle>` | Inspect evaluation JSON with explicit omitted data; not an RL trajectory export. |

Use `mesocosm <command> --help` for exact arguments. `chain` and deprecated `scenario` remain available
for legacy workflows; new condition comparisons should use `experiment`.

## Receipts and records

Worker jobs persist identity, package digest, status revisions and cleanup results separately from
research run bundles. The same job ID accepts only the same digest. Restart recovery cleans unfinished
ownership and records interruption without replaying the observation. A cleanup failure prevents new
execution. Remote submission supports finite comparison worlds with at least three agents and required
containment; persistent streams and training jobs remain outside this API.

Existing enforcement operations append receipts with requested values, enforcement component,
verification evidence and an explicit result: `applied`, `rejected_unsupported`, `rejected_budget`,
`rejected_policy` or `failed`. There is no `approximated` result.

Not every command produces a receipt. Experiments persist a trial plan, rendered manifests and
outcomes. Reward allocation appends `learning.reward` events with source evidence while preserving
`verifier.reward`. Feedback attachment and completed harness delivery have separate events. Monitor
records include scope, rubric version/hash and coverage. None of these records asserts a weight update.
The training controller separately saves full rollout provenance, checkpoint hashes, optimizer receipts,
activation receipts and final policy consistency checks. `no_update` is explicit when no gradient update
occurs; a failed or incomplete world never supplies an update.

Pre-action monitoring holds each supported OpenCode tool call for a controller-only decision. Binding,
assessment and decision receipts retain session/call attribution, rubric hash and evidence. `deny` and
`terminate` are intervention policies; unknown assessments fail closed. This boundary does not mediate
individual subprocesses or syscalls within an approved command.

Experiment teardown has a 120-second client deadline. Uncertain cleanup stops subsequent trials and
records `failed_cleanup` / `lifecycle_errors` alongside completed measurements. Remaining trials stay
`not_run`; the CLI exits unsuccessfully. Killing an owned Compose client does not prove Docker removed
the world. The runner separately terminates owned agent containers on errors/active stop, checks actual
state and requires pre-action private-PID evidence for descendant termination. See
[research controls](research-controls.md) for the adapted-host boundary and new operations.

## Authority and JSON

Agents receive inference credentials and their configured sandbox interfaces. Controller credentials
and private verifier authority are outside their configured workspace. Consult the
[capability matrix](capability-matrix.md) for isolation restrictions, including Colab adaptations.

All worker API routes require a bearer token. Deployment assumes a trusted operator, loopback plus an
SSH tunnel or HTTPS, and private worker storage. Model-key environment names require a worker allowlist;
their upstream destinations remain submitter-controlled. Submission files and unchanged downloaded
bundles can contain credentials and task data. The API is not a multi-tenant isolation boundary.

With `--json`, operation results are JSON on stdout and failures use nonzero exit codes. Experiment
validation/record errors also use structured JSON. A process killed before it can handle termination
cannot guarantee a final output object; the saved experiment plan retains unrun trials.
