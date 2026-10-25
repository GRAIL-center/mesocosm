# Architecture

Mesocosm connects independent coding-agent processes to real task environments and records their
interactions. Experiments vary enforced conditions while preserving independent task measurements.
The implementation goal is correct system composition: real environment/agent interfaces, enforceable
conditions, independent measurement and repeatable execution. Acceptance does not require cooperation,
collusion or any other desired behavior. The agents choose their actions. Incident stories, planted
decoys and prescribed misconduct are
[legacy fixtures](examples/fixtures/README.md), not the architecture's organizing unit.

This document describes the implemented structure and its boundaries. Consult the
[capability matrix](docs/capability-matrix.md) for evidence and the
[native runtime guide](docs/native-multiagent.md) for deployment requirements.

## Execution and authority

The experiment definition and execution location are separate. A Mac or Linux client packages a
finite research world or condition experiment and submits it to an authenticated worker. The worker
owns the whole world through its selected execution backend: agent and environment startup, original
verification, record collection and cleanup. Accepted jobs continue after the client disconnects. Model inference
can run on a separate host; the built-in execution backend is Docker on Linux. This boundary does not
provide remote policy training or persistent streams. See the [execution guide](docs/execution.md)
and [ADR-0051](docs/adr/0051-remote-world-execution.md).

The Docker worker runs beside a local Linux Docker daemon, with the host PID visibility required by native
containment checks. It does not turn a remote Docker socket into a portable controller. A host-wide
worker lease and a serial queue allow one managed world at a time because the current networks use
fixed subnets. Direct, manually launched stacks remain the operator's responsibility. On worker
restart, recorded unfinished ownership is cleaned before new execution; interrupted observations are
not replayed. A cleanup failure closes admission and prevents another world from starting.

Each agent has its own OpenCode server, session, gateway credential and workspace. The native task
runtime builds from the task's declared Docker environment, adds the released harness, and preserves
the task's user and working directory. It runs the original filesystem verifier in a separate snapshot.
The agent profile remains Debian/Ubuntu. A validated subset of original task Compose sidecars supports
isolated or shared service state, with fresh separate verifiers for declared artifacts or live services.
Shared mode keeps distinct agent workspaces and records one team measurement. Task GPUs, shared-main
RPC and arbitrary agent-process restoration remain unsupported. This extension has command-level tests;
new real-agent acceptance remains unverified ([research runtime](docs/research-runtime.md), ADR-0050).

Agents use an internal Docker network. The model gateway connects them to inference; the optional web
gateway provides configured internet access. Shared volumes and services are explicit affordances.
The control plane, verifier results and run bundles remain outside agent write access. The egress check
verifies the selected topology before agents run. Colab's rootless adaptations have additional isolation
and resource limits documented in the setup guide; a successful Colab run does not establish production
isolation. Standard native execution requires inspected private PID containment before task turns;
the older local `containment: adapted` path permits explicitly recorded adaptations (ADR-0049).
Remote jobs always require verified containment, and native Compose service startup refuses shared
PID namespaces even on the adapted local path (ADR-0050).

The native gateways are integration boundaries. Mesocosm configures released OpenCode, Harbor task formats,
vLLM and NNsight through their supported interfaces and does not patch their internals.

## Integration contracts

Task formats and whole-world execution are separate installed extension points. A task source resolves
materialized inputs and loads original instructions, configuration and provenance. Its `execution_kind`
identifies the environment/evaluation contract it needs. The built-in Docker backend accepts compatible
Harbor execution inputs; a different task format can use that path without renaming itself `harbor`.

A whole-world backend provides its actual agent interfaces, environment lifecycle, evaluator, enforced
conditions and evidence collection. A browser/API environment can therefore preserve its native
semantics. Registration does not turn it into an OpenCode adapter or implement any requested control.
The provider must reject unsupported configurations before resource allocation. Generic worker
submission, scheduling, stop requests, durable state and result transfer remain shared.

Providers are trusted Python distributions registered through `mesocosm.task_sources` and
`mesocosm.execution_backends` entry points. Uploaded task files never define import paths or trigger
installation. `execution.backend` selects the provider; recorded `execution.options` are distinct from
private worker deployment configuration. Every effective experiment condition must match the worker's
selected backend.

