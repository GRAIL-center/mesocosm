# Development milestones

The project is a pre-alpha research runtime. The direction is a laboratory for multi-agent behavior,
built in four stages: reproduce, observe, elicit, red-team
([ADR-0078](adr/0078-purpose-stages-and-builders.md)). Each stage is the precondition of the next.

## Research stages

| Stage | Current evidence | Exit criterion / remaining work |
|---|---|---|
| 1. Reproduce | No published behavior has been reproduced. An agent can bring an environment as a manifest and a command, record its model calls through the gateway, and run comparisons that return the effect next to the run-to-run noise ([ADR-0079](adr/0079-behavior-studies.md)); The naming-convention control has real-model runs on a reconstruction ([evidence](naming-game-evidence.md)). With Qwen3-4B and brief step-by-step reasoning, populations of 8 agents reached consensus without an instruction in 5 of 5 claim runs at 30 rounds, removing interaction history removed it (0.675 to 0.125, p = 0.016, five runs per condition, uncorrected), and the prompts show the change reached the model. Three exploration runs with 24 agents also ended in consensus. Qwen3.5-9B did not show the behavior. The Emergent Collusion control has not been started. | Two positive controls. First, connect the original Emergent Collusion environment through the gateway, reproduce its published baseline, and compare runs with and without memory across episodes. Second, a naming game with and without interaction history; the [reconstruction template](../skills/behavior-study/template/) meets the three criteria below for Qwen3-4B at 8 agents; open are a claim run at 24 agents, more runs per condition, and a second model family. For each control: the baseline effect exceeds measured run-to-run noise, removing the published condition changes the behavior in the reported direction, and request records show the manipulation reached model inputs, which needs per-request sampling seeds that the model server honors, or paired replay, to make the reach check decisive. A control that does not reproduce is recorded as a re-test result and is not repaired by adding cues. |
| 2. Observe | The recorded real-agent runs on the native world scored zero on every task, and the first of them observed no cooperation. | Agents complete solvable tasks at a rate fixed in advance. Detectors are checked against the stage-1 cases before their flags are read. Hosted models run through the gateway; multi-provider deployment is unverified. |
| 3. Elicit | Enforced conditions (budgets, access, memory, incentives, monitoring) have tests and partial real-agent evidence, listed below. No behavior has been elicited. | Run-to-run noise measured before any comparison is interpreted. Every case carries the origin of the behavior. Forked reruns from a saved live state, unsupported today, for attribution inside a run. |
| 4. Red-team | Containment through the two gateways and before-tool monitor intervention exist. No red-team study has been run. | A search for conditions under which a team behaves in misaligned ways, monitor misses and false alarms measured on those cases, and a release review for elicitation recipes. |

The two positive controls are tasks for a team of AI agents, and where the team gets stuck is the list of
defects to fix. The team works from the [behavior-study skill](../skills/behavior-study/SKILL.md) and three
commands, which have scripted-inference tests. The Emergent Collusion repository is connected as a contained
environment ([ADR-0084](adr/0084-contained-study-environments.md)); its first real-model run is the next step.

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
algorithms and adaptive monitor training are prioritized when an agent team's reproduction needs them.
Each extension needs an enforceable interface, clear unsupported cases and evidence on the actual
execution path. There is no deadline or funded schedule implied by this list.
