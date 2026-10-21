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
| Compare monitoring systems | Configurable posthoc review and bounded before-tool intervention are implemented. The released OpenCode hook has local real-harness allow/deny checks; malformed/unknown decisions, global budgets and failed-run evidence have tests. | Validate real-model judgments and enforcement separately. Detection quality and adaptive reviewer training are later studies; the gate mediates top-level tool calls. |
| Persistent operation and observation | Queueing, snapshots, replay and activation recomputation have tests and earlier fixture evidence. | Validate long real workloads and document uncaptured state; existing snapshots do not establish exact process or training continuation. |
| Actual policy training | `train run` implements complete frozen-policy worlds, exact rollout validation, a real REINFORCE LoRA update, checkpoint activation and final policy verification. Actual Torch/PEFT CPU tests exercise changed/reloaded weights; `none` and `in_context` remain fixed-weight modes inside an ordinary world. | Verify the native A100 multi-world path. Learning benefit requires a separate held-out evaluation; a nonzero parameter update alone is not that evidence. |
| Later research studies | Outside current implementation acceptance; no completed general misalignment study is established by the infrastructure work. | When conducting a study, report task performance, incentives, reviewed behavior and monitor errors across conditions, with uncertainty and complete trial accounting. |

The new experiment, reward and monitoring implementations do not inherit live validation from the older
A100 run. The new condition run validates only its documented paths: one team-mean participant used no
tool, monitoring was disabled and learning used fixed weights. Current evidence and unsupported settings are listed in the [capability matrix](capability-matrix.md).
Tests using fake inference or authored tasks validate plumbing, not emergent behavior.

## Subsequent extensions

Prioritize extensions when a concrete study needs them: additional native task profiles and harnesses,
real application-service integrations, broader training algorithms and adaptive monitor training. Each extension
needs an enforceable interface, clear unsupported cases and evidence on the actual execution path.
There is no deadline or funded schedule implied by this list.
