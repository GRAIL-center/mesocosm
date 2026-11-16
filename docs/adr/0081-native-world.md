# ADR-0081: The native world: enforced conditions, real agents, separate measurements

Status: accepted · Date: 2026-11-16 · Consolidates ADR-0003, ADR-0024, ADR-0026, ADR-0042, ADR-0044, ADR-0050 and ADR-0055

## Decision

- **A condition is enforced or it fails.** A condition the platform cannot enforce fails with a typed
  error (`rejected_unsupported`, `rejected_budget`). There is no approximated result, and an unsupported
  condition is never imitated with prompt text.
- **Every actor is an agent in the manifest.** No scripted posts, bots, scheduled messages or mock
  services. A scheduled condition is allowed; scheduled behavior is not.
- **Every mechanism takes N agents.** Acceptance of the native world needs at least three real harness
  sessions, real tool execution in the task's declared environment and the task's original verifier.
  Acceptance is system integration and correct records. It does not require a target behavior or task
  success.
- **Research is condition-led.** Vary real permissions, resources, incentives, memory and monitoring.
  Incident scripts, decoy scorers and invented histories stay out of the research path. Original task
  instructions and independent success criteria stay intact. Every scheduled trial is kept, and a run
  never stops because a desired flag appeared.
- **Measurements stay separate.** Task outcomes, allocated incentives, monitor assessments and reviewed
  violations are different records. Missing evidence is unknown, not zero and not safe.
- **Benchmarks come in through installed contracts.** Task sources and execution backends preserve the
  original inputs and evaluators. The core has no benchmark-name branches.
- **Commands.** Every CLI command accepts `--json` and then prints one JSON object on stdout.

## Consequences

- `profile: fixture` marks test inputs with handwritten checks. The default research profile refuses
  them. Mechanisms that only the fixture profile admits (simulated clock, ledger, honeypot, seeded state,
  account traps, side tasks) are still in the code and are candidates for removal.
- Budgets fixed in advance and incomplete outcomes are preserved. Noncompletion alone is not a platform
  defect.
