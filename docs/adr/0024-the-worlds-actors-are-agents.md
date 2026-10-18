# ADR-0024: The world's actors are agents; nothing in it is scripted or mocked

Status: accepted · Date: 2026-10-18

## Context

Two things in the world were not real. The `sites` service (ADR-0010, M2's "minimal mock external
world") served hand-written pages pretending to be a vendor's documentation, from before the web
gateway existed. `world.events` (ADR-0020) let a manifest schedule posts, replies, chat messages and
wiki changes signed with a made-up name, performed by the run manager: a colleague who posts a
deadline at +15 s and never answers a reply. The maintainer's direction is that the agents interact
with a real world: other agents, real services, the real internet under policy. A scripted actor is
a stage prop, and an agent that answers it is interacting with nobody; a mock site is a page no one
maintains. Both are also exactly the kind of thing that makes a world recognizable as a test.

## Decision

- **Every actor in the world is an agent in the manifest.** A colleague, a lead, an operations
  account that posts freezes: each is an `agents[]` entry with its own persona, tasks, budget and
  model, observed like any other, logged under its own identity. There is no `world.events`, no
  `/admin/world`, no `origin: world` record. If a world needs a background actor, the manifest adds
  an agent for it.
- **No mock services.** The `sites` service and `services.sites` are removed, along with the
  example vendor pages. External information comes from the real internet through the web gateway
  (ADR-0022) under an allow list, and from the world's own real services: the board, the wiki, the
  repositories, the package mirror (real packages), the chat. The example task that read a vendor
  changelog now reads the package's version from the real index.
- **What stays, and why.** `conditions.exposure` (a wiki page visible from a set time) is a condition
  the researcher controls about information availability, not an actor; it stays, with its receipt.
  Account roles and traps (ADR-0020) are permissions and planted credentials, part of the world's
  state, not behavior. The peer-verification runner's feedback messages (ADR-0017) are the
  experimental protocol's reward signal, labelled `runner`, not a colleague.

## Consequences

- ADR-0020's `world.events` clause and ADR-0010's `sites` service are superseded; their roles and
  journals remain.
- A richer world costs model calls: a background colleague is an agent with a budget. That is the
  honest price of real interaction, and the budgets and receipts already account for it.
- AGENTS.md gains the rule so this does not come back: actors are agents, services are real or the
  real internet, nothing is scripted.
