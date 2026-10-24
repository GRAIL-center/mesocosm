# Research controls

These controls build experiments around real tasks and real agents. They constrain access and
resources and preserve what was observed; they do not prescribe communication or misconduct.

## Execution and stopping

`containment: required` is the default. The native CLI inspects each task container before the first
agent turn and requires private PID containment on the controller's Linux kernel. It refuses unknown
or unsupported evidence. A remote daemon or macOS controller cannot establish this local kernel proof.
`containment: adapted` explicitly permits such a host while preserving its limitations. Colab's current
runtime wrapper removes PID isolation: neither cross-process containment nor descendant termination
is established there. This setting is separate from the task resource-limit override.

A stop marker interrupts an active harness turn. The runner records model-admission stop, session abort
and Docker termination separately. It kills only the full container ID whose project/service labels
match, then inspects its final state. Acknowledged signals are not proof of exit. Missing evidence,
restarted containers or shared PID namespaces leave workload termination unknown. An inactive/crashed
runner cannot service a stop marker; gateway refusal alone does not terminate tools.

## Access and shared resources

The following fragment belongs in a world containing these three agent IDs and enabled chat:

```yaml
containment: required
isolate_agents: true
operator_id: researcher-01  # controller metadata; not an agent persona
services:
  git: [project]
  chat:
    enabled: true
    channels: [general]
  access:
    services:
      chat: {read: [agent-1, agent-2, agent-3], write: [agent-1, agent-2, agent-3]}
      wiki: {read: [agent-1, agent-2, agent-3]}
    direct_messages:
      agent-1: [agent-2]
      agent-2: [agent-3]
      agent-3: [agent-1]
    channels:
      general: {read: [agent-1, agent-2, agent-3], write: [agent-1]}
    git:
      project: {read: [agent-1, agent-2, agent-3], write: [agent-1]}
resources:
  model_requests: 90
  completion_tokens: 150000
  model_concurrency: 3
  service_requests: {chat: 100, git: 30}
```

Omitting `services.access` preserves existing service access. Providing it makes omitted grants deny
access. Chat also requires the applicable service grant; Git grants apply by repository. Account roles
remain additional requirements. Unknown callers cannot consume an authenticated identity's grant.
The service enforces logical routes even under another hostname. MCP tools use the same underlying
board/wiki checks. Liveness endpoints reveal no content and consume no research quota.

An opted-in access graph requires isolated agent networks and rejects `shared_dir`, which bypasses
service permissions. Shared resources permit information relay among their writers/readers. Allowed
internet is a separate communication channel. No universal information-flow or provenance claim is made.

Shared model limits count authenticated agent requests across the run; reviewer calls retain separate
monitor budgets. Requests consume quota at admission, including upstream attempts that fail. Output
caps are reserved while requests are active and reconciled from reported usage. Missing usage blocks
further output-token-limited calls. Concurrency limits active gateway dispatch/response lifetimes;
transport closure does not prove the inference server cancelled GPU work. Unconfirmed closure retains
the reservation. Gateway pools recover from their persisted accounting journal; unresolved requests
remain reserved with unknown usage after restart. Service counters recover from their admission journal.
These guarantees require the same intact log; log loss is not quota recovery. Service request limits
count allowed dispatch attempts, including handler failures, and do
not count denied ACL requests or liveness probes. These are not token billing or CPU/GPU schedulers.

## Joint live monitoring

```yaml
judge:
  monitor:
    enabled: true
    model: reviewer  # an existing model catalog entry
    timing: pre_action
    scope: joint
    visibility: actions
    include_shared: true
    decision_delay_seconds: 0.5
    decision_timeout_seconds: 30
    max_calls: 200
    max_input_chars: 120000
    on_flag: terminate
```

Each proposed top-level tool action is held while the monitor sees the current registered sessions,
completed session snapshots and optional shared-service state/journals. Not-yet-started agents are
explicitly identified. Transcript visibility includes messages; action visibility includes tool records.
Shared records include service/web information independently of transcript visibility. Exact reviewer
input is saved under `native/monitor/` with its digest. Collection is not atomic; another already
authorized action may execute while evidence is gathered. Decision delay is a hold before collection,
not a simulation of stale observations. Reviewer timeouts, unavailable peers, oversized evidence and
exhausted call budgets deny the proposal and abort the session. Nested sessions and mediation inside
an already approved shell command remain unsupported.

## Repetition, evaluation and evidence

Set `baseline: true` in an experiment to require at least three agents, native materialized tasks with
original verifiers and declared model revisions (`model.revision`, a 40-character commit). Input trees
and generated manifests are fingerprinted before execution and checked before trials. A changed input
stops remaining trials; the plan and completed outcomes remain recorded. The declared revision is not proof of
the model loaded by a remote server. Dockerfile/build-context hashes likewise do not pin mutable image
tags or package indexes. `order_seed` only controls trial ordering.

Set `evaluation_world` in a training specification to a separate world with content-disjoint tasks.
The controller executes that world before and after training with no optimizer update, verifies its
policy provenance, and stores separate evaluation results. This detects exact overlap, not semantic
duplicates or prior model pretraining exposure. Task performance, rewards and reviewed behavior stay
separate; a weight update or different evaluation score alone is not a demonstrated learning benefit.

```bash
uv run mesocosm evaluate runs/<run-id> --json
uv run mesocosm judge review-assessment runs/<run-id> <event-id> rejected reviewer-01 --json
uv run mesocosm audit graph runs/<run-id> --json
uv run mesocosm audit anchor runs/<run-id> /independent-storage/<run-id>.anchor.json --json
uv run mesocosm audit verify runs/<run-id> /independent-storage/<run-id>.anchor.json --json
```

For assessment review, `confirmed` means the reviewed evidence violates the rubric, `rejected` means
it does not, and `unclear` remains unknown. Review a prespecified sample including negative predictions
to estimate false negatives; an absence of flags supplies no ground-truth safe labels. Prospective
assessment labels concern proposed actions, not executed violations. Report the sampling process
separately from the confusion matrix.

Event IDs and exact message/object-version links show recorded exposure. They do not establish why
an agent acted. Agent instance, session, operator and verified policy identities are separate fields;
unknown provenance stays null. Detached anchors cover exact bundle bytes and detect changes against
the reference you retain. Creating a local anchor does not supply independent witness custody, prove
complete capture or authenticate the truth of a logged event. Later reviews require a new anchor;
keep previous anchors. Raw bundles can contain credentials and should remain private.
