# Operation contract

The current public surface is the `mesocosm` CLI. Its implementation lives in `mesocosm.cli.main`;
Python users can call the underlying modules, such as `core.run` and `core.experiment`.
There is no implemented `core.api` facade, general HTTP/MCP control API or training export.

Changing a supported variable is a configuration operation. Introducing a new variable or service
requires implementation, validation and a capability declaration. A prompt description is not an
enforcement mechanism.

## Current operations

| Command | Result |
|---|---|
| `capabilities` | Supported interfaces and a link to evidence/limits. |
| `world validate <manifest>` | Schema/compatibility errors, warnings, resolved versions and digest. Unknown schema fields fail. |
| `env compose <manifest>` | Rendered Docker topology and agent configuration. This does not start a world. |
| `run <manifest>` | One run using a prepared topology: isolation checks, budgets, real harness execution and task verification. Native execution is the default. |
| `experiment run <experiment>` | Fresh worlds and workspace directories per trial, repeated named conditions, full preflight, all outcomes retained. |
| `experiment report <directory>` | Condition summaries with task scores, incentives, monitor judgments and missing observations separated. |
| `report <bundle>` | Recorded calls, tokens, task results, flags and run status. |
| `pause` / `resume` / `stop <bundle>` | Gateway admission controls and run lifecycle records; pause is not suspension of a running shell command. |
| `conditions set <bundle> <agent> budget <json>` | Update a gateway budget with an enforcement receipt. Other mid-run conditions are rejected. Initial budgets belong in `agents[].budget`. |
| `observe serve <bundle>` | Live/replay observer over available records. |
| `snapshot create` / `snapshot restore` / `fork` | Declared filesystem/service state and coverage; not complete process/session/training-state restoration. |
| `judge monitor <bundle>` | Append posthoc monitor results with configurable scope, evidence and budgets. |
| `flags list` / `flags review` | Candidate detections and reviewer verdicts, kept separate. |
| `export inspect <bundle>` | Inspect evaluation JSON with explicit omitted data; not an RL trajectory export. |

Use `mesocosm <command> --help` for exact arguments. `chain` and deprecated `scenario` remain available
for legacy workflows; new condition comparisons should use `experiment`.

## Receipts and records

Existing enforcement operations append receipts with requested values, enforcement component,
verification evidence and an explicit result: `applied`, `rejected_unsupported`, `rejected_budget`,
`rejected_policy` or `failed`. There is no `approximated` result.

Not every command produces a receipt. Experiments persist a trial plan, rendered manifests and
outcomes. Reward allocation appends `learning.reward` events with source evidence while preserving
`verifier.reward`. Feedback attachment and completed harness delivery have separate events. Monitor
records include scope, rubric version/hash and coverage. None of these records asserts a weight update.

Experiment teardown has a 120-second client deadline. Uncertain cleanup stops subsequent trials and
records `failed_cleanup` / `lifecycle_errors` alongside completed measurements. Remaining trials stay
`not_run`; the CLI exits unsuccessfully. Killing an owned Compose client does not prove Docker removed
the world, and the controller does not automatically terminate host/container workloads to recover.

## Authority and JSON

Agents receive inference credentials and their configured sandbox interfaces. Controller credentials
and private verifier authority are outside their configured workspace. Consult the
[capability matrix](capability-matrix.md) for isolation restrictions, including Colab adaptations.

With `--json`, operation results are JSON on stdout and failures use nonzero exit codes. Experiment
validation/record errors also use structured JSON. A process killed before it can handle termination
cannot guarantee a final output object; the saved experiment plan retains unrun trials.
