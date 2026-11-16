# ADR-0068: Restricted fixed-policy designer MCP

Status: accepted

## Context

The fixed-P inverse study needs a designer to choose enforced S/E conditions without
seeing student identities, populations, private evaluation or held-out interventions.
The trusted research workbench exposes records and paths and is unsuitable for this boundary.

## Decision

Add the optional official Python MCP SDK dependency (`mcp>=1.30,<2`) and a local stdio
`mesocosm designer serve` command. Expose exactly capabilities, corpus search/statistics,
event reads, spec checks and final submission. The operator supplies a finite allowlist
of exact JSON choices over the supported S/E factor names; every other field is locked.
No task, model, population, persona, skill, prompt, MCP, network, judge, observation or
budget input is accepted. Even string/list/path choices must equal an operator-approved
complete value, so designers cannot author free text through those fields.

`check_spec` invokes the existing `configure_experiment` and `validate_experiment`
implementations against a private fixed base world. Full configurations, packages and
native diagnostic text stay private. Only spec hashes and bounded validation feedback
are public. A transaction freezes one immutable final per operator-chosen session;
identical retries return its original hash, including after restart.

`mask-corpus` uses source-hash-bound operator review selecting each event and each text
field explicitly. Metadata, conditions, system/developer roles and reward/context events
are excluded. Teacher run bundles use the existing native evidence normalizer, including
recorded native turns. Bundle review also binds the manifest hash. Operator source bytes,
source-to-public citation mappings, policy and projection hashes remain private; public
citations are newly assigned stable ids and identities are locally aliased. Optional
literal redactions and forbidden-literal checks are mechanical checks only. Operators
must inspect ordinary event text for semantic condition, reward, identity, path and
held-out leakage before approving it. This is not an automatic semantic guarantee.
Canonical known `split: test` or `partition: test` declarations, including original
bundle metadata and event declarations, reject the entire source before projection or writes.

Every attempted tool call records arguments, timestamp and result hash in a private
hash-chained audit. Responses are bounded to 24,000 UTF-8 bytes, with event and exact
long-text continuation offsets. No raw-record, execution, evaluator or arbitrary-path
operation is registered.

## Consequences

The server is a restricted tool surface, not an OS sandbox. A real designer must run
in a fresh context and separate OS sandbox with the server outside it; only the
reviewed public corpus and intended MCP access may cross that boundary. The trusted
operator CLI and private configuration must never be handed to the designer.
Software tests cover stdio transport, exact tool discovery, native validation, locked
fields, immutable submission, audit and bounded masked projections. Real-agent design
acceptance and behavioral transfer remain unverified until independently run.
