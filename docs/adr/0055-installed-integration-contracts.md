# ADR-0055: Installed task sources and whole-world execution backends

Status: accepted; extends ADR-0037, ADR-0040 and ADR-0051.

## Context

The maintainer requested a general integration structure rather than bespoke connections for three
benchmarks. Task loading, provenance, packaging and validation assumed Harbor directories. Worker
construction assumed Docker/OpenCode. Different benchmark formats can share the native execution
contract, while browser or API environments need their actual lifecycle, agent interface and evaluator.
Converting every format into a nominal Harbor directory would hide these execution differences.

## Decision

1. Register installed trusted packages through the standard Python entry-point groups
   `mesocosm.task_sources` and `mesocosm.execution_backends`. Factories implement `api_version = 1`.
   Built-in `harbor` and `docker` remain the defaults. Duplicate IDs, missing providers and incompatible
   APIs fail explicitly; uploaded files never become plugin imports or installation instructions.
2. A source resolves materialized task roots, loads the original instruction/configuration, identifies
   source-specific fixture content, and supplies fingerprint metadata. The shared `Task` carries
   `source` and `execution_kind`; its input root cannot change during loading. Mesocosm retains full
   input-tree fingerprints and copies original bytes/modes for portable submissions.
3. A backend owns a complete world: actual agent interfaces, environment startup, enforcement,
   original evaluation, records, cancellation and verified recovery. Its provider validates every
   requested feature before acquiring resources. It does not acquire OpenCode compatibility merely by
   registering. Existing native execution continues to require Harbor-compatible task inputs.
4. Manifests select `execution.backend` and recorded `execution.options`. Operator-owned deployment
   parameters are supplied separately through `worker serve --backend-config` and `BackendContext`.
   One worker serves one backend, including every effective condition in an experiment. Unknown or
   unenforceable options/controls must be rejected, never approximated with prompt text.
5. Portable manifests pin selected provider API, distribution, version, entry point and code hash in
   `integration_lock`. Validate existing locks before packaging, generate effective locks for every
   condition, and recheck on worker admission and before queued execution. Input snapshots detect
   integration drift. Changes to already loaded provider packages require process restart. These
   hashes do not cover the entire dependency environment or remote service state.
6. Persist backend ownership before execution. Recovery cleans interrupted observations without
   rerunning tasks or importing task sources. An external provider identity must match its recorded
   owner; Docker retains its inspected resource cleanup path across code updates. Missing or failed
   cleanup closes admission. Backend replacement does not authorize taking over another provider's
   resources.
7. Keep native baseline acceptance and policy-weight training on the Docker/OpenCode path. Other task
   sources may use that path only with compatible native execution inputs and existing evidence
   checks. Custom-backend baseline/RL semantics are not claimed by this interface change.

## Consequences

New integrations can be developed and distributed independently, while reusing package transfer and
worker lifecycle handling. Each backend remains responsible for enforcing accepted conditions,
preserving original trajectories/verifiers and writing standard research evidence. A method signature,
successful package transfer or reference-program execution cannot establish real-agent acceptance.

Compatibility checks run without allocating a GPU or starting Docker. Tests cover source dispatch,
package locks and drift, condition variants, provider selection, ownership and lifecycle. A separately
installed CPU distribution tests the packaging boundary with explicit non-agent fixture receipts.
Actual benchmark adapters and their N >= 3 real-agent acceptance remain separate implementation work.
No new SWE-bench or WebArena support is asserted.

See the [integration guide](../integrations.md), [task-source tests](../../tests/test_task_sources.py),
[input tests](../../tests/test_integration_inputs.py),
[backend tests](../../tests/test_execution_backends.py) and
[worker tests](../../tests/test_execution_worker.py).
