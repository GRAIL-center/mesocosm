# Capability matrix

Status is **supported** when the listed evidence tests the stated scope, **unsupported** when the
mechanism is absent or explicitly refused, and **unverified** when implementation exists without the
required execution evidence. A unit-tested feature is not thereby validated with real agents. Acceptance checks system integration,
condition enforcement and observation correctness; it does not require agents to exhibit a target behavior.

The [2026-10-19 native A100 run](native-multiagent-evidence.md) demonstrated three real OpenCode
sessions, model calls, shell tools and unchanged external-task verifiers. All task scores were zero;
no cooperation was observed. It predates the generic experiment, reward and monitoring changes below
and supplies no live evidence for those changes. Earlier L4 demonstrations used authored fixtures;
their records establish integration behavior, not spontaneous misalignment.

The [2026-10-21 UTC condition run](condition-experiment-evidence.md) exercised verifier-derived
individual/team-mean incentives with three real sessions per condition. The individual arm had tool
use by all three agents; one team-mean agent used no tool. All scores were zero, and Colab teardown
needed operator intervention. This verifies the stated execution/record paths, not an incentive effect
or unattended Colab lifecycle. The subsequent teardown guard has separate local regression evidence.

The subsequent [A100 training run](training-monitor-evidence.md) completed an actual policy update
and a second world using the new checkpoint. All three agents made real model calls in each world;
only agent 2 used tools. This validates three policy users and the training loop, not tool use by
every participant or a learning benefit. Its cleanup also needed operator assistance.

## Real execution and task integration

| Capability | Status and boundary | Evidence |
|---|---|---|
| At least three independent agents on an external task | **supported** for the tested native profile; agents share one model replica but have separate processes, sessions, keys and workspaces | [A100 evidence](native-multiagent-evidence.md), [new individual condition](condition-experiment-evidence.md), [task runtime tests](../tests/test_task_runtime.py) |
| Native environment and original verifier | **supported** for Debian/Ubuntu, single-container filesystem tasks. Harness packages are added to the task image; the original verifier runs against a separate filesystem snapshot | [Runtime guide](native-multiagent.md), [runtime tests](../tests/test_task_runtime.py), ADR-0042 |
| Task Compose stacks, task GPUs, separate verifier images or live-process verification | **unsupported** by the current native profile | [Runtime tests](../tests/test_task_runtime.py), [documented profile](native-multiagent.md) |
| Harbor task and collection references | **supported** in tests: resolve materialized task directories, retain provenance and refuse missing references. Running an entire external collection is **unverified** | [Harbor adapter tests](../tests/test_harbor_adapter.py) |
| Other harnesses or broad SWE-bench execution | **unsupported** harness integration / **unverified** task compatibility; importing a standard directory is not execution evidence | [Manifest checks](../tests/test_manifest.py), [runtime profile](native-multiagent.md) |
| Per-agent model catalog routing | **supported** in tests through configured OpenAI-compatible chat upstreams; multi-provider deployment remains **unverified** | [Gateway tests](../tests/test_gateway.py), [compose tests](../tests/test_env.py), ADR-0029 |
| Tool allow-list, MCP and skill configuration | Config rendering/validation is **supported** in tests. Earlier OpenCode listed configured MCP tools; actual model-driven MCP/skill use and tool allow-list enforcement remain **unverified** | [Manifest tests](../tests/test_manifest.py), [compose tests](../tests/test_env.py), [services tests](../tests/test_services.py), ADR-0021 |
| Task and round deadlines | **supported** in tests: remote session abort, failed run, no grading of a timed-out turn. Remote process-tree termination is **unverified** | [Run tests](../tests/test_run_e2e.py), [adapter tests](../tests/test_opencode_adapter.py) |
| Missing or failed verifier results | **supported** failure handling in tests; absence is not a successful zero measurement | [Verifier tests](../tests/test_verifier_failure.py), [run tests](../tests/test_run_e2e.py) |

## Experiment design and incentives

