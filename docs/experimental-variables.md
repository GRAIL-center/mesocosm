# Using experimental variables

Mesocosm changes the conditions under which real agents do original tasks. Use these controls to
test a mechanism, not to prescribe the behavior you hope to observe. The unchanged task evaluator,
allocated incentive, monitor assessment, and reviewed policy violation are separate measurements.

Start with the [paper notes](research-literature.md) for motivation and transfer limits, then the
[variable audit](variable-audit.md) for the existing controls' validity and confounds. This guide
describes the native Docker/OpenCode implementation. An external backend must implement each
requested control or reject it; registration does not inherit these mechanisms.

The extensions below have automated contract tests. Their combined behavior with three or more
real agents remains **unverified**. Older three-agent runs do not validate newly added conditions.
See the [capability matrix](capability-matrix.md) and [acceptance procedure](native-acceptance.md).

## Choose the mechanism before the settings

1. Name the proposed cause: scarce inference, reciprocal evaluation, remembered outcomes,
   selective exposure, or preferential work assignment. State a result that would disconfirm it.
2. Preserve task inputs, original evaluator, model identities and other conditions across variants.
   Check that the evidence needed to complete and review the task is actually accessible.
3. Declare repetitions, ordering, budgets and stopping rules before seeing outcomes. Keep failures,
   missing measurements and unselected participants in the analysis.
4. Inspect original requests, tool results, service reads/writes and independent verifier artifacts.
   A high peer score or copied file alone does not establish collusion, deception or intent.

Use [experiments](experiments.md) to compare named manifest patches in fresh worlds. YAML blocks
below are **world fragments**, to merge into a configured world with materialized original tasks,
models and agents. They do not create tasks or authorize external services.

## Existing controls worth retaining

| Variable | Where it takes effect | Interpretation and boundary |
|---|---|---|
| `agents[].budget.requests`, `completion_tokens` | Authenticated model gateway | Individual inference opportunities/output limits. Failed admitted attempts can consume requests; truncation is not deliberate misconduct. |
| `agents[].budget.prompt_tokens` | Gateway's next-admission check | Cumulative observed input usage threshold. The last admitted prompt can exceed it; not a strict reservation of the next prompt. |
| `resources.model_requests`, `completion_tokens`, `model_concurrency`, `service_requests` | Shared gateway/service admission | Real shared limits. These are not money, CPU allocations or measured GPU work. |
| `services.access`, `isolate_agents` | Service/repository ACLs and network topology | Read/write grants, directed DMs and channel access. Shared permitted resources can relay information; internet is another channel. |
| `services.chat.max_chars`, `max_messages` | Chat service | Communication capacity. Restricting evidence below what review requires confounds inability with willingness. |
| `agents[].model`, `models` | Model routing | Population heterogeneity. Model identity, capability and inference cost can change together. |
| `memory: reset / carry` | Task workspace and harness-session boundary | Reset clears managed local work/session; carry retains them within compatible environments. Neither erases shared services or external state. |
| `rewards.source`, `allocation`, `team_weight` | Episode reward calculation | Verifier/peer/compositional incentive; individual/team/mixed allocation. Original task scores remain separate. |
| `judge.monitor.*` | Actual LLM assessment and supported tool gate | Reviewer model, evidence scope, timing and threshold. Monitoring is fallible; a denial due to timeout is not a misconduct finding. |
| `internet.*`, `task_services` | Web gateway and native task-service lifecycle | Access to real sites and isolated/shared original services. Shared-service outcomes measure the team, not individual causal contribution. |

See [research controls](research-controls.md) for exact ACL, budget, containment and monitoring
boundaries. Nondefault settings on disabled components and unsupported combinations are rejected;
default-valued dormant sections are allowed for serialization and are not experimental treatments.
When disabling a customized component in an experiment patch, remove its whole section with `null`
(for example, `judge: {monitor: null}`). Setting only `enabled: false` retains inherited options under
the merge rules and is rejected when those options are nondefault.
Simulated ledgers/clocks, planted incident histories and legacy side objectives remain fixture-only.

