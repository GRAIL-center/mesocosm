# ADR-0039: Seed the world with a past

Status: accepted · Date: 2026-10-19

## Context

A world started empty: the shared repository was a bare repo with no commits, the board and the chat had no
messages, and only the wiki had content (from its mounted `docs` directory). An agent that cloned the repo got
an empty project; one that listed the board saw nothing. For a world that is supposed to read as an ongoing
workplace, "no history" is one of the strongest tells that it is a fresh test rig, and it removes the very
thing that makes multi-agent dynamics realistic — a shared context the agents did not create.

ADR-0024 forbids *scripted behaviour*: no bots posting on a schedule during a run, no `world.events`, no mock
services. It explicitly keeps *state*: account roles, planted credentials, the wiki's pages. Initial history
is state of the same kind — it is laid down once, before any agent runs, and nothing acts on a schedule during
the run.

## Decision

- **A world may ship a seed directory (`services.seed`, relative to the manifest).** It is mounted read-only at
  `/seed` (outside the writable `/data`) and loaded once at service start:
  - `seed/board.jsonl` — prior posts (`author`, `title`, `body`, optional `ago_seconds` and `replies`), loaded
    only if the board is empty.
  - `seed/chat.jsonl` — prior messages (`from`, `text`, and either `channel` or `to`, optional `ago_seconds`),
    loaded only if the chat is empty. A `from`/`to` that names a current agent is stored as that agent's id, so
    it reads back as the username and a seeded direct message reaches that agent; any other name is a former
    colleague, kept as written.
  - `seed/git/<repo>/` — a working tree committed as the repository's initial history, so `git clone` yields a
    project with a past. The commit is authored by the team identity at a back-dated time; it is made only when
    the bare repo is newly created.
- **Seeds are timestamped in the past.** Each entry defaults to a time before the run start (`ago_seconds`),
  so the history predates the agents rather than appearing at t=0.
- **Loaded once, never re-applied.** Each store seeds only when it is empty, so a restart, a resume or a
  snapshot restore over existing data does not duplicate the past. This matches how the ledger and the cache
  already seed (ADR-0028, ADR-0033).
- **It is state, not behaviour (ADR-0024).** Nothing posts or commits during the run; the seed is the world's
  condition at t=0. A seeded author who is not one of the run's agents is a name in the history, not an actor —
  there is no process behind them. A background colleague who *acts* during the run is still an `agents[]`
  entry, as ADR-0024 requires.

## Consequences

- `examples/world/seed/` gives the shared `project` repo a small real history, the board a couple of prior
  threads and `#general` a short prior exchange; `examples/world-channels.yaml` loads it. A world that wants a
  blank slate simply omits `services.seed`.
- Seeding runs on the control-plane-built service container from a read-only mount; no agent gains a route, and
  the containment invariant (ADR-0022) is untouched.
- The git seed is a single initial commit, which is enough to make the repo real; a longer, multi-author
  history can be supplied later as a prepared `git bundle` without changing the manifest surface.
- Not seeded here: the pastebin and the honeypot (their emptiness is part of what they test) and the wiki
  (already content-driven by its `docs` mount).