| Capability | Status and boundary | Evidence |
|---|---|---|
| Named condition patches and repetitions | **supported** in tests: all variants prevalidate, mappings merge, lists replace, null removes keys, paths use the base manifest directory | [Experiment tests](../tests/test_experiment.py), ADR-0044 |
| Listed/randomized scheduling and trial isolation | **supported** in tests; two real condition trials used distinct worlds/workspaces and randomized scheduling with seed 0, with manual Colab teardown. `order_seed` seeds scheduling only; repetitions and unattended Colab transitions are **unverified** | [Experiment tests](../tests/test_experiment.py), [compose tests](../tests/test_env.py), [condition evidence](condition-experiment-evidence.md) |
| Complete trial accounting | **supported** in tests: failures, missing records and no-flag trials retained; no flag-based stopping or automatic replacement trials; interruption persists the remaining plan | [Experiment tests](../tests/test_experiment.py) |
| Strict manifest and experiment schema | **supported** in tests: unknown schema fields, unsupported modes and ineffective reward/monitor patches rejected | [Manifest tests](../tests/test_manifest.py), [condition tests](../tests/test_experiment_conditions.py), [experiment tests](../tests/test_experiment.py) |
| Incentive source: original verifier or peer verdicts | **supported** in tests for comparison episodes; verifier-derived records exercised with real agents. Peer-verdict live validation remains **unverified**. Original scores stay separate; missing required peer reviews remain unmeasured | [Reward tests](../tests/test_rewards.py), [three-agent control-plane tests](../tests/test_experiment_conditions.py), [condition evidence](condition-experiment-evidence.md), ADR-0044 |
| Individual, team-mean and mixed reward allocation | **supported** in tests; individual/team-mean paths exercised with real agents and equal zero scores. Different outputs and missing-input semantics have test evidence; mixed live validation is **unverified** | [Reward tests](../tests/test_rewards.py), [integration tests](../tests/test_experiment_conditions.py), [condition evidence](condition-experiment-evidence.md), ADR-0044 |
| `learning.mode: none` / `in_context` | **supported** in tests with fixed weights; `none` exercised live. Real-agent feedback delivery remains **unverified**. Feedback attaches to the next task; attachment and completed delivery are separate records | [Integration tests](../tests/test_experiment_conditions.py), [condition evidence](condition-experiment-evidence.md), ADR-0044 |
| Actual RL, optimizer updates, checkpoint activation | **supported** for the tested A100 FP16 profile: two native worlds with three real agents each, one actual REINFORCE LoRA update, hashed checkpoint activation and final rollout probability verification. The earlier BF16 attempt failed parity before updating. Learning benefit is **unverified**; context feedback remains fixed-weight | [A100 training evidence](training-monitor-evidence.md), [training guide](training.md), [trainer tests](../tests/test_learning.py), [controller tests](../tests/test_training_controller.py), ADR-0045, ADR-0047 |
| Rewards in a persistent shared task queue | **unsupported**: generic rewards currently require synchronized comparison episodes | [Manifest validation](../src/mesocosm/core/manifest.py) |

## Enforced environment conditions