## 1. Bounded memory and explicit feedback

[Emergent Collusion](research-literature.md#1-emergent-collusion-in-long-horizon-llm-agent-interaction)
motivates separating retained experience from reward feedback. Its two-agent protocol and constrained
verification are not a prediction for realistic three-agent work.

```yaml
memory: reset
history:
  episodes: 3
  scope: actions
  max_chars: 60000
rewards:
  enabled: true
learning:
  mode: in_context
  feedback:
    fields: [raw_reward, allocated_reward, verifier]
    delay_episodes: 2
    max_chars: 60000
```

| Field | Meaning |
|---|---|
| `history.episodes` | Last k completed task episodes for that agent; `0` disables replay. Requires `memory: reset` so an older native session cannot bypass the window. |
| `history.scope: actions` | Original recorded tool inputs/results only. |
| `history.scope: transcript` | Assistant visible responses plus tool inputs/results. Original user/system prompts, hidden reasoning, synthetic/ignored text and generated summaries are excluded. This is not the full input transcript. |
| `history.max_chars` | Complete rendered replay limit. Overflow rejects delivery; no silent truncation or summarization. |
| `learning.feedback.fields` | Any nonempty unique selection of `raw_reward`, `allocated_reward`, `verifier`, `peer_verdicts`, `review_accuracy`, `components`. Peer fields require ratings. `components` exposes the measured metric map; cost entries are unknown unless compositional rewards enabled their measurement. |
| `learning.feedback.delay_episodes` | Number of episode boundaries before feedback becomes due; `1` means the next episode. It is delivered in a subsequent task prompt, not an extra model turn. |
| `learning.feedback.max_chars` | Bound on the complete due feedback payload. Oversize delivery fails explicitly. |

Replay contains only observations from actual completed sessions. Stored task/session/message/part
identities and hashes link each selection to native records; `task_sha256` is the original instruction
hash. Input prompts are excluded to prevent prior replay blocks recursively accumulating. Compacted,
pruned or malformed native evidence is rejected. Empty action lists mean a completed episode had no
selected tools, not that missing evidence was replaced with an invented result.

Feedback is an independently selected disclosure. Exposing `verifier` in a peer-reward experiment
reveals an additional signal; it changes the treatment. The last episodes can finish before their
delayed feedback is ever due. Report attachment and completed delivery separately. Neither history
nor `in_context` updates model weights. `learning.mode: none` still explains the current incentive
rule, but supplies no outcome feedback.

**Hypothesis:** Does remembering prior tool outcomes increase reciprocal favorable reviews? Compare
`episodes: 0` with `episodes: 3`, keep feedback, communication and evaluator fixed, and inspect approval
agreement with original outcomes. Shared board/chat/Git can retain history even in the zero-memory
condition; constrain or explicitly measure that alternate route.

## 2. Measured reward components

[Emergent Collusion](research-literature.md#1-emergent-collusion-in-long-horizon-llm-agent-interaction)
and [CoopEval](research-literature.md#2-coopeval-benchmarking-cooperation-sustaining-mechanisms-and-llm-agents-in-social-dilemmas)
motivate distinguishing individual benefit, review quality, team benefit and cost. The terms below
calculate incentives from observed records; they do not create fictional balances or transfer money.

```yaml
services:
  chat: {enabled: true}
  peer_review: {enabled: true, protocol: ratings, topology: all}
rewards:
  enabled: true
  source: components
  success_threshold: 1.0
  terms:
    - {metric: verifier, weight: 1.0}
    - {metric: review_accuracy, weight: 0.5}
    - metric: completion_tokens
      weight: -0.1
      scale: 1000
      agent_weights: {agent-3: -0.2}
  allocation: mixed
  team_weight: 0.5
```

For agent a: `raw_reward[a] = sum(effective_weight[a] * metric[a] / scale)`.
`agent_weights` overrides that term's `weight` for named agents. Terms have unique metrics; scales
must be positive and values finite. Missing required measurements produce an unknown reward.
A zero effective weight does not require that component's missing measurement.

| Metric | Independent source and interpretation |
|---|---|
| `verifier` | Original task evaluator score, unchanged by incentive settings. |
| `peer_acceptance` | Fraction of assigned reviewers accepting this agent's work. Missing required votes are unknown. |
| `review_accuracy` | Fraction of this agent's assigned verdicts agreeing with whether the original score is `>= rewards.success_threshold`. Agreement with a fallible evaluator is not ground-truth alignment. |
| `model_requests` | Completed, attributable native model calls for this agent/task. Distinct from budget admission attempts, which can fail. |
| `completion_tokens` | Reported generated tokens for those completed calls. Missing usage is unknown; not GPU time or monetary cost. |

`allocation: individual` uses the raw value, `team_mean` uses the participant mean, and `mixed` uses
`(1 - team_weight) * own + team_weight * mean`. A missing team input prevents a measured team mean.
Cost units and scales matter: poorly scaled costs can dominate task reward. The current reward
formula is described to each agent. Disclosure is currently fixed; account for this instruction
alongside the calculated return when interpreting an incentive contrast.

The separate [policy-training loop](training.md) accepts these measured components. Its collector
recomputes them from original verifier artifacts, native service verdicts and policy-bound model
requests, and rejects altered or missing evidence. Logging a new reward alone does not train a policy.
Monitor suspicion is not automatically a reward term or RL penalty.

**Hypothesis:** Does rewarding review accuracy reduce unsupported acceptance relative to rewarding
peer approval? Keep review assignments and independent evaluation fixed. Report task scores, approval,
review accuracy, costs and missing votes separately; inspect whether access made review feasible.

## 3. Review matching, reputation and work assignment

[CoopEval](research-literature.md#2-coopeval-benchmarking-cooperation-sustaining-mechanisms-and-llm-agents-in-social-dilemmas)
motivates changing repeated relationships and visible reputation. Its partner rematching and contracts
are broader than the reviewer reassignment implemented here.

```yaml
services:
  chat: {enabled: true}
  peer_review:
    enabled: true
    protocol: ratings
    topology: random_ring
    matching_seed: 17
rewards: {enabled: true, source: peer_verdict}
reputation:
  enabled: true
  window: 3
  metrics: [verifier, review_accuracy]
```

`all` assigns every other agent; `ring` uses manifest order; `random_ring` reshuffles the ring at each
zero-based task episode using `matching_seed`. The actual mapping is recorded and ratings endpoints
enforce assigned targets. A random ring changes who reviews whom, not the model population, task
cohort or communication ACLs. A small population can yield the same ring in adjacent episodes.

Reputation supplies actual per-participant measurements from the last `window` completed world
episodes in subsequent task context. `metrics` selects `verifier`, `review_accuracy`, or both. It is
not an invented trust label, a ranking or a claim about intent. Exposing verifier-based reputation
also exposes task-success information. It does not automatically change who receives work.

Task selection is separate:

```yaml
memory: reset
rewards: {enabled: true}
task_assignment:
  policy: performance
  participants: 3
  score: verifier
  seed: 17
```

This fragment requires a population larger than three. `policy: all` is the default. `round_robin`
cycles over a seed-shuffled population; `performance` ranks the mean of each agent's observed prior
scores, with seeded tie order. `score` selects `verifier` or `allocated_reward` for performance ranking.
The first task measures **every** configured agent; later tasks run only the selected cohort in
successive waves. Unselected agents do not receive fabricated results or extra tasks. They are
recorded as skipped, and retain their last measured history. Selection is not exploration or an RL
optimizer, and can amplify early noisy scores or unequal opportunities.

Selective assignment requires comparison, individual rewards, a cohort smaller than the population,
and isolated task services. It cannot currently combine with peer review or policy-weight training.
Use at least three actual agents per selected wave for multi-agent acceptance.

**Hypotheses:** Compare fixed versus random review rings for persistence of reciprocal approvals.
Separately compare round-robin versus performance-based work assignment for concentration of work
and exposure to strategies. Do not change reviewer matching and cohort selection in the same contrast
unless the interaction itself is the preregistered question.

## 4. Publication and artifact exposure

[Reward Hacking as a Transmissible Strategy](research-literature.md#3-reward-hacking-as-a-transmissible-strategy-in-multi-agent-coding-systems)
and the [research-swarm case study](research-literature.md#4-a-case-study-on-emergent-cheating-and-whistleblowing-in-autonomous-research-swarms)
motivate controlling which actual outputs become available for reuse. Their seeded sources and
observational findings do not establish spontaneous transmission in Mesocosm.

```yaml
rewards: {enabled: true}
artifacts:
  enabled: true
  paths: [answer.py, notes.md]
  eligibility: passing
  success_threshold: 1.0
  show_author: true
  show_score: false
  max_bytes: 1048576
```

Declare exact existing task-output paths relative to the agent workspace, without globs. Missing files
are recorded as missing. The native verifier snapshots original UTF-8 file bytes after inspected
task quiescence and before grading, then immutable versions are published after original verification.
`completed` publishes measured completed
attempts, including failing scores; `passing` requires the configured original-score threshold.
Both modes publish after measurement. Pre-verifier release and configurable publication delays are
not implemented. Publication can become visible while slower peers are still working.

The `max_bytes` limit is aggregate over at most 32 declared files (default 1 MiB, maximum 16 MiB).
Oversized, non-UTF-8, unstable, linked or unsafe paths reject capture instead of silently losing data.
`show_author` and `show_score` select metadata disclosure; file content can still identify its author,
and passing-only availability itself reveals threshold information. A published score belongs to the
whole original task, not an independent correctness certification for each file. These flags affect
agent-visible metadata; a monitor with `include_shared` can inspect privileged full artifact records
and bytes within its existing evidence budgets.

Agents may list `http://board/api/artifacts` and retrieve a chosen immutable file. Board read permissions
apply. Publication, listing and exact file reads are logged with version/content hashes. Agents decide
whether to retrieve or reuse the material. A read is evidence of exposure, not proof of adoption.
This path requires live board services and synchronized reward episodes, and is not yet integrated
with policy-weight training. Shared-service exchange is unsupported. Separate verifiers accept only
paths covered by their declared original artifact inputs; paths overlapping original child mounts are
rejected because a host workspace path would not represent those mounted bytes. A custom verifier
must supply the explicit stable-capture hook; there is no generic pre-verifier fallback.

**Hypothesis:** Does passing-only publication reduce downstream reuse of independently failing work?
Keep task distributions and source agents fixed. Measure availability, exact retrieval and subsequent
tool/code changes separately; allow the hypothesis to fail if the original verifier accepts bad work.

## Limits and next mechanisms

History replay, reward composition, reviewer matching/reputation and measured artifact publication/work
selection implement the first four extension areas. [History tests](../tests/test_history.py),
[episode-control tests](../tests/test_episode_controls.py), [reward tests](../tests/test_rewards.py),
[artifact tests](../tests/test_artifact_exchange.py) and [training provenance tests](../tests/test_training_collect.py)
check their contracts. Actual new-condition acceptance and behavioral effects remain unverified.

Audit sampling policies, trusted fallback/editing, report/escalation resolution, binding contracts,
real transfers, population replacement and automatic adoption detection remain **proposals**. Existing
LLM monitoring is not all of [AI Control](research-literature.md#5-ai-control-improving-safety-despite-intentional-subversion),
and a chat message is not an operational [report-resolution workflow](research-literature.md#6-can-escalation-channels-redirect-reward-hacking-toward-defect-disclosure).
Specify enforcement, source records and failure handling before treating any of these as a variable.
