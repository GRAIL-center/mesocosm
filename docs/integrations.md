# Connect a benchmark or execution system

Mesocosm has two extension points: **task sources read original benchmark inputs; execution backends
run complete worlds**. They are installed Python packages. A new benchmark does not need a special
case in the core registry or a rewritten copy of its problem and evaluator.

| What changes? | Implement | Existing behavior you retain |
|---|---|---|
| Task format, dataset lookup or provenance | A task source | Packaging, input fingerprints, experiments and the compatible execution backend |
| Browser/API actions, environment lifecycle, evaluator or agent interface | A task source and an execution backend | Authenticated submission, durable job state, serial scheduling, cancellation and result transfer |
| Another collection already in supported Harbor format | Use `source: harbor` and `execution.backend: docker` | The existing native Docker/OpenCode path, subject to its [runtime limits](research-runtime.md) |

A source can declare `execution_kind: harbor` when its returned configuration actually satisfies the
existing native task contract. A browser environment with a different lifecycle needs its own backend.
The backend owns its agent interfaces; the extension contract does not translate arbitrary agents into
OpenCode sessions. Registration alone establishes neither benchmark support nor containment.

## Register an installed package

Use standard [Python packaging entry points](https://packaging.python.org/en/latest/specifications/entry-points/).
For example, a separately developed integration could declare these entries in its `pyproject.toml`:

```toml
[project.entry-points."mesocosm.task_sources"]
my-benchmark = "my_integration.source:BenchmarkSource"

[project.entry-points."mesocosm.execution_backends"]
my-backend = "my_integration.backend:BenchmarkProvider"
```

Each target is a zero-argument factory returning a provider with `api_version = 1`. A class with a
zero-argument constructor works. IDs use lowercase letters, digits, dots, underscores and hyphens,
start with a letter, and have at most 64 characters. Duplicate IDs, missing packages and incompatible
API versions are rejected. Built-in IDs `harbor` and `docker` cannot be replaced.

Install the trusted integration and its dependencies into both the client and worker environments.
Mesocosm never installs submitted dependencies or loads a plugin from an uploaded task directory.
Task programs and evaluators remain executable inputs handled by the selected backend. Keep provider
factories free of workload startup: validation and identity inspection also construct providers.

```bash
uv run mesocosm integrations list --json
uv run mesocosm integrations check /path/to/world.yaml --json
uv run mesocosm capabilities --json
```

The inventory reports registration metadata. It does not import every discovered package or certify
that an advertised integration works. `check` resolves selected providers and materialized tasks
without GPU, Docker or model calls; a compatible contract still requires runtime preflight.

## Implement a task source

The interface is [`TaskSource`](../src/mesocosm/integrations/tasks.py). The following is an interface
skeleton, **not an implemented benchmark adapter**:

```python
from pathlib import Path
from typing import Any
from mesocosm.integrations.tasks import Task

class BenchmarkSource:
    api_version = 1

    def resolve(self, dataset: str, *, roots: list[Path],
                limit: int | None = None) -> list[Path]:
        raise NotImplementedError("Resolve materialized task roots deterministically")

    def load(self, path: Path, *, dataset: str | None = None,
             name: str | None = None) -> Task:
        raise NotImplementedError("Read the original task and return its execution contract")

    def research_errors(self, task: Task) -> list[str]:
        raise NotImplementedError("Reject known authored substitutes for this source")

    def fingerprint(self, task: Task) -> dict[str, Any]:
        raise NotImplementedError("Describe original evaluator and environment identity")
```

`resolve` selects already materialized inputs under the supplied roots, with stable ordering and an
explicit limit. `load` returns a `Task` rooted at exactly the requested directory. Its instruction is
the original agent-facing problem, `config` describes the source's real execution inputs, and
`execution_kind` identifies the contract a backend must understand. The common loader binds `source`
to the selected provider. A plugin cannot redirect the returned task to a different input root.

Keep the full task input closure inside that root. Read relative references from the task root, not
from the process working directory or an unrecorded local cache. Packaging copies the complete tree
without changing its bytes or modes; it rejects links and special files. A source that needs assets
elsewhere must first materialize a complete task directory. Dynamic remote services need explicit
version/reset evidence from the backend; a local file hash cannot establish their state.

`research_errors` implements source-specific research/fixture checks. It must not reinterpret an
unrelated format using Harbor filenames. `fingerprint` adds JSON-compatible evaluator/environment
metadata and should include an `execution_sha256` when a stable execution-input identity is available.
Mesocosm independently hashes the complete tree and the instruction. Do not claim verified originality,
image resolution or deterministic behavior without corresponding evidence. The built-in
[`HarborSource`](../src/mesocosm/adapters/harbor/source.py) is a complete implementation to inspect.

## Implement a whole-world backend

The factory interface is [`BackendProvider`](../src/mesocosm/execution/backends.py):

```python
from mesocosm.core.manifest import WorldManifest
from mesocosm.execution.backends import BackendContext
from mesocosm.execution.protocol import ExecutionBackend

class BenchmarkProvider:
    api_version = 1

    def validate(self, world: WorldManifest) -> list[str]:
        raise NotImplementedError("Validate options and every requested control")

    def create(self, context: BackendContext) -> ExecutionBackend:
        raise NotImplementedError("Construct an operator-configured backend")
```

`validate` is a resource-free compatibility check. Return errors for unknown options and every
requested feature the implementation cannot enforce: harnesses, task kinds, isolation, memory,
communication, budgets, monitoring or learning. Accepting a manifest is not permission to silently
ignore its controls. Use actual permissions and resources; do not substitute prompt instructions.

This obligation includes the [repeated-interaction controls](experimental-variables.md), not only the
original scalar budgets and reset/carry policy. Check them explicitly in provider validation:

| Requested control | Backend obligation or explicit rejection |
|---|---|
| `history` and `learning.feedback` | Enforce fresh-session/history selection and actual delivery boundaries; retain source records, bounded payloads and missing/delivered distinctions. Native history currently requires OpenCode message semantics. |
| Compositional rewards | Preserve the original evaluator and independently measurable component inputs, agent/task attribution, missing values and allocation rules. Do not invent usage or approval scores. |
| Reviewer matching and reputation | Enforce actual review targets and publish only measured historical observations. A textual instruction alone does not restrict verdict submission. |
| Selective task assignment | Dispatch only the recorded cohort after all-agent calibration; retain skipped participants and actual work opportunities. |
| Artifact exchange | Capture the actual declared task bytes at a verified stable boundary before grading, preserve immutable provenance, and apply publication/read policy. Host paths alone do not prove they are the files the native task uses. |

These capabilities are not supplied automatically by the worker or task-source interface. Reject
unsupported combinations before workload allocation, including features a backend supports separately
but cannot combine faithfully. Native limitations, including shared-service artifact exchange and
policy-training exclusions, remain in force when reusing that path. See
[ADR-0056](adr/0056-measured-interaction-conditions.md) and the [capability matrix](capability-matrix.md).

`create` receives the private worker root and operator options. The returned object implements:

| Method | Required behavior |
|---|---|
| `async capabilities()` | Return a dictionary whose `status` is `supported` only when deployment prerequisites are available. State the actual supported job kinds and limits. |
| `async execute(job_dir, input_dir, kind, entrypoint)` | Execute the submitted `world` or `experiment`, preserve evidence, and return only after owned workloads are cleaned. Reject an unsupported kind before launching anything. |
| `async recover(job_dir)` | Inspect durable ownership and clean interrupted workloads. Return `{"status": "complete", ...}` only after verifying cleanup. Do not rerun the observation. |

Persist resource ownership **before** creating processes, containers, sessions or remote jobs. Handle
`asyncio.CancelledError` without abandoning cleanup. Recovery must work after process restart, when
in-memory handles are gone. Never delete resources based only on a broad name prefix. Follow the
existing [Docker implementation](../src/mesocosm/execution/docker.py) for inspected ownership and bounded
teardown, adapting the mechanism to the actual execution system.

An execution result includes a `status` and a `cleanup` dictionary. With `cleanup.status: complete`,
the worker maps `status: ok` to `completed`, `status: cancelled` to `cancelled`, and other statuses to
`failed`. An outstanding stop request also produces `cancelled`. Missing or unsuccessful cleanup takes
precedence: it becomes `failed_cleanup` and blocks admission. `ok` means the execution/evidence contract
completed, not that the agents solved the task.

Write standard [`RunBundle`](../src/mesocosm/core/records.py) evidence under `job_dir/runs/run_*`:
resolved configuration, integration identities, original model and harness records, tool inputs and
results, original verifier output, enforcement receipts, termination reasons and cleanup evidence.
The result exporter requires `manifest.json` and `events.jsonl` for each exported bundle. These files
alone do not establish evidence completeness. Keep original trajectories and verifier artifacts
outside agent write access; separate task scores, incentives, monitor assessments and reviewed claims.
Missing evaluation evidence remains unknown, not a successful zero score.

For experiments, preserve every scheduled variant and repetition, input checks, incomplete records
and failed trials. The existing [`run_experiment`](../src/mesocosm/core/experiment.py) supplies this
scheduling contract when the backend supplies a real `run_one` callback. Outcome-based retries or
stopping on a desired behavior change the observation and must not replace this contract.

## Select and deploy

An integration-specific world keeps the normal agent, model and task declarations. For example, these
fields select the **placeholder providers above**, which must first be implemented and installed:

```yaml
execution:
  backend: my-backend
  options: {}              # Recorded experiment parameters, validated by this provider.
tasks:
  - source: my-benchmark
    path: ./materialized-task
```

`execution.options` belongs to the experiment and travels with the manifest. Host addresses,
credential references, deployment directories and other operator settings go in the private file
passed to `worker serve --backend-config`; its mapping becomes `BackendContext.options`. The custom
provider must validate that file too. Docker-specific flags are refused for other backends.

```bash
uv run mesocosm worker serve \
  --backend my-backend \
  --backend-config /private/operator.yaml \
  --root /private/worker-state
```

Use the normal [submission and result commands](execution.md). One worker uses one backend; every
effective experiment variant must select it. Direct `run`, Compose and fork commands retain the Docker
path and reject foreign backends. The native `baseline` acceptance mode and policy-weight training are
also Docker/OpenCode paths. An external source may use them when it produces compatible native inputs;
custom-backend baseline/RL support has not been generalized.

## Identity and validation

Portable submissions contain `integration_lock` entries for each selected task source and backend.
They record API version, distribution, version, entry point and package-content hash, including
editable source. Every condition receives its effective lock. Client packaging and worker admission
reject missing or different providers before execution; queued jobs are checked again before launch.
An explicitly supplied lock is checked before re-pinning. To deliberately change providers in a
condition, remove the old lock in that condition and create a new package.
Older submission archives without integration locks must be packaged again with this client; their
missing identities are not silently inferred by a worker. Existing durable Docker cleanup remains available.

Do not edit installed provider code in a running process. Restart the client/worker with the intended
packages before preparing or accepting a new observation. The worker binds its instantiated backend
identity and refuses a new installation identity. External-backend recovery requires the recorded
owner's provider identity; a different provider cannot take over cleanup. Docker can clean its existing
inspected ownership inventory after a code change without loading task plugins.

Code hashes do not pin the entire dependency environment, remote service state, model weights or
sampling randomness. Keep dependency lockfiles and deployment/version evidence separately. A matching
lock establishes consistent declared code and inputs, not complete reproducibility or security.

Contract tests cover [task dispatch](../tests/test_task_sources.py),
[portable inputs](../tests/test_integration_inputs.py),
[backend admission/recovery](../tests/test_execution_backends.py) and
[worker lifecycle](../tests/test_execution_worker.py). The separately installed
[CPU fixture](../tests/fixtures/integration_plugin/README.md) tests extension plumbing only: it runs
no agents and establishes no containment or research acceptance. Each real integration still needs
at least three real agents, actual tools/actions, the original evaluator and trajectory inspection.
SWE-bench and WebArena adapters are not supplied by this interface change; see the
[task source comparison](task-sources.md) for the work still required.