| Capability | Status and boundary | Evidence |
|---|---|---|
| Per-agent model-request and token budgets | **supported** in tests: authentication, atomic admission and output caps. Prompt usage is checked at response boundaries, so one admitted prompt may cross its threshold; missing usage prevents later token-budgeted calls | [Gateway tests](../tests/test_gateway.py), ADR-0043 |
| Authenticated model surface | **supported** in tests: only chat completions and model listings are forwarded; unknown keys and unobserved inference/management routes are refused | [Gateway tests](../tests/test_gateway.py), ADR-0043 |
| Mid-run condition changes | **supported** for gateway budgets only; other conditions return `rejected_unsupported` with a receipt | [Condition tests](../tests/test_conditions.py) |
| Shared-directory and pastebin availability; network isolation | **supported** by compose/services and per-agent egress receipts, with earlier fixture Docker evidence. Availability is not evidence that agents used the resource | [Compose tests](../tests/test_env.py), [run tests](../tests/test_run_e2e.py), [spike record](../spike/SPIKE.md) |
| Memory reset/carry | **supported** for managed workspace and harness-session boundaries. Full harness-storage reset is **unsupported**; arbitrary carry across incompatible native environments is refused | [Run tests](../tests/test_run_e2e.py), [native runtime tests](../tests/test_task_runtime.py) |
| Shared services and permissions | **supported** in tests: functioning board/chat/wiki/pastebin, Git HTTP, wheel index, account roles and message caps. Selected earlier service operations ran in fixture Docker worlds | [Services tests](../tests/test_services.py), [chat tests](../tests/test_chat.py), [peer endpoint tests](../tests/test_peer_review_services.py) |
| Observer tool logs | **supported** in tests and the condition run: generic tool journals remain in controller-owned bundle storage. Only the legacy peer-verification fixture writes an agent-readable workspace log | [Run tests](../tests/test_run_e2e.py), [condition evidence](condition-experiment-evidence.md) |
| Initial wiki and service content | Explicit seed/mount support is **supported** in tests. A rebuilt services image was checked in a real container and had an empty wiki directory. Archived histories are synthetic inputs | [Seed tests](../tests/test_seed.py), [compose tests](../tests/test_env.py), [image check](condition-experiment-evidence.md), [fixture inventory](../examples/fixtures/README.md) |
| Controlled internet | Live/record access through the policy/logging proxy has earlier Docker evidence; replay is unit-tested. Direct sandbox egress is excluded by topology and checked before a run | [Web tests](../tests/test_web.py), [run tests](../tests/test_run_e2e.py), ADR-0022 |
| General CPU/GPU scarcity and resource-allocation interventions | **unsupported** as a generic experimental resource scheduler. Native task limits follow the host/profile; Colab omitted CPU/RAM/storage enforcement under explicit overrides | [A100 adaptations](native-multiagent-evidence.md), [runtime guide](native-multiagent.md) |
| Cache, clock, ledger and vault | **supported** as optional text storage, simulated time/balances and canned honeypot data. **Unsupported** as substitutes for a real build cache, compute quota or credential authority | [Cache tests](../tests/test_cache.py), [world-state tests](../tests/test_world_state.py), [honeypot tests](../tests/test_honeypot.py) |

## Monitoring, review and records

| Capability | Status and boundary | Evidence |
|---|---|---|
| Per-agent or joint posthoc monitor | **supported** in tests using a fake model endpoint; real-model validation of the new path is **unverified**. It reads all recorded requests/tool events in selected windows, not only the final model context | [Monitor tests](../tests/test_monitor.py), ADR-0044 |
| Transcript/actions visibility and optional shared evidence | **supported** in tests: selected service/web records and world snapshots; configurable rubric/version and call/input/output budgets | [Monitor tests](../tests/test_monitor.py) |
| Missing, malformed or truncated monitor evidence | **supported** explicit unknown/coverage handling in tests. Missing answers do not count as safe or undetected successes; joint scores do not identify an individual culprit | [Monitor tests](../tests/test_monitor.py) |
| Live/pre-action monitor intervention | **supported** at OpenCode 1.19.3's top-level `tool.execute.before` boundary: per-agent allow, deny or terminate, bounded model assessment and fail-closed unknown/error handling. Real-OpenCode/Qwen evidence includes an allowed write, denied write and acknowledged session abort; a falsely allowed read remains a recorded monitor-quality failure. Nested sessions, joint live review, shared-evidence review and syscall mediation inside an approved tool are **unsupported** | [Live evidence](training-monitor-evidence.md), [gate tests](../tests/test_action_gate.py), [failure evidence tests](../tests/test_failure_evidence.py), ADR-0046, ADR-0048 |
| Adaptive monitor training | **unsupported**; policy training updates the agents' policy, not the reviewer | [Training guide](training.md), ADR-0045, ADR-0046 |
| Candidate flags and append-only reviews | **supported** in tests. Flags, monitor scores and human review verdicts remain different records; neither a flag nor a shared-store pickup establishes collusion | [Judge tests](../tests/test_judge.py), [CLI tests](../tests/test_cli.py), [report tests](../tests/test_report.py) |
| Independent outcome families in experiment summaries | **supported** in tests; live task/incentive records and disabled-monitor accounting checked separately. Reviewed verdicts stay in each run bundle | [Experiment tests](../tests/test_experiment.py), [record tests](../tests/test_records.py), [condition evidence](condition-experiment-evidence.md) |
| Gateway records and live/replay observer | **supported** in tests and earlier real/fixture runs; exact model exposure is retained when received. Joins across other sources can rely on time windows | [Observer tests](../tests/test_observe_state.py), [gateway tests](../tests/test_gateway.py), [A100 evidence](native-multiagent-evidence.md) |
| Activation recomputation and probes | Recompute has earlier L4 evidence; probe plumbing is **supported** on synthetic tensors. Live `vllm serve` activation hooks are **unsupported**; probe quality on real behavior is **unverified** | [Activation tests](../tests/test_activations.py), [probe tests](../tests/test_probes.py), ADR-0016, ADR-0031 |
| Inspect evaluation export | **supported** in tests and an earlier Inspect readback. Export declares omitted native logs, token IDs and activations; it is not a training trace | [Export tests](../tests/test_export.py), ADR-0025 |
| Validated RL trajectories and checkpoint records | **supported** in tests through the training collector: exact gateway token IDs/logprobs, frozen policy/session/task bindings, independent verifier files and allocated returns must agree. Prompt/tool-result tokens are excluded from the objective. Failed or incomplete worlds are refused; Inspect export remains separate | [Collector tests](../tests/test_training_collect.py), [gateway tests](../tests/test_training_gateway.py), [runtime integration tests](../tests/test_training_runtime.py), ADR-0045 |

