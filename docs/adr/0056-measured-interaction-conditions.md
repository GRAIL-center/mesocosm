# ADR-0056: Measured interaction conditions

Status: accepted

## Context

Research needs to vary the conditions behind repeated interaction without prescribing misconduct,
inventing histories, replacing original tasks, or silently accepting ineffective configuration.
The earlier reset/carry, scalar incentive and fixed peer-review controls leave several independent
questions coupled. Literature motivates additional controls but does not establish their effects
on Mesocosm's native tasks. See [primary sources](../research-literature.md) and the
[existing-variable audit](../variable-audit.md).

## Decision

Extend the native comparison path with explicit measured controls:

- `history` replays selected original observable records from the last k completed tasks into fresh
  sessions and reset workspaces. It does not summarize, restore arbitrary processes, remove shared
  service history, or guarantee that older information is absent from later generated artifacts.
- `learning.feedback` selects own outcome fields and delays delivery by completed episode boundaries.
  Queued, attached and completed delivery have different meanings. Feedback remains fixed-weight.
- `rewards.source: components` combines independently recorded task outcomes, peer acceptance,
  review accuracy and model-use measurements with explicit weights, scales and agent overrides.
  Original outcomes are immutable. Missing required components produce an unknown reward.
- Ratings review can use a seeded `random_ring`; the actual assignments are recorded and enforced
  by the service. Reputation exposes a window of original observations, without invented initial
  trust scores. It is an information treatment, not proof that participants trust it.
- Selective task assignment first measures all agents on a calibration task, then dispatches finite
  waves using round-robin or historical measured performance. Nonselected participants do not execute
  that task and do not receive artificial zero outcomes. This changes workload allocation, so it is
  incompatible with team-allocation rewards, peer reviews and shared-service team measurement.
- Artifact exchange uses the native verifier's explicit capture hook: pause and inspect the isolated
  task workload, capture declared workspace files, then run the original grader and publish eligible
  completed work. Shared-service exchange is rejected. Separate verifiers require paths within their
  original declared inputs; child-mount overlap is rejected. A verifier without the hook cannot use a
  generic unstable fallback. Consumers fetch actual bytes through the board service and its access
  policy. Publication and retrieval are recorded separately. The score belongs to the whole task,
  not an individual file certification. Metadata hiding is not content anonymity; privileged shared
  monitor evidence can include original metadata and file bytes under its existing budgets.

Every control has a schema, admission checks and evidence. Disabled nondefault conditions and malformed
exposure configurations are rejected. Conditions remain backend capabilities, not universal powers
granted by plugin registration. No model calls, optimizer steps or external messages are synthesized
by these mechanisms.

## Alternatives

Prompting agents to remember fewer rounds would leave old context accessible; bounded replay instead
opens fresh sessions and supplies recorded projections. Adding fake colleagues, prewritten rankings
or incident-specific files would confound behavior; reputation and artifacts derive from real actions.
Changing task tests to make reward hacking likely would destroy the independent measurement; incentive
composition leaves the original evaluator intact.

## Consequences and verification

The interfaces are additive; existing default configurations retain their original behavior. The
stronger admission checks deliberately reject formerly ignored configuration. Review-target enforcement
now refuses ratings outside the assigned relation. Historic legacy fixtures retain their separate path.

The training collector reconstructs compositional rewards from original verifier files, exact bound
gateway requests and the native verdict journal, rather than trusting computed metrics. New reward
provenance uses a v2 rule marker. Selective assignment, bounded history and artifact exchange remain
unsupported in policy training until their full rollout contracts are implemented.

Tests cover N-agent allocation, delayed feedback, fresh-session history, real HTTP artifact publication
and retrieval, deterministic matching and dispatch, and forged/missing training measurements. Runtime
tests replace inference and hardware; they are not real-agent acceptance or evidence of an incentive
effect. The next native acceptance must use at least three actual agents, original evaluators and
trajectory inspection. Audit sampling, adjudicated reporting, contracts and agent-selected partners
remain proposals, not implemented configuration.
