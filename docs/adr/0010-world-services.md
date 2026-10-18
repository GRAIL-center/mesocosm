# ADR-0010: The world is a set of ordinary services that identify callers by address

Status: accepted · Date: 2026-10-18

## Context

Agents must live in something that looks like a real team environment (board, repositories, docs,
package installs) without any route to the internet, and every read and write must be attributable
to an agent so that information flow between agents is a recorded fact, not an inference. Hidden
channels must be a condition the platform switches on and off and can prove it did.

## Decision

- One container (`mesocosm.env.services`, `spike/docker/Dockerfile.services`) serves several
  hostnames on every sandbox network: `board` (REST + HTML), `wiki` (markdown pages with
  `visible_from` / `visible_after_seconds`), `git` (smart HTTP via `git http-backend`, no auth),
  `pypi` (PEP 503 index over a wheel cache filled at image build), `sites` (static mock sites) and,
  only when the manifest enables the channel, `pastebin`. Agents use curl, git and pip as shipped.
- Callers are identified by address: the compose renderer assigns fixed addresses per network
  (172.30.<n>.0/24: gateway .2, services .3, control .4, agent k .10+k) and passes the
  address → agent map to the services. A claimed author in a request body is recorded separately
  from the identity, never trusted.
- Every request is one JSONL line in `/runs/<label>/services.jsonl` (ts, service, op, agent,
  address, key, sha256 of the content, a 120-char summary). The run manager copies it into the
  bundle (`native/services/services.jsonl`) and records `service.<op>` events, so a message's
  write, its read by another agent and the model request that then contained it are three
  records joinable by agent, time and content hash.
- Hidden channels are manifest entries (`channels: [{type: pastebin}]`, `shared_dir`). The
  in-container check (`egress-check`) prints, for every supported channel, whether it is present;
  the run manager turns those lines into a receipt per channel and aborts the run if any agent's
  observation contradicts the manifest. Unknown channel types are `rejected_unsupported`.
- Services are on by default (`services.enabled: true`); a world without them is a configuration,
  not the norm.

## Consequences

- The isolation argument stays intact: the services container has no `edge` network and no egress;
  it is the only way agents on separate networks can affect each other.
- The package index limits agents to the mirrored packages. That is a visible limit (the index says
  what it has), not a silent failure; the list is per manifest and extends the image at build time.
- Fictional accounts and credentials (traps) are not part of this decision; identity is by address.
  Accounts come with the M4 scenarios and will be recorded as *claimed* identity next to the
  address-derived one.
- Fixed addresses make it possible to run the control plane on the Docker host (or inside rootless
  Docker's network namespace), which settles the M1 question of where `mesocosm run` executes.
