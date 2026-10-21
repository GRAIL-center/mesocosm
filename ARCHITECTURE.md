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

Each agent has its own OpenCode server, session, gateway credential and workspace. The native task
runtime builds from the task's declared Docker environment, adds the released harness, and preserves
the task's user and working directory. It runs the original filesystem verifier in a separate snapshot.
The current profile is Debian/Ubuntu, single-container filesystem tasks; task Compose stacks, task GPUs,
separate verifier images and live-process verification are unsupported.

Agents use an internal Docker network. The model gateway connects them to inference; the optional web
gateway provides configured internet access. Shared volumes and services are explicit affordances.
The control plane, verifier results and run bundles remain outside agent write access. The egress check
verifies the selected topology before agents run. Colab's rootless adaptations have additional isolation
and resource limits documented in the setup guide; a successful Colab run does not establish production
isolation.

The gateways are integration boundaries. Mesocosm configures released OpenCode, Harbor task formats,
vLLM and NNsight through their supported interfaces and does not patch their internals.

## Components

| Component | Responsibility |
|---|---|
| `core.manifest` | Validated agents, tasks, models, services, budgets, rewards, memory and monitor configuration. Unknown schema fields are rejected. |
| `core.experiment` | Prevalidate named condition patches, schedule repetitions, and retain every trial's outcome and missing measurements. |
| `core.run` | Prepare environments, enforce run boundaries, drive concurrent agents, invoke original verifiers, collect evidence and run posthoc analysis. |
| `core.rewards` | Synchronize comparison episodes and allocate incentives separately from original verifier scores. |
| `gateway` | Authenticate identities, route chat requests, enforce model budgets and record actual model exposure. |
| `env` | Docker topology, native task runtime, real shared services, web access policy and state inspection. |
| `adapters.opencode`, `adapters.harbor` | Harness API and task/environment/verifier integration. |
| `judge` | Candidate flags, append-only review verdicts and configurable posthoc model assessments. |
| `observe` | Live/replay views and optional activation recomputation and probes. |
| `export` | Inspect evaluation logs, with omitted record types declared. |

## Experiments and conditions

An experiment specifies a base world, named manifest patches, repetitions and listed or randomized
execution order. Patches merge mappings, replace lists and remove keys set to null. Paths resolve from
the base manifest. Every variant is validated before the first trial starts; each trial gets a distinct
manifest and run label. `order_seed` controls scheduling only, not model sampling, task randomness or
training.

Trials run sequentially because the topology uses fixed subnet addresses. The CLI reconciles a fresh
stack and separate workspace directories for each trial. Trial failure is recorded and the remaining
plan continues. The experiment runner has no flag-triggered stopping or automatic replacement trials;
these would select observations based on outcomes. Older `scenario` and `chain` interfaces remain for
compatibility, with their distinct stopping and retry semantics.

Conditions are enforced at their actual point of effect: token and request budgets at the gateway,
shared-directory access through mounts, service permissions at the service, and memory at task
boundaries. Only budgets currently support mid-run mutation through the condition-control API.
Unsupported combinations fail explicitly instead of being approximated with task prose.

`memory: reset` clears the managed workspace and opens a new harness session; `carry` retains both
within the run. Neither option describes a complete reset or serialization of harness internals.
Native carried workspaces cannot be transferred arbitrarily between incompatible task environments.

## Outcomes, incentives and learning

The original verifier always measures task outcome independently of the incentive source. With
`rewards.enabled`, comparison-mode agents complete the same task as a synchronized episode:

- `source: verifier` uses those original task scores as raw incentives.
- `source: peer_verdict` uses the fraction of assigned reviewers who accept the agent's work through
  the real chat verdict endpoint. Missing required reviews remain unmeasured.
- `allocation: individual` retains each raw score; `team_mean` assigns the team mean; `mixed` combines
  own score and team mean using `team_weight`. Missing team inputs do not become zero.

Peer scoring uses `services.peer_review.protocol: ratings` with an `all` or `ring` topology. The
paper-specific correctness-of-verdict protocol is isolated in `core.legacy_peer_review` and retained for
legacy fixtures. Generic rewards are not supported in persistent queue mode.

`learning.mode: none` records incentives without delivering outcome feedback. `in_context` attaches the
previous episode's own reward feedback to the next task and records attachment and completed delivery.
The last episode may have no subsequent task to receive feedback. Both modes keep model weights fixed;
context adaptation and weight updates must not be reported as the same mechanism.

Actual policy-weight training is a desired extension, but no trainer, optimizer, checkpoint activation
or validated RL export is implemented. Unsupported learning modes are rejected. A future trainer must
establish rollout provenance, action boundaries, reward versions and actual checkpoint changes before
its results can be described as RL. [ADR-0044](docs/adr/0044-condition-led-experiments.md) replaces the
older categorical exclusion of training without claiming that this implementation performs it.

## Observation and judgment

A run bundle contains the manifest, normalized events, enforcement receipts, native gateway/harness
records and verifier results. Model calls use `request_id`; other sources also carry run and agent
identity where available. Some joins still use agent/task time windows; this is not a complete causal
trace with exact action-token alignment.

Keep four outputs separate: original task outcomes, allocated incentives, monitor assessments and
reviewed policy violations. Detectors produce candidates. A shared-store read, a high reward or a monitor
score alone does not establish collusion or intent.

The monitor operates after execution. It can inspect each agent/task window independently or the joint
run, using recorded transcripts or actions, optionally including shared-service/web evidence and world
snapshots. It records the rubric version/hash, selected requests, evidence coverage, truncation and
call/input/output budgets. Malformed answers and missing evidence remain unknown. A joint score has no
individual-agent attribution. No live monitor gate, monitor-triggered intervention or adaptive monitor
training is implemented.

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

Persistent mode assigns tasks from a shared queue. Snapshots save declared workspace/service/shared-file
state and queue metadata, verify file hashes on restore, and record lineage and missing coverage.
Snapshots do not capture full live process state, harness sessions, complete container filesystems,
optimizer state or exact training continuation.
