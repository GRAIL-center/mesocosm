# ADR-0008: Live observation reads the run bundle; it never writes to it

Status: accepted · Date: 2026-10-18

## Context

Researchers need to watch a run while it happens: N agents side by side, each agent's session tree
(subagents spawned through OpenCode's `task` tool), a one-line summary of what each node is doing,
and a drill-down to what the model actually saw and answered, including any separate reasoning
field. The same view is needed after the fact, to replay a run at any speed.

Everything the view needs is already recorded append-only: the run bundle (`events.jsonl`,
`receipts.jsonl`) and the gateway log (`gateway.jsonl`). Adding an in-process event bus to the run
manager would couple the view to the run's lifetime and give the live and replay paths two
different code paths.

## Decision

- `mesocosm observe serve <bundle> [--gateway-log FILE] [--replay SPEED]` tails the files and
  serves one static page. The state builder (`mesocosm.observe.state.Observer`) is a pure
  function of the records; live tailing and timed replay feed the same builder, and scrubbing
  rebuilds the state at a point in time from the files.
- The observer is read-only and bounded: timelines, tool calls, request bodies and sessions are
  capped; a slow browser gets fewer, newer snapshots (no per-client queues).
- Node summaries are rule-based (latest tool call and its input, latest text, status). LLM-written
  summaries are a judging feature (M4) and, if added, are labeled as derived, never mixed with records.
- The page is a single HTML file with no framework and no build step; FastAPI, already a gateway
  dependency, serves it.

## Consequences

- The run manager, gateway and record formats are unchanged; the view can be pointed at any
  bundle, including ones produced by future components, as long as they keep appending JSONL.
- Subagent trees depend on OpenCode's `parentID` on child sessions (`session.created`), inherited
  into the parent's agent. Unverified against a real `task` tool call until a run exercises it.
- Model requests are joined to sessions by time (the agent's busy session at that moment), since
  the gateway sees agents, not sessions. The agent-level request list is the authoritative one.
