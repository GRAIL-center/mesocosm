# ADR-0084: Contained study environments and checks before trials start

Status: accepted · Date: 2026-11-17 · Supersedes one consequence of ADR-0079

## Context

ADR-0079 left a command environment as a plain host process and named the missing piece: an environment
whose agents execute code needs a contained runner. The published Emergent Collusion code is such a case,
because agents write test files that it runs. The first real-model runs also lost three trials for good
because they started before a model server answered.

## Decision

- **A manifest can require containment.** With `"contained": true` every trial runs in a bubblewrap sandbox:
  its own user, mount, process and network namespaces, an unprivileged user, all capabilities dropped, an
  environment holding only the trial's variables, a read-only view of the system directories and of the
  manifest's directory, and write access to the trial's own directory only. `{python}` is then a bare
  interpreter without the study tool's packages.
- **The gateway is the only way out.** The sandbox has loopback and nothing else. A relay inside it forwards
  one loopback port to a Unix socket that is bound into the sandbox, and a relay on the host forwards that
  socket to the recording gateway. The environment still reads the standard `OPENAI_BASE_URL`.
- **An environment brings its interpreter with it.** A contained trial does not see the home directory, so
  the environment's interpreter and packages are built inside the manifest's directory.
- **Nothing starts that cannot finish for a known reason.** Before any trial, the commands refuse with
  `rejected_unsupported` when a contained environment's host has no usable bubblewrap, and when a model
  server does not answer its model list. A refused command creates no trial record.
- **The published Emergent Collusion code is connected** as `examples/agent-collusion`, kind `original`: the
  repository at a pinned commit, an adapter that maps conditions to its flags and reads its result file,
  and a setup script.

## Consequences

- Supersedes the consequence of ADR-0079 that a command environment is not contained and that a contained
  runner does not exist. An uncontained environment remains a plain host process.
- The sandbox shares the host's kernel and sets no CPU or memory limit; the trial timeout is the only bound.
  The system directories under `/usr` are visible read-only. On hosts that forbid mounting `/proc`, such as
  Colab runtimes, the sandbox runs without it.
- The evidence is automated tests on a host with bubblewrap and a run of the published code against a
  scripted server that reached the gateway from inside the sandbox. A real-model run is recorded separately.
- No new Python dependency. The host needs the `bubblewrap` package for contained environments.
