# ADR-0050: Native service worlds, explicit measurement scope and calibration

Status: accepted. Extends ADR-0042 and enforces the research/fixture boundary of ADR-0044.

## Context

Running N copies of a filesystem task does not make its application or database a shared world.
Research needs original task services, unchanged success criteria, and evidence that distinguishes
model output, executed tools and actual task performance. Authored histories and behavior scripts
must not silently enter the primary research path. A zero score alone cannot diagnose a model,
harness, environment or verifier failure.

## Decision

`profile: research` is the default. Known authored fixture mechanisms fail validation and are checked
again when tasks are materialized or streamed. They remain available under `profile: fixture` for
regression work. This boundary detects known platform extensions; it cannot authenticate arbitrary
external content. Original task environment inputs, application code and database initialization stay
unchanged.

The native runtime imports a validated subset of the original task's Compose services. Each agent
still has a distinct harness process, workspace, inference credential and internal network interface.
`task_services: isolated` creates per-agent sidecars. `task_services: shared` attaches one service
group to the participating agents' internal networks, while preserving distinct agent containers.
Shared mode requires comparison scheduling and reset memory. An arbitrary long-lived application
inside the agent's main process is not a supported shared service.

Sidecars retain supported startup commands, healthchecks, environment and storage. Main application
readiness and harness readiness both gate execution. Host network/privileges, published ports,
undeclared network access and unsupported Compose semantics fail explicitly. Main filesystem mounts
cannot hide the native workspace or controller-mounted harness files. Runtime-owned services and
volumes appear in a complete companion Compose file so teardown can account for them.

Before original Compose services start, a credential-free, disconnected base-image probe must establish
private PID containment using same-kernel host evidence. Unknown containment or the historical Colab
shared-procfs adaptation prevents startup. This requirement cannot be bypassed by adaptation flags.

The services admin API requires the control credential even from previously unknown addresses.
OpenCode's upstream HTTP Basic authentication uses a different per-agent credential; sidecars receive
neither credential. This protects harness endpoints from unauthenticated network peers, not from the
root user inside that harness's own container. Shared application services remain possible information
relay channels. Built-in `services.access` permissions do not govern arbitrary external application
routes.

Original separate verifiers resolve their declared image, tests Dockerfile or fresh task base image.
Only declared checked artifacts are transferred at their original source paths, plus the original
optional `/logs/artifacts` convention. No live workspace or modified agent root filesystem is inherited.
During grading, owned agent containers are paused; task sidecars remain available on an internal
network. Filesystem snapshot verification remains available for the earlier profile.

Shared service grading requires a separate verifier with no per-agent artifact dependency. After all
participants finish, one verifier invocation creates one team measurement ID. Per-agent outcomes
reference that ID; aggregation counts it once per trial. It does not establish individual credit.
Policy-weight training rejects this new shared-service path until its rollout contract supports team
measurement provenance. Existing isolated-task RL and optional context feedback remain separate paths.

`diagnose` reports recorded tool calls, harness execution, monitoring/admission failures and original
scores without assigning an unsupported cause. `env calibrate` runs the original verifier in an empty
task environment, destroys that environment, then executes the author's unchanged solution and verifier
in a fresh environment. The solution never enters an agent research rollout. Calibration reports the
original score, expected-score comparison, task fingerprints and cleanup evidence. Its status is not
agent acceptance or learning evidence.

Owned workload cleanup must complete before the immutable run summary is written. A failed cleanup
prevents a successful run result and therefore policy updates; an earlier failure is preserved too.

## Verification and limits

See [research runtime](../research-runtime.md) and [capability matrix](../capability-matrix.md) for tests
and the exact supported subset. Command-level and control-plane tests cover these contracts. The new
Compose/separate-verifier combination, reference calibration, and shared-service world have no new
standard Linux Docker acceptance with N >= 3 real agents. Earlier adapted Colab execution does not
validate this extension. Task GPUs, other harnesses, full process snapshots, shared-main RPC, universal
information-flow isolation and learning effectiveness remain outside the verified scope.

## Upstream interfaces

- [Harbor multi-container tasks](https://docs.harborframework.com/tasks/multi-container)
- [Harbor separate verifiers](https://docs.harborframework.com/tasks/separate-verifier)
- [OpenCode server authentication](https://opencode.ai/docs/server/#authentication)