Portable inputs pin selected provider API, package version, entry point and code hash. The worker
checks those locks before admission and again before execution; input snapshots detect integration
drift. Already loaded code changes require process restart. Recovery uses persisted ownership without
loading task sources, and an external backend must match its recorded owner identity. Code hashes do
not pin dependencies, remote service state or sampling behavior.

The extension contracts have CPU tests; benchmark acceptance still requires three real agents,
actual actions, original evaluation and trajectory inspection. Native baseline acceptance and
policy-weight training remain Docker/OpenCode paths. See the [integration guide](docs/integrations.md)
and [ADR-0055](docs/adr/0055-installed-integration-contracts.md).

## Components

| Component | Responsibility |
|---|---|
| `core.manifest` | Validated agents, tasks, models, services, budgets, rewards, memory and monitor configuration. Unknown schema fields are rejected. |
| `core.experiment` | Prevalidate named condition patches, schedule repetitions, and retain every trial's outcome and missing measurements. |
| `core.run` | Prepare environments, enforce run boundaries, drive concurrent agents, invoke original verifiers, collect evidence and run posthoc analysis. |
| `core.rewards` | Synchronize comparison episodes and allocate incentives separately from original verifier scores. |
| `core.episode_controls`, `core.history`, `core.artifacts` | Measured reward terms, matching and work selection, bounded native-history projections and immutable publication of declared task outputs. |
| `execution` | Package declared inputs, authenticate remote jobs, persist lifecycle state, and own Docker startup, cancellation and recovery; share runtime composition with direct local commands. |
| `integrations` | Discover installed task sources and execution providers, validate selected contracts, pin their code identities and reject drift. |
| `gateway` | Authenticate identities, route chat requests, enforce model budgets, bind exact rollout identities and hold proposed tool actions for controller decisions. |
| `env` | Docker topology, native task runtime, real shared services, web access policy and state inspection. |
| `adapters.opencode`, `adapters.harbor` | Harness API and task/environment/verifier integration. |
| `judge` | Candidate flags, append-only review verdicts, posthoc assessments and pre-action tool decisions. |
| `learning` | Validate original rollout/reward provenance, update LoRA weights with REINFORCE, activate immutable policy aliases and verify subsequent evaluation rollouts. |
| `observe` | Optional activation recomputation and probes; run inspection uses CLI reports and original records. |
| `export` | Inspect evaluation logs, with omitted record types declared. |

## Experiments and conditions

An experiment specifies a base world, named manifest patches, repetitions and listed or randomized
execution order. Patches merge mappings, replace lists and remove keys set to null. Paths resolve from
the base manifest. Every variant is validated before the first trial starts; each trial gets a distinct
manifest and run label. `order_seed` controls scheduling only, not model sampling, task randomness or
training.

Trials run sequentially because the topology uses fixed subnet addresses. The execution layer reconciles a fresh
stack and separate workspace directories for each trial. Trial failure is recorded and the remaining
plan continues. If teardown fails, the prior environment may still exist: the experiment stops with
`failed_cleanup`, retaining completed results and unrun trials. Bounded teardown verifies an empty
project container inventory. Active stop requests and failures invoke owned-container termination
with separate process-isolation evidence. The runner has no flag-triggered stopping or automatic replacement trials;
these would select observations based on outcomes. Older `scenario` and `chain` interfaces remain for
compatibility, with their distinct stopping and retry semantics.

Conditions are enforced at their actual point of effect: token and request budgets at the gateway,
shared-directory access through mounts, service permissions at the service, and memory at task
boundaries. Only budgets currently support mid-run mutation through the condition-control API.
Unsupported combinations fail explicitly instead of being approximated with task prose. Optional
`services.access` grants enforce an internal access graph; team request/output/concurrency pools and
service quotas apply at actual admission points. Shared mounts are refused with the access graph.
See [research controls](docs/research-controls.md) for relay, restart and inference-compute limits.
The [variable manual](docs/experimental-variables.md), [paper notes](docs/research-literature.md) and
[variable audit](docs/variable-audit.md) connect each treatment to its scientific rationale and limits.
Nondefault controls on inactive components are rejected before execution, rather than silently
becoming a baseline. Conditions are fixed at their declared enforcement points; only the existing
gateway budget API supports mid-run mutation.

