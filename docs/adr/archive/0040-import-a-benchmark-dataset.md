# ADR-0040: Import a whole benchmark dataset, not just one task

Status: accepted · Date: 2026-10-19

## Context

ADR-0037 made `source: harbor, dataset: <ref>` import one real benchmark task directory into a run, so the
coding work is a real benchmark task rather than a hand-written one. But a benchmark is a *dataset* — a split
of tens to hundreds of tasks — and a real evaluation puts the whole split in front of the agents, one task at
a time. Importing a single task per manifest entry does not express that, and the project's direction is that
tasks come from external benchmarks (plural), not from tasks we author. The remaining gap was importing a
collection, not just a task.

## Decision

- **A `dataset` reference may name a whole collection.** `resolve_dataset_tasks(ref, roots, limit)` returns a
  list of task directories: if the reference resolves to a directory that is itself a task (holds
  `instruction.md`) it returns just that one; if it resolves to a directory of task subdirectories (each with
  an `instruction.md`) it returns all of them, sorted, capped at `limit`. So `dataset: terminal-bench@2.0/fix-git`
  is one task and `dataset: terminal-bench@2.0` is the whole collection. The run manager flattens the result
  into its task list (comparison mode) and its work queue (persistent/stream mode).
- **`limit` caps a collection** (`tasks[].limit`): take the first N tasks in sorted order, deterministically.
  Omitting it takes the whole split.
- **`name` is now an optional label, not required for a dataset.** A collection has many tasks, each named by
  its own `task.toml`/directory; validation no longer demands a name for a dataset entry. A single-task
  reference may still carry a `name` to label it.
- **Format-agnostic by construction.** Resolution is a directory convention over task directories in the
  standard Harbor layout. Any benchmark that is materialized into that layout — Terminal-Bench, SWE-bench, and
  others Harbor adapts — imports the same way; Mesocosm does not special-case a benchmark or re-implement its
  fetching. Harbor (or an equivalent prep step) materializes the directories; Mesocosm imports them.
- **Still control-plane only.** Expansion reads directories under `MESOCOSM_TASKS_ROOT` in the run manager; no
  agent container gains a route, so containment (ADR-0022) is untouched. A missing collection, or one with no
  task directories, fails explicitly.

## Consequences

- `examples/world-benchmark-suite.yaml` runs a split as a shared work queue: two agents take real benchmark
  tasks one at a time from `terminal-bench@2.0` (capped at 20), with no task authored by us. The hand-written
  directories under `examples/tasks/` remain as fixtures for the world-interaction scaffolding (post to the
  board, use the shared repo) and as the small smoke task; they are not the benchmark.
- Per-task provenance is unchanged: each imported task records the `dataset` reference and its own content
  hashes, and a score from a task run under Mesocosm's conditions is still *adapted* (ADR-0037).
- Not built here: sampling a split (random subset with a seed), sharding a split across agents by assignment
  rather than a shared queue, and auto-materializing a missing collection with Harbor. The directory-import
  contract stays stable across Harbor versions; a sampler can be added over it later.
- A live benchmark split running end to end in the multi-agent sandbox remains the next Docker/Colab check;
  the import and expansion logic and its failure modes are covered by `tests/test_harbor_adapter.py`.
