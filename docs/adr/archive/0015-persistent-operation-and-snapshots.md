# ADR-0015: Persistent operation is a shared work queue; snapshots copy what the platform owns and declare the rest

Status: accepted · Date: 2026-10-18

## Context

M6 asks for a task stream, state carried across runs, snapshots with declared coverage, and forks
into comparison runs. Comparison mode gives every agent the same fixed task list; operation means
tasks keep arriving and the team shares them. A snapshot of a running sandbox can only contain what
the control plane can reach: the bind-mounted workspaces, the services' data, the shared volume and
the bundle; not the processes inside the containers.

## Decision

- **Persistent mode is one shared queue.** `mode: persistent` queues the manifest's tasks, then every
  task directory that appears in `stream.dir`, polled every `poll_seconds`. Each task goes to the
  first idle agent (`task.assigned`); the stream closes at `max_tasks`, `max_minutes` or a stop
  request, and an agent ends after `idle_stop_seconds` without work. The per-task body (memory policy,
  protected files, harness turn, verifier, state check) is the same code as comparison mode.
- **A snapshot captures and declares.** `mesocosm snapshot create` copies each agent's workspace, the
  services' board, pastes and bare repositories, the shared directory, the queue state (loaded,
  assigned, finished, pending) and the manifest, with a sha256 per file, and writes `coverage.json`
  listing what was captured and what was not: harness state inside the agent containers, requests in
  flight, gateway counters, container filesystems outside those paths. Take it after `mesocosm pause`.
- **Restore is verified.** `snapshot restore` empties and refills the workspaces, copies the services
  data back and restarts the services container, refills the shared directory, then hashes what
  came back; mismatches are listed and the result is not `ok`.
- **A fork is a new run with lineage.** `mesocosm fork <snapshot> --edit patch.yaml` merges the patch
  into the snapshot's manifest, brings the stack up, restores, and runs. The first task boundary keeps
  the restored workspace whatever the memory policy says (the receipt names the snapshot); the new
  bundle carries `lineage.json` (parent run, snapshot, patch) and a `run.restored` event with the
  restore verification.

## Consequences

- Agents in a forked run start from the restored files and world data but with fresh harness
  sessions; what the harness remembered internally is gone, and the coverage declaration says so.
- Compose projects are named after the manifest; a fork is a new project, so the parent stack must be
  down first (the CLI and the chain runner do this).
- The queue is in-process; a crashed run manager loses the queue, and the snapshot's queue state is
  the record of what was pending.
