# Research execution and original task environments

Research execution uses actual agent processes, original task inputs and independent task verifiers.
The runtime rejects known authored fixture mechanisms instead of relying only on example-directory
labels. These checks do not prove that arbitrary instructions or external images are authentic.
Record and review their provenance separately.

## Research and fixture profiles

World manifests default to `profile: research`. Add `profile: fixture` only for regression and
plumbing runs. A fixture remains a fixture even when it uses real model calls or Docker containers.
Strict baselines and the policy-training controller require the research profile.

| Input or mechanism | Research profile |
|---|---|
| Actual Git, chat, board, wiki, package index and text object storage | Allowed |
| Real request/token/concurrency budgets, service permissions and resource limits | Allowed |
| Original source code, database initialization and other inputs inside the task environment | Preserved |
| Simulated `services.clock`, `services.ledger` or canned-secret `services.honeypot` | Rejected when enabled |
| Authored `services.seed`, nonempty `services.cache.seed`, trap accounts | Rejected |
| Legacy peer-review correctness protocol | Rejected; use the explicit ratings/reward interface |
| `tasks[].side_task`, side-task-aware monitoring | Rejected |
| Task-root `side_task.md`, `tests/side_test.sh`, `agents/`, `instances/`, `protected/`, authored `rounds` | Rejected |
| Task metadata `fixture = true` | Rejected |

Checks inspect the task-root extensions, not similarly named directories inside the original
`environment/` build context. They run when local task manifests are validated and again at execution,
including materialized dataset tasks and tasks received through the persistent queue. Nothing strips
or rewrites the original task to make it pass validation.

Archived worlds under `examples/fixtures/` explicitly select the fixture profile, and their task TOMLs
carry `metadata.fixture = true`. Relocating an archived task therefore retains its fixture marker.
Legacy staged scenarios require fixture worlds and cannot switch a stage into research. The CLI's
`--task-runtime fixture` also requires the fixture profile.

The compatibility key and hostname `cache` still refer to a **text object store**. It stores actual
text values by key and records reads and writes; it does not implement a build cache or package cache.
Wiki documentation and ordinary service accounts remain available. Their contents are operator inputs,
not proof of real organizational history.

## Control-plane authentication

All world-service `/admin/*` endpoints require the configured bearer control key. A missing key closes
the admin surface. Known agent addresses are denied even if they send the correct key; an unknown
address, including a task sidecar, does not imply control-plane authority. Failed requests return 404
and are recorded without the credential.

Compose supplies the key only to the services/control infrastructure; `ServicesAdmin` sends it on
control calls. Service roles such as a board editor's credentials do not grant control-plane access.
This protects the service administration API; it is not a general guarantee against container escape,
application-level relays or compromised operators.

`env compose` uses `gateway.run_label` from the manifest, defaulting to `spike-docker` when unset.
An explicit `--run-label` must match it so generated harness credentials and runtime clients agree.
Regenerate existing Compose files and agent configuration when upgrading to authenticated harnesses.

## Separate original verifiers

The separate verifier follows Harbor's documented mode and image selection rules:

1. Set `verifier.environment_mode = "separate"`, or provide `[verifier.environment]`.
2. A verifier-specific `docker_image` takes precedence over `tests/Dockerfile`.
3. With neither definition, start from the pinned original agent **base** image. The agent's modified
   root filesystem and harness image are not used.

Dedicated verifier images must contain their own `/tests/test.sh`. The base-image fallback mounts
the task's original `tests/` read-only. The actual script's shebang determines the interpreter: for a
dedicated image, the controller reads the script from that fresh verifier container and records its
hash. A script without a shebang uses `/bin/bash`. Tests and the reference solution are never
introduced into a research agent's workspace.

The base-image fallback also preserves the original Compose main user, working directory and
environment variables, before applying explicit verifier overrides. Dedicated verifier images retain
their own settings. Generated harness authentication and controller credentials are never inherited;
reserved `OPENCODE_*` and `MESOCOSM_*` variables in the original main environment are refused.
The verifier's environment settings select its CPU/memory limits. If those limits cannot be enforced,
preparation fails. Unknown fields,
GPU/storage requirements, verifier-specific Compose stacks and public/allowlisted verifier internet
access are currently refused. A live-service verifier can connect to the runtime-selected internal
task network; a declared `no-network` policy cannot be silently overridden. It runs while the task's
sidecars remain available, after agent work is frozen by the runtime.

