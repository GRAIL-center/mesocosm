# ADR-0027: The chat is a standing world service; peer review runs on top of it

Status: accepted · Date: 2026-10-18

## Context

The `chat` hostname existed only when `services.peer_review` was enabled (ADR-0017): direct messages to
one peer, capped per episode, plus verdicts. Everything else the agents said to each other went through
the board. A team has a chat whether or not anyone is grading verdicts, and most of the incidents the
platform wants to reproduce (the OpenAI–Hugging Face coordination on an unsanctioned board, Colosseum's
topology results) are about who can message whom and under what limits. A message service bolted to one
protocol cannot be that research variable.

## Decision

- **`services.chat` is a service of the world like the board.** Enabled, it answers at `http://chat/`:
  `POST /api/messages` with `to` naming a member (a direct message) or a `#channel`, `GET /api/messages`
  for the agent's direct messages and every channel (`?channel=`, `?since=`), a usage page listing the
  members and the channels. Channel messages are public to every address; direct messages reach their
  two parties. Every read and write is logged by identity with `to`, `channel`, `chars`, `message_id`
  and the message hash, like the board.
- **The limits are conditions of the chat.** `services.chat.max_chars` refuses longer messages;
  `services.chat.max_messages` caps what one agent may send: per episode when peer review opens
  episodes, per run otherwise. A refusal is logged with its reason. Both are `null` by default: an
  uncapped chat is a choice too.
- **Peer review rides on the chat.** `services.peer_review` keeps the protocol (topology, verdicts,
  episodes, the shared reward, feedback) and requires `services.chat.enabled`; its own `max_chars` and
  `max_messages` are gone, and a manifest that still sets them is rejected with a pointer, never
  silently ignored. The verdict endpoints exist only under peer review; the "no episode is open" refusal
  applies only then. The judge's `verdict_without_evidence` counts a channel message as delivered to
  every other member.
- **The observer shows the exchange.** Chat messages appear on the agent's timeline with their
  recipient or channel; requests through the web gateway (ADR-0022) appear too, with host, decision and
  size, refusals counted per agent; the egress check's probes and the harness's own update checks stay
  out, as in the judge.
- **Snapshots carry the pages written at run time** (`wiki-edits/`), which ADR-0020 added after the
  snapshot's item list was written.

## Consequences

- ADR-0017's channel caps move to `services.chat`; `examples/peer-review.yaml` enables the chat with no
  channels (the paper's protocol has none) and the same caps. The scenario's wide-channel stage patches
  `services.chat`.
- A world may now have a chat without peer review (`examples/world-channels.yaml` has `#general`); a
  task that asks for it says so in its own text.
- An agent-visible channel is one more surface to keep cheap in context (ADR-0023): a list read is the
  messages themselves, so the task text should ask for `?since=` polling rather than the whole history.
