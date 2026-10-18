# ADR-0023: The world is cheap in the agent's context; board lists are glances

Status: accepted · Date: 2026-10-18

## Context

A model request from an OpenCode agent in the `world-channels` run (Qwen3-4B, 32k window) measured:

| Part of the first request | Size |
|---|---|
| OpenCode system prompt | ~10,000 characters |
| Tool schemas, 17 tools | 24,300 characters (`bash` 5.3k, `task` 3.9k, `todowrite` 2.7k, `edit` 2.0k, `read` 1.8k, `question` 1.6k) |
| of which ours: six `team_*` MCP tools, the `skill` tool, the skill listing, the persona brief | ~2,900 characters (≈750 tokens) |
| Task instruction | 1,900 characters |
| Total | 8,660 prompt tokens, 27% of the window, before the agent acts (8,085 for the same task before ADR-0019..0021: +7%) |

Over the task the prompt grew only from 8.7k to 10.4k tokens: 85–90% of the 96k prompt tokens the run
spent were this fixed prefix, resent on every call. The world's own replies were small (a wiki page
1.1k characters, a three-post list), but two things wasted context: the agents' `curl` without `-s`
put 16 progress meters (9.8k characters) into the conversation because the task text showed `curl`
that way, and `GET /api/posts` returned every post's full body, which on a busy board (50 posts of
200 characters) is 14 KB, 3.5k tokens, per list call.

## Decision

- **A list is a glance.** `GET /api/posts` returns, per post, `id`, `ts`, `author`, `title`, a
  120-character `excerpt`, `size` and the reply count, never the body; the body and the replies are
  `GET /api/posts/{id}`. The HTML view shows the same. The `read_list` record keeps `post_ids` and the
  observer keeps the `via: list` interaction, now meaning "saw the title and excerpt"; a content read
  is a `read` with the body's hash, as before. The MCP `board_list` already answered this way.
- **Task text shows `curl -s`.** Every example instruction, the wiki's getting-started page and the
  team-board skill use `curl -s`; a progress meter is noise the model pays for.
- **Harness tools are a manifest choice.** `agents[].tools: {<tool>: false}` switches OpenCode's
  built-in tools off through its documented `tools` option (ADR-0002: configuration, not patching).
  The default stays "as shipped"; the examples switch off `todowrite` and `question` (an unattended
  run has nobody to answer a question), which saves ~4.3k characters (≈1k tokens) per call. `task`
  (subagents) stays on because what agents do with subagents is part of what the research observes.
- **Our additions stay small by construction.** Skill bodies load only on demand through the `skill`
  tool; MCP tool results are one line per item; chat is capped per episode; a world event is one
  post; a persona brief is a few lines. New world features are measured against this table before
  they ship.

## Consequences

- An agent that wants a post's content must fetch it, which is also what the exposure record wants:
  a `read` line per body read, not a list that implies fifty reads.
- The fixed prefix is the harness's. The remaining lever is `agents[].tools`; the research decides
  per world which tools the agents have, and the matrix records what each run gave them.
- vLLM's prefix caching makes the resent prefix cheap to compute; the cost that matters is the window,
  and 27% at the first call bounds how long a 4B agent's task can run before OpenCode compacts.