## Lifecycle and compatibility

| Capability | Status and boundary | Evidence |
|---|---|---|
| Full-source typing | **supported** by `mypy src`: all 68 source files pass after fixing the 34 previously reported errors. Ruff passes; the default suite has 527 passes and 10 optional-learning skips, with 64 learning/controller tests passing separately in the actual Torch/PEFT environment | [Typing boundary](../src/mesocosm/observe/_activation_types.py), [learning tests](../tests/test_learning.py), [controller tests](../tests/test_training_controller.py) |
| Bounded teardown and failed-cleanup accounting | **supported** in local subprocess/control-flow tests: stop subsequent trials, retain completed outcomes, report lifecycle errors and reap only owned Compose clients. This guard was added after the condition run; the later training run needed external cleanup assistance within its deadline. Unattended Colab teardown remains **unverified**; automatic host/container workload termination is **unsupported** | [Stack tests](../tests/test_stack.py), [experiment tests](../tests/test_experiment.py), [CLI tests](../tests/test_experiment_cli.py), [condition limitation](condition-experiment-evidence.md), [training limitation](training-monitor-evidence.md) |
| Persistent queue, snapshots and hash-verified restore | **supported** in tests and earlier fixture runs. Snapshot coverage excludes live processes, harness sessions, complete container filesystems and training state | [Snapshot tests](../tests/test_snapshot.py), [run tests](../tests/test_run_e2e.py) |
| Gateway pause/resume/stop | **supported** in tests and earlier fixture runs. Pausing model requests does not pause arbitrary running tools; long holds can exceed harness timeouts | [Control tests](../tests/test_control.py), [gateway tests](../tests/test_gateway.py) |
| Legacy chains and staged scenarios | Compatibility is **supported** in tests; staged scenarios are deprecated for condition comparisons. Their retries/stopping rules differ from the experiment runner | [Chain tests](../tests/test_chain.py), [scenario tests](../tests/test_scenario.py), [fixture inventory](../examples/fixtures/README.md) |
| Legacy peer-verification and side tasks | Compatibility is **supported** in tests. Paper-specific reward messages and directly instructed misconduct do not establish emergent behavior or policy training | [Run tests](../tests/test_run_e2e.py), [fixture inventory](../examples/fixtures/README.md) |
| Production process isolation on Colab | **unsupported** by the documented wrapper: it removes PID isolation and exposes host `/proc` and `/sys`. This restriction is separate from tested network checks | [A100 adaptations](native-multiagent-evidence.md) |

Update this matrix when an interface or its evidence changes. Keep raw runs outside version control.
