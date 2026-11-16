# ADR-0028: The world's state is a verifiable object: clock, ledger, world.json, checkpoints

Status: accepted · Date: 2026-10-18

## Context

A Harbor verifier sees the agent's workspace (`/app`) and nothing else, so a task could only be graded
on files, while the behavior the platform studies happens on the board, in the chat, in the repositories
and in what agents hand each other. Verifiers also answered 0 or 1, which hides how far an agent got.
The worlds the platform compares itself with (TheAgentCompany's checkpoints, Vending-Bench's balance and
simulated calendar) grade world state and partial progress, and give agents a clock and money to
reason about. The world had neither.

## Decision

- **`/admin/state/world` exports the world as it stands**: every live board post with its body and
  replies, the wiki's pages (slug, visibility, hash, whether written at run time), the chat's messages
  and verdicts, the pastes, every repository's refs, the ledger and the clock, plus the personas and,
  when asked for an agent, a `viewer` naming the agent under verification. Control plane only; an agent
  gets the usual 404 and an `admin_denied` line.
- **The run manager puts it in front of the verifier.** Before `tests/test.sh` runs, `world.json` is
  written into the verifier's logs directory, mounted at `/logs`; the bundle records `verifier.world_state`
  with its size and hash. A verifier that reads the world is a plain shell script over JSON, like
  `examples/tasks/world-exchange/tests/test.sh`.
- **Checkpoints are partial credit.** A verifier may write `/logs/verifier/checkpoints.json`,
  `{name: true | false | number}`. `Reward.checkpoints` carries it and `verifier.reward` records it; with no
  `reward.txt` or `reward.json`, the reward is the checkpoints' mean (`source: checkpoints.json`). A
  reward file still wins when both exist.
- **`services.clock` is a simulated calendar.** The `clock` hostname answers with the manifest's `origin`
  advanced by the real seconds since the run start times `speed`; it shares the anchor the exposure
  schedule uses (`/admin/clock`, ADR-0018), so "published at 10:00" and "visible after 60 s" are the
  same instant. Its position is in `world.json` and `/admin/state`; it is not snapshot state (a restored
  world starts again at `origin`, a fork patches it to continue).
- **`services.ledger` is money that is real inside the world.** Members hold the manifest's `unit`
  (`initial` balances by agent id, applied once); `POST /api/transfer` moves it between members and is
  refused beyond the balance with 402 and a `transfer_rejected` line; `/api/balance`, `/api/balances`
  and `/api/history` read it; the control plane credits and debits from the `bank` through
  `/admin/ledger` (`ServicesAdmin.credit`). The ledger is a journal (`ledger.jsonl`) like the board,
  replayed at start, exported in `world.json` and carried by snapshots.

## Consequences

- Tasks can grade coordination: "the notes are on the board" is a checkpoint, not a hope. Partial scores
  make the Inspect export's accuracy a mean of fractions, which is what Inspect expects.
- `world.json` can be large on a long persistent world (every post body); it is written per task into the
  bundle's verifier directory, which the bundle already owns. A size cap is a future decision.
- A ledger makes resource conditions concrete: a task can cost, a reward can pay, and a transfer between
  agents is a recorded act the judge can look at. No detector reads it yet.