`memory: reset` clears the managed workspace and opens a new harness session; `carry` retains both
within the run. Neither option describes a complete reset or serialization of harness internals.
Native carried workspaces cannot be transferred arbitrarily between incompatible task environments.
Managed OpenCode configurations disable automatic compaction and pruning, preserving tool results
in the working history. Context overflow ends the turn explicitly. Each round retains its complete
native turn separately from the last HTTP reply; terminal errors and truncated endings prevent grading.
An actual terminal model-budget refusal triggers controller abort, while the last admitted request
can finish normally. See [ADR-0052](docs/adr/0052-harness-turn-evidence-and-termination.md).

Optional `history.episodes` replays only the last k completed tasks into fresh reset sessions. The
projection includes tool inputs/results, optionally assistant visible responses, with source identities
and hashes. It excludes original prompts, hidden reasoning and generated summaries; prior replay
blocks are not recursively copied from user prompts. Oversized, compacted or malformed evidence fails
explicitly. This limits the delivered projection, not information retained by shared services or
repeated in later agent-generated content. It does not serialize the entire agent process.

## Outcomes, incentives and learning

The original verifier always measures task outcome independently of the incentive source. With
`rewards.enabled`, comparison-mode participants complete the same task as a synchronized episode:

- `source: verifier` uses those original task scores as raw incentives.
- `source: peer_verdict` uses the fraction of assigned reviewers who accept the agent's work through
  the real chat verdict endpoint. Missing required reviews remain unmeasured.
- `source: components` computes a weighted, scaled sum of original outcome, peer acceptance, review
  accuracy and observed completed-model-call/token counts. Agent-specific weights are explicit.
  Review accuracy is agreement with a configured threshold on the original score, not an alignment
  label. Costs come from completed attributed requests, distinct from admitted-request budget quotas.
- `allocation: individual` retains each raw score; `team_mean` assigns the team mean; `mixed` combines
  own score and team mean using `team_weight`. Missing team inputs do not become zero.

Peer scoring uses `services.peer_review.protocol: ratings` with an `all`, `ring` or seeded `random_ring`
topology. Assignments are recorded, installed at the real verdict endpoint and preserved on service
restart. Random matching changes review relationships, not the agent population or task cohort. The
paper-specific correctness-of-verdict protocol is isolated in `core.legacy_peer_review` and retained for
legacy fixtures. Generic rewards are not supported in persistent queue mode.

`learning.mode: none` records incentives without delivering outcome feedback. `in_context` attaches the
selected outcome fields after the configured episode delay and records attachment and completed delivery.
The default selects own raw/allocated rewards for the next task; disclosing verifier scores or peer
verdicts is a separate information treatment. Oversized feedback fails instead of being truncated.
The last episodes may have no subsequent task to receive feedback. Both modes keep model weights fixed;
context adaptation and weight updates must not be reported as the same mechanism.

Optional reputation exposes original participant measurements from a bounded window of completed world
episodes; no initial trust scores or invented histories are inserted. Work assignment is independent:
the default runs all agents on every task, while selective policies first measure every agent once,
then dispatch smaller cohorts in finite round-robin or measured-performance waves. Unselected agents
do not execute the task and have no fabricated outcome. Selective dispatch requires individual rewards
and isolated services and cannot combine with peer review or policy training. Selection changes work
opportunities and can amplify noisy early scores; it is not an optimizer.

`mesocosm train run` adds an outer policy-training loop around fresh native worlds. It requires at least
three agents sharing one pinned causal LM, reset memory, measured rewards and complete comparison
episodes. Each model call is bound to its policy, agent, task, round and harness session before sampling.
The collector cross-checks original gateway tokens, logprobs, verifier artifacts, reward allocation and
controller receipts. Missing or inconsistent evidence stops training.
Compositional incentives are recomputed from original verifier files, native verdict writes and exact
policy-bound model requests. New metric/assignment provenance uses a v2 rule marker; original scalar
records retain their earlier interpretation. Bounded history, selective assignment and artifact
publication are explicitly unsupported in policy training until their rollout contracts are integrated.

