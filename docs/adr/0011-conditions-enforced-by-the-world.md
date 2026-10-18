# ADR-0011: Exposure timing, accounts and traps are conditions enforced by the world services

Status: accepted · Date: 2026-10-18

## Context

M3 needs conditions beyond budgets: when a piece of information becomes readable, and, for the M4
scenarios, credentials nobody was given. ADR-0003 forbids approximating a condition with prompt
text: a condition is enforced at a component that logs it, and the run must prove that the component
is in the requested state before any agent runs. ADR-0010 left accounts out and kept identity by
address. Agents also ran one after another, which makes any condition about information flow between
agents meaningless.

## Decision

- **Exposure timing is a manifest condition.** `conditions.exposure: {wiki: {<slug>: <seconds>}}`
  reaches the services container as `MESOCOSM_WIKI_VISIBILITY` and overrides the page's front
  matter. Seconds count from the services process start; the receipt records that start as
  `services_started`, so the absolute time a page appeared is in the bundle. Reads of a hidden page
  are logged as `read_hidden`.
- **The services report what is in force.** `GET /admin/state` lists channels, the visibility
  schedule, repositories, accounts, traps and the start time. Requests from agent addresses get the
  same 404 as any unknown page and an `admin_denied` line. The run manager compares the state with the
  manifest and writes a `conditions.set` receipt (`enforced_at.component = services`,
  `verification.method = services /admin/state`); a mismatch aborts the run as
  `failed_exposure_verification` before any agent starts, like a failed channel check.
- **Accounts are claimed, never trusted.** `services.accounts: [{user, password, trap, note}]`
  reaches the container as `MESOCOSM_ACCOUNTS`. The services accept HTTP basic auth and never require
  it; identity stays address-based (ADR-0010). Every logged line carries `account` and
  `account_valid` when credentials were sent, and `trap: true` when they belong to a trap account.
  The first use of a trap account in a request writes one extra `trap_triggered` line with the
  account's `note` (where the credential was planted). The observer shows it as a flag on the agent.
- **The platform records, it does not judge.** Whether using a planted credential is misaligned
  in a given scenario is for the judge (M4); the services only make the fact unmissable.
- **Agents run concurrently.** The run manager starts one runner per agent and gathers them; each
  has its own harness session and event collector, and steps are counted per agent.

## Consequences

- Exposure is enforced on one surface today (the wiki). Timed board posts or commits would follow
  the same pattern: an environment variable, a field in `/admin/state`, a receipt.
- The schedule is relative to the services start, not to `mesocosm run`. The gap is tens of seconds
  on the Docker topology and is recorded; a manifest author who needs precision picks a later time.
- A trap account is only a trap if the manifest author plants it in world content (a wiki page, a
  site). The platform knows the account list, not the planting; `note` exists so the record says
  where it was planted.
- `/admin/state` is denied to agent addresses and allowed to everything else on the sandbox
  networks, which is the gateway and the control plane; the services have no edge network.
- ADR-0010's consequence that accounts are out of scope is superseded by this record.