See the original [Harbor separate-verifier specification](https://docs.harborframework.com/tasks/separate-verifier)
for mode resolution and image precedence. Support here is an explicit subset of that interface.

### Artifact transfer

Declare agent outputs in the task's original top-level `artifacts` array:

```toml
artifacts = [
  "/app/report.json",
  { source = "/app/output", destination = "output" },
]

[verifier]
environment_mode = "separate"
```

String paths and objects containing `source` and optional `destination` are supported. The host
destination controls where the snapshot is retained; the verifier receives each artifact at its
original absolute source path. The current Harbor-compatible automatic `/logs/artifacts/` transfer
is also attempted; absence is recorded. A missing explicitly declared artifact fails preparation.
Exclusion patterns, sidecar artifact sources and overlapping paths are refused rather than ignored.
See [Harbor artifacts](https://docs.harborframework.com/tasks/artifacts) for the upstream format.

Transfers reject symlinks, hard links, special files, setuid/setgid/sticky modes and paths overlapping
verifier/system authority such as `/tests` or `/logs/verifier`. They retain hashes and permission modes
for copied files. Checked snapshots are copied into a fresh verifier container; no live agent workspace
is mounted. Docker initially owns copied files as root, so a control-plane preparation step assigns
only the transferred paths to the original verifier user before running the original test script.

Verifier output has a dedicated `/logs/verifier` mount. Isolated verification can mount a supplied
world-state export read-only; shared service-only verification never mounts an individual agent's
`world.json`. The host record is retained, and `world_state_mounted` records whether it was provided.
Image identity, selection, resource arguments, environment variable names and artifact-transfer receipts
are saved separately from task scores; receipts omit environment-variable values. Preparation failures
attempt owned-container/image cleanup. If that cleanup cannot be confirmed by the Docker operation,
the failure is explicit and `preparation-cleanup.json` preserves the unresolved actions.

Shared-team grading currently requires service state only. It refuses declared per-agent artifacts and
nonempty automatically published artifacts so that choosing one agent cannot silently determine the
team's score.

## Evidence boundary

The profile, authenticated service administration and separate-verifier transfer paths have focused
automated regression tests in `test_research_profile.py`, `test_services_admin.py` and
`test_task_verifier.py`. Command-contract tests use fake Docker responses and filesystem fixtures.
They are not evidence of a new real three-agent acceptance run, reference-solution success or improved
agent performance. Actual Docker/GPU integration evidence must be recorded separately.

## Original application and database services

Mesocosm reads the task's original `environment/docker-compose.yaml`, `docker-compose.yml`,
`compose.yaml` or `compose.yml`; ambiguous definitions fail. It validates the supported subset before
Docker interprets it. There is no generated application, replacement database or authored incident.
The original `main` image/build is extended with the unchanged OpenCode harness as before.

Choose the state boundary in the world manifest:

```yaml
profile: research
mode: comparison
memory: reset
isolate_agents: true
# Each agent keeps a separate process, workspace and model session.
task_services: shared  # isolated gives each agent its own service group instead
agents:
  - id: agent-1
  - id: agent-2
  - id: agent-3
```

This fragment supplements an existing model/gateway/task configuration. The external task must already
contain the actual services and original separate verifier needed to grade their state. Shared mode
requires those services; pointing it at a plain filesystem task fails. Isolated sidecars require distinct
agent networks so the same original DNS names resolve to each agent's own services.

| Scope | Task sidecars | Agent filesystem | Original measurement |
|---|---|---|---|
| `isolated` (default) | One group per agent | Separate base workspace; original child mounts retained | Per-agent |
| `shared` | One group per task | Separate base workspace; original named child volumes may be shared | One team measurement after every agent finishes |

Supported Compose inputs include image/local build, commands, entrypoints, environment, user and
working directory, healthchecks, service dependencies, local named volumes, tmpfs and read-only inputs
confined to the task's environment directory. Service aliases are retained within the selected network;
controller/harness aliases cannot be replaced. Main mounts cannot replace the entire private workspace;
original child mounts are retained and require a separate verifier with declared artifact inputs.
Both the original main healthcheck and authenticated
harness readiness must pass. CPU/memory constraints require host enforcement, or an explicit adaptation
where the runtime supports one.

Host privileges, published ports, extra networks, host-variable interpolation, env files/includes,
external volume drivers, arbitrary host mounts, sidecar dependencies on main and other unsupported
Compose features fail explicitly. Task sidecars have internal networking only. They do not receive
control keys or harness passwords. The built-in `services.access` graph controls the built-in services;
it does not implement permissions inside an arbitrary external database or web application. Shared
application state is itself a communication route and must be included in the experiment's design.
Live monitor shared evidence currently includes the built-in service/web records; arbitrary sidecar
application internals require an explicit observation adapter. Sidecar container logs aid diagnosis but
are not a universal application audit or atomic state snapshot.

Before any original Compose service starts, a disposable container from the pinned task base image
must demonstrate a private PID namespace. The probe receives no controller credentials, network or
mounts; it bypasses the original entrypoint and healthcheck. The controller compares the container's
namespace with the Docker host's `/proc` evidence. This currently requires the controller to run on the
same Linux kernel and have permission to inspect the container's host PID. Remote-daemon uncertainty
and the historical Colab shared-procfs wrapper are rejected, even with adaptation enabled. The receipt
is retained as `task-containment.json`; this check is not a general container-escape guarantee.

The runtime pauses owned agent containers before grading. A process inside `main` pauses with its agent;
**live-service grading therefore covers sidecars, not live main-process RPC**. Shared mode supplies no
shared `main` alias. Applications requiring agents to share a single main process, or requiring a verifier
to call the paused main process, need another supported adapter contract and are not implemented here.

Shared grading only accepts a separate verifier with no declared agent artifacts and no nonempty
published agent artifacts. It waits for every participant, grades service state once and writes one
`measurement_id` with `measurement_scope: team`. `verifier.team_reward` is the canonical event;
participant `verifier.reward` events reference it. `task_measurements` contains the single measurement,
while `task_outcomes` preserves each participant's result/reference. Experiment reward aggregation counts
that measurement once per trial and treats conflicting copies as unknown. A team result gives no
individual causal credit. Policy-weight training currently rejects `task_services: shared`.

Native task resources are removed after reset-mode grading and before the next task proceeds. Carry
mode preserves its compatible environment until the next turn or final teardown. The complete generated
`<compose-file>.runtime.yaml` companion records dynamic services/volumes for lifecycle cleanup. Use the
Mesocosm lifecycle commands for teardown; a hand-written `docker compose down` against only the original
file does not describe dynamic task volumes. Failed startup, grading, cancellation and teardown remain
failures, and successful cleanup must precede the immutable run summary.

## Diagnosing zero scores and calibrating the verifier

```bash
uv run mesocosm diagnose runs/<run-id> --json
uv run mesocosm env calibrate /absolute/path/to/original-task \
  --out runs/calibration-001 --expected-score 1 --json
```

`diagnose` reads the existing bundle without model calls. It separates generated tool calls from executed
harness tools, response completion/finish reasons, gateway admission errors, monitor refusals and original
verifier results. It omits prompt contents, tool arguments and raw error text. Missing or malformed records
remain unknown. A prose statement such as “run git status” is not an executed tool call. The diagnostic
never infers that a token budget, model, monitor or harness caused a failure merely from a zero score.

Calibration is an operator diagnostic requiring the original `solution/solve.sh`, original verifier,
a Linux Docker daemon and observed private PID containment. It creates a new private output directory
and two separate environments: an untouched baseline and a fresh reference environment. Destruction of
the first environment prevents a stateful baseline verifier from contaminating the reference result.
The original solution runs with its declared interpreter, task user and working directory, with no model
requests. Staged solution files are removed before the original verifier runs again. The solution never
enters a research agent's rollout, prompt or training batch.

Scores remain on the task's original scale. `status: completed` means the diagnostic stages produced
finite measurements; `matches_expected` is a separate comparison and controls CLI success alongside
completion. Zero and nonbinary results are retained. Failed stages, changed task inputs and unverified
cleanup are reported; they do not become synthetic rewards. Tasks requiring verifier-only package
installation may use the existing `--verifier-network bridge` filesystem path. Separate verifiers and
live sidecar grading require their declared dependencies already installed and an internal network.

`env calibrate` exposes `--verifier-network none|bridge` and `--allow-unenforced-resources`, with
strict defaults. The resource opt-in records unsupported storage quotas and unavailable CPU/RAM
enforcement without changing the original task; it never bypasses PID containment. For the unchanged
`fix-git` task, both `--verifier-network bridge` and `--allow-unenforced-resources` are needed. Follow
the [native acceptance procedure](native-acceptance.md) to distinguish original assertion failures
from verifier setup errors and to keep reference-solution diagnostics separate from agent runs.

The strict repeated-baseline path still requires a materialized original `tests/test.sh` so it can
fingerprint the checker; merely declaring an image with baked tests does not establish that baseline.

## Evidence still required

Compiler, command, artifact-transfer, rendezvous, measurement and CLI tests cover these contracts. No
new real Docker calibration or N >= 3 shared-service acceptance is claimed. A standard Linux Docker
run must still show three actual harness sessions and tool execution, unchanged task inputs/verifier,
working shared services, preserved original scores and successful owned-resource cleanup. The historical
Colab wrapper removes PID isolation, so its earlier runs cannot establish that evidence. See the
[capability matrix](capability-matrix.md) and [ADR-0050](adr/0050-native-service-worlds.md).
