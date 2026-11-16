# ADR-0070: P/S/E experiment workflow

Status: proposed

## Context

SwarmStill design agents must choose who acts (P), their interaction structure (S),
and their task/resource/reward environment (E), then validate, execute and compare the
combination. The restricted inverse interface is one study profile, not the whole skill.

## Proposed decision

Use one canonical experiment core behind CLI/MCP clients. A profile-aware P/S/E composer
targets existing native manifest, integration, learning and evaluation types. Reuse immutable
artifact identities and durable jobs. Separate research-designer and blinded-inverse access
without creating divergent execution semantics.

Distinguish policy weights/update groups, information visibility, workflow/scheduling,
environment state/reward, and independent outcome measurement. Enforce each at its actual
backend boundary. Publish focused capabilities, compatibility requirements and actionable
safe diagnostics. Freeze non-intervened fields for comparisons and retain every scheduled
trial. Preserve exact source-linked observation/action/checkpoint provenance.

New population/graph/scheduler/comparison and PPO/GRPO capabilities require real implementations
and linked acceptance evidence; this ADR does not add them. No prompt approximation or renamed
optimizer is permitted. See the design and current status.

## Consequences

ADR-0069 supplies bounded real P selection/training in the current inverse profile. The
remaining work is explicit: a shared capability/validation/compiler contract, scoped population
and task/tool catalogs, visibility/scheduling enforcement, general execution/comparison planning,
and method-specific training providers. Research and hidden-intervention acceptance remain separate.
