# ADR-0038: Remove environment tells the agents can see

Status: accepted · Date: 2026-10-19

## Context

ADR-0019 keeps the platform's *name* out of the agent containers, but a world can still read as a test rig
through smaller tells — details that are accurate for the control plane yet wrong for the fiction the agent is
in. Three were visible to any agent:

- **The clock announced that it was simulated.** `GET http://clock/api/time` returned `speed: 60`, the manifest
  `origin` string, and `elapsed_seconds` (seconds since the run started). An agent reading that learns the
  world runs at 60x and that the run began seconds ago — a real calendar service reports neither.
- **The internal domain was `corp.example`.** The RFC 2606 `.example` TLD is unmistakably a placeholder.
- **The getting-started wiki contradicted itself.** It opened with "There is no internet access from the
  workstations" and then listed an **Internet** entry describing the company proxy. A real runbook does not
  contradict itself on the first screen.

None of these change what the agent can *do*; they change whether the world is believable, which is what
decides whether an elicited behaviour would also appear in a real deployment.

## Decision

- **The agent-facing clock looks like an ordinary calendar.** `sim_clock(..., admin=False)` (the default, used
  by `GET /api/time` and the clock service's front page) returns only `now` and `epoch`, plus `deadline` and
  `remaining_seconds` when a decommission time is set — the countdown is diegetic and meant to be seen
  (ADR-0034). It no longer returns `speed`, `origin` or `elapsed_seconds`. The control plane's `/admin/state`
  and the verifier's `/admin/state/world` export pass `admin=True` and still get the full mechanics, so the
  observer's countdown and the receipts are unchanged.
- **A realistic internal domain.** The example worlds use `northwind.internal` instead of `corp.example`:
  Northwind is a long-standing fictional sample company, and `.internal` is the ICANN-reserved private-use TLD,
  so the domain reads like a real corporate network yet can never resolve on the public internet — realistic
  and collision-free, which `corp.example` was not. The `domain` field is still optional and free-form; this
  only changes the shipped examples and their persona briefs ("at Northwind").
- **The wiki is internally consistent.** getting-started now says direct internet is blocked and approved sites
  go through the company proxy, matching its own Internet entry and the web gateway (ADR-0022). The spike
  world, which ships no web gateway, keeps "no internet access" because there it is true.

## Consequences

- A world where `internet.enabled` is false still reads consistently: the wiki describes the proxy as the only
  external route, which is exactly what the sandbox enforces; when the gateway is disabled the approved-sites
  set is simply empty, not a contradiction.
- The clock change is behind the existing agent/admin split, so no observer or verifier code changes; a test
  pins that the agent view is exactly `{now, epoch}` (plus the countdown when set) and the admin view keeps
  `speed`/`origin`/`elapsed_seconds`.
- This is a pass over the tells found today, not a claim that the world is now indistinguishable. Remaining
  realism work — seeding the world with real history, and rewriting the hand-written world tasks so they read
  like tickets rather than API recipes — is tracked separately.
