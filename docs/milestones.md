# Development milestones

The project is a pre-alpha research runtime. The current direction is condition-led experiments over
real tasks and at least three real agents. Current acceptance is functional system composition, not
behavior reproduction. Earlier incident scenarios and synthetic workplace histories
are retained as [legacy fixtures](../examples/fixtures/README.md); reproducing their expected flags is
not a research milestone.

| Milestone | Current evidence | Exit criterion / remaining work |
|---|---|---|
| Real multi-agent execution | The [2026-10-19 A100 run](native-multiagent-evidence.md) executed three independent OpenCode sessions, real shell tools and original external-task verifiers. All task scores were zero. | Execution criterion met for the documented native profile; successful task performance, collaboration and broader task compatibility need separate evidence. |
| Enforced environment conditions | Gateway budgets, network/channel controls, task-boundary memory, service permissions and receipts have tests; selected earlier controls have fixture-based Docker evidence. | Establish repeatable real-task baselines and verify each selected condition end to end. |
| Repeated condition experiments | Generic runner and reporting have tests. [Two A100 conditions](condition-experiment-evidence.md) ran once each with separate worlds; Colab teardown required operator intervention. | Verify unattended lifecycle and repeated trials. The new bounded-cleanup guard has local tests; it does not repair Colab process isolation. `order_seed` alone does not establish reproducibility. |
| Separate incentives from evaluation | Verifier-derived individual/team-mean records ran with three real sessions per condition; all scores were zero. Peer sources, mixed allocation and context feedback have tests. | Verify peer sources and feedback delivery with real agents, including missing measurements. Different allocation outputs currently have unit-test evidence. No target behavior is required. |
| Compare monitoring systems | Configurable posthoc scope, evidence visibility, rubric and budget handling have tests. | Verify evidence selection, budgets and failure reporting with a real model. Detection quality is a later empirical study; live intervention remains unimplemented. |
| Persistent operation and observation | Queueing, snapshots, replay and activation recomputation have tests and earlier fixture evidence. | Validate long real workloads and document uncaptured state; existing snapshots do not establish exact process or training continuation. |
| Actual policy training | Not implemented. `none` and `in_context` both keep weights fixed. | Integrate a real trainer, verify rollout/action/reward provenance and changed checkpoints, activate them for subsequent real-agent episodes, and evaluate on held-out tasks. |
| Later research studies | Outside current implementation acceptance; no completed general misalignment study is established by the infrastructure work. | When conducting a study, report task performance, incentives, reviewed behavior and monitor errors across conditions, with uncertainty and complete trial accounting. |

The new experiment, reward and monitoring implementations do not inherit live validation from the older
A100 run. The new condition run validates only its documented paths: one team-mean participant used no
tool, monitoring was disabled and learning used fixed weights. Current evidence and unsupported settings are listed in the [capability matrix](capability-matrix.md).
Tests using fake inference or authored tasks validate plumbing, not emergent behavior.

## Subsequent extensions

Prioritize extensions when a concrete study needs them: additional native task profiles and harnesses,
real application-service integrations, live monitor interventions, and policy training. Each extension
needs an enforceable interface, clear unsupported cases and evidence on the actual execution path.
There is no deadline or funded schedule implied by this list.
