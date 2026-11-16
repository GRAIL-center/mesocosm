# Development milestones

The project is a pre-alpha research runtime. Since
[ADR-0075](adr/0075-behavior-laboratory-direction.md) the direction is a laboratory for multi-agent
behavior, built in four stages: reproduce, observe, elicit, red-team. Each stage is the precondition of
the next. Earlier incident scenarios and synthetic workplace histories are retained as
[legacy fixtures](../examples/fixtures/README.md); reproducing their expected flags is not a research
milestone.

## Research stages

| Stage | Current evidence | Exit criterion / remaining work |
|---|---|---|
| 1. Reproduce | No published behavior has been reproduced. The study core of [ADR-0074](adr/0074-behavior-studies.md) (conditions, substrate contract, trial runner, typing battery, behavior card) has CPU tests with a deterministic test environment. No environment is connected and no card comes from real models. | Two positive controls. First, connect the original Emergent Collusion environment through the gateway, reproduce its published baseline, and compare runs with and without memory across episodes. Second, a naming game with and without interaction history; it has no implementation yet. For each control: the baseline effect exceeds measured run-to-run noise, removing the published condition changes the behavior in the reported direction, and request records show the manipulation reached model inputs, which needs paired replay to make the reach check decisive. A control that does not reproduce is recorded as a re-test result and is not repaired by adding cues. |
| 2. Observe | The recorded real-agent runs on the native world scored zero on every task, and the first of them observed no cooperation. | Agents complete solvable tasks at a rate fixed in advance. Detectors are checked against the stage-1 cases before their flags are read. Hosted models run through the gateway; multi-provider deployment is unverified. |
| 3. Elicit | Enforced conditions (budgets, access, memory, incentives, monitoring) have tests and partial real-agent evidence, listed below. No behavior has been elicited. | Run-to-run noise measured before any comparison is interpreted. Every case carries the origin of the behavior. Forked reruns from a saved live state, unsupported today, for attribution inside a run. |
| 4. Red-team | Containment through the two gateways and before-tool monitor intervention exist. No red-team study has been run. | A search for conditions under which a team behaves in misaligned ways, monitor misses and false alarms measured on those cases, and a release review for elicitation recipes. |

Until stage 1 exits, no new designer tools, training providers or integrations are added. Fixes, and
what a positive control needs, are in scope.

## Runtime acceptance

The native runtime stays what it was: condition-led experiments over real tasks with at least three
real agents. Its acceptance is functional system composition, not behavior.

| Milestone | Current evidence | Exit criterion / remaining work |
|---|---|---|
| Real multi-agent execution | The [2026-10-19 A100 run](native-multiagent-evidence.md) executed three independent OpenCode sessions, real shell tools and original external-task verifiers. All task scores were zero. | Execution criterion met for the documented native profile; successful task performance, collaboration and broader task compatibility need separate evidence. |
| Enforced environment conditions | Gateway budgets, network/channel controls, task-boundary memory, service permissions and receipts have tests; selected earlier controls have fixture-based Docker evidence. | Establish repeatable real-task baselines and verify each selected condition end to end. |
| Repeated condition experiments | Generic runner and reporting have tests. [Two A100 conditions](condition-experiment-evidence.md) ran once each with separate worlds; Colab teardown required operator intervention. | Verify unattended lifecycle and repeated trials. The new bounded-cleanup guard has local tests; it does not repair Colab process isolation. `order_seed` alone does not establish reproducibility. |
| Separate incentives from evaluation | Verifier-derived individual/team-mean records ran with three real sessions per condition; all scores were zero. Peer sources, mixed allocation and context feedback have tests. | Verify peer sources and feedback delivery with real agents, including missing measurements. Different allocation outputs currently have unit-test evidence. No target behavior is required. |
| Compare monitoring systems | Configurable posthoc review and bounded before-tool intervention are implemented. The released OpenCode hook has [real-model allow/deny/abort evidence](training-monitor-evidence.md), including a falsely allowed read; malformed/unknown decisions, global budgets and failed-run evidence have tests. | The tested intervention boundary works; establish monitor accuracy separately. Detection quality and adaptive reviewer training are later studies; the gate mediates top-level tool calls. |
| Persistent operation and records | Queueing, snapshots, CLI analysis and activation recomputation have tests and earlier fixture evidence. | Validate long real workloads and document uncaptured state; existing snapshots do not establish exact process or training continuation. |
| Actual policy training | `train run` implements complete frozen-policy worlds, exact rollout validation, a real REINFORCE LoRA update, checkpoint activation and final policy verification. The [native A100 FP16 run](training-monitor-evidence.md) completed one actual update and a second three-agent world using the new checkpoint, with final probability verification. `none` and `in_context` remain fixed-weight modes inside an ordinary world. | The tested training loop works with documented operator-assisted Colab cleanup. Learning benefit requires a separate held-out evaluation; a nonzero parameter update alone is not that evidence. |
| Later research studies | Outside current implementation acceptance; no completed general misalignment study is established by the infrastructure work. | When conducting a study, report task performance, incentives, reviewed behavior and monitor errors across conditions, with uncertainty and complete trial accounting. |

The new experiment, reward and monitoring implementations do not inherit live validation from the older
A100 run. The new condition run validates only its documented paths: one team-mean participant used no
tool, monitoring was disabled and learning used fixed weights. Current evidence and unsupported settings are listed in the [capability matrix](capability-matrix.md).
The subsequent training run has separate evidence for a real policy update and live monitor calls:
all three agents used the active model in each world, but only one executed tools. Its original
task scores were also all zero. It does not establish tool use by all three participants or a learning benefit.
Tests using fake inference or authored tasks validate plumbing, not emergent behavior.

## Subsequent extensions

The [research-control implementation](research-controls.md) adds internal access graphs, team quotas,
owned-workload stopping, explicit evidence links, joint before-tool review, input drift checks and
held-out evaluation. Its regression tests establish these control paths; new standard Linux containment
and repeated real-agent acceptance remain to be run. Earlier Colab evidence does not verify these changes.

Additional native task profiles and harnesses, real application-service integrations, broader training
algorithms and adaptive monitor training wait until stage 1 exits, unless a positive control needs one.
Each extension needs an enforceable interface, clear unsupported cases and evidence on the actual
execution path. There is no deadline or funded schedule implied by this list.