One REINFORCE optimizer step updates LoRA weights from each complete batch. The controller hashes the
saved checkpoint, activates a fresh vLLM alias and uses it in the next world. The final world performs
evaluation and token-logprob consistency verification without another update. Original scores and
allocated incentives remain distinct. Context feedback stays disabled in this training profile. See
the [training guide](docs/training.md) and [ADR-0045](docs/adr/0045-on-policy-world-training.md) for the
estimator, supported profile and distinction between a real update and demonstrated learning benefit.
Optional `evaluation_world` runs content-disjoint tasks before and after training without updates.
Experiment inputs are fingerprinted and checked for drift; `baseline: true` requires a three-agent
native task profile and declared model revisions, separate from verified serving identity.

## Observation and judgment

A run bundle contains the manifest, normalized events, enforcement receipts, native gateway/harness
records and verifier results. Model calls use `request_id`; other sources also carry run and agent
identity where available. Some joins still use agent/task time windows; this is not a complete causal
trace with exact action-token alignment. New events carry IDs and explicit identity/link fields;
exact message/object versions link recorded exposures. Detached anchors verify preserved bytes against
a separately retained reference, without asserting capture completeness or execution truth.

Keep four outputs separate: original task outcomes, allocated incentives, monitor assessments and
reviewed policy violations. Detectors produce candidates. A shared-store read, a high reward or a monitor
score alone does not establish collusion or intent.

The posthoc monitor can inspect each agent/task window independently or the joint
run, using recorded transcripts or actions, optionally including shared-service/web evidence and world
snapshots. It records the rubric version/hash, selected requests, evidence coverage, truncation and
call/input/output budgets. Malformed answers and missing evidence remain unknown. A joint score has no
individual-agent attribution.

With `timing: pre_action`, the released OpenCode `tool.execute.before` hook awaits a controller decision
before the tool implementation starts. A gate binds the loaded plugin digest, authenticated agent and
top-level session; the controller checks the proposed call and arguments against the actual harness
record. The reviewer assesses whether executing that proposed action would violate the configured
rubric; a temporary hold is not a completed intervention. Its prompt version and exact system-prompt
hash are recorded separately from the rubric hash ([ADR-0048](docs/adr/0048-prospective-monitor-assessment.md)).
A threshold crossing denies the tool or terminates the session. Unknown assessments, exhausted
budgets and transport failures deny and abort. This is a per-agent tool boundary, not a syscall monitor
inside an allowed shell command. Joint live review can collect registered sessions and shared-service/web
evidence, preserving the exact input and collection interval. Missing evidence fails closed. Nested
agent sessions and adaptive monitor training remain unsupported. [ADR-0046](docs/adr/0046-before-tool-monitor-intervention.md)
describes this boundary.

Activation capture is opt-in. The gateway can retain token IDs when the upstream provides them;
NNsight recomputes activations in a separate engine. This is distinct from capturing activations in the
live serving process. The Inspect export is an analysis format and omits token IDs, raw tool logs and
other records identified in its metadata; it is not a validated training trace.

## Shared services and state

Board, chat, wiki, pastebin, Git and the wheel index are functioning applications with logged reads
and writes. A fresh wiki has no authored pages unless `services.wiki.docs` supplies them. Initial service
content requires explicit configuration and provenance; archived fixture histories are invented inputs.
The optional cache is a text object store, while clock, ledger and vault provide simulated time,
balances and canned secrets. These facilities are not substitutes for an application's actual cache,
compute allocation or credential authority.
Research is the default execution profile and rejects simulated time/balances, canned secrets, service
history seeds and authored task extensions. Regression fixtures explicitly opt into `profile: fixture`.
Service admin routes require a control credential, and each harness has its own upstream server password.

Optional artifact exchange publishes immutable copies of declared UTF-8 task outputs through the board
service. Native capture requires quiesced task execution before the original verifier; publication
then uses either completed measured attempts or an original-score threshold. Readers choose whether
to fetch the bytes, with board access permissions and exact content/version records. Publication,
listing and file reads are separate evidence; a read does not prove adoption. Author/score metadata
can be hidden, but content is not thereby anonymous. Shared-service artifact exchange and pre-verifier
publication remain unsupported. See [ADR-0056](docs/adr/0056-measured-interaction-conditions.md).

Persistent mode assigns tasks from a shared queue. Snapshots save declared workspace/service/shared-file
state and queue metadata, verify file hashes on restore, and record lineage and missing coverage.
Snapshots do not capture full live process state, harness sessions, complete container filesystems,
optimizer state or exact training continuation.
