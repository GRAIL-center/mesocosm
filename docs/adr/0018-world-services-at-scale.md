# ADR-0018: The world services keep journals, page their lists and count exposure from the run start

Status: accepted · Date: 2026-10-18

## Context

The world services (ADR-0010) stored the board, the pastes and the chat as one JSON file each,
rewritten whole on every write, and `GET /api/posts` returned every post with its body. A load
check with 20 virtual agents over a board of 3,000 posts, a 300-page wiki and a 50 MB wheel
(`scratchpad/bench_services.py`, in-process ASGI) measured 45 requests/s in total: a board list took
1.2 s and answered 1.1 MB, a post 0.5 s, the package index 1.5 s because it hashed the wheel on
every request, and the log line of a list read named every post (15 KB). The run manager copied the
gateway log and the services log by reading each file whole into memory, and `asyncio.gather` left
the other agents' runners alive when one failed, so a stalled harness could keep a run (and in a
chain, the next run's bundle) open indefinitely. The exposure schedule counted from the moment the
services container started, which precedes `mesocosm run` by tens of seconds on the Docker topology.

Pilot runs are longer and larger than the smoke runs: persistent worlds, more agents, thousands of
posts. The world has to stay quick enough that the agents never wait on it, and its records have to
stay bounded.

## Decision

- **Append-only journals.** Board, pastes and chat are `board.jsonl`, `pastes.jsonl` and
  `chat.jsonl` under the services data directory: one line per write (`kind: post | reply`,
  `kind: epoch | message | verdict`, one paste), replayed into memory at start. Nothing is rewritten.
  Snapshots copy the journals (`SERVICES_ITEMS`).
- **Paged lists.** `GET /api/posts` returns the newest page: `limit` (default 50, at most 200),
  `offset`, `since`, and `total`. The HTML view shows the same page. A `read_list` log line names the
  posts of that page only, with `total` and `offset`; the exposure join (`post_ids`) is unchanged in
  meaning and bounded in size.
- **Caches keyed by file identity.** Distribution hashes and wiki front matter are computed once per
  `(path, mtime, size)`. Visibility is still decided per request.
- **The exposure clock is the run start.** The run manager calls `POST /admin/clock` with its own
  time before verifying the exposure condition; `conditions.exposure` seconds count from there, and
  the receipt records the anchored `services_started`. This supersedes the clause of ADR-0011 that
  made the schedule relative to the services start.
- **Streamed copies.** `GET /mesocosm/records?run=&since=` on the gateway is NDJSON, read from the
  log line by line; the run manager writes the bundle's `gateway/records.jsonl` and
  `services/services.jsonl` record by record (`RunBundle.open_native`).
- **One failure cancels the run.** Agent runners execute in an `asyncio.TaskGroup`; the first
  failure cancels the others and is the run's recorded error. The egress check runs on all agents at
  once.
- The services log keeps its file open, line-buffered.

## Consequences

- Measured after the change, same benchmark: 740 requests/s; board list p50 30 ms (14 KB answer),
  post 16 ms, package index 13 ms, wiki index 80 ms; `services.jsonl` 4.1 MB instead of 13.2 MB for
  the same traffic (`docs/capability-matrix.md`, World services).
- Agents that want the whole board page through it (`offset`); task instructions that say "list the
  posts" still see the newest 50, which covers every smoke and pilot task written so far.
- A world snapshot taken before this change (`board.json`) does not restore; none is kept.
- The exposure timing of existing manifests shifts earlier by the services-to-run gap; the Colab
  learnings and the examples are updated to count from the run start.
