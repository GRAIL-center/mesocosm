# ADR-0037: Real benchmark tasks imported by dataset reference

Status: accepted · Date: 2026-10-19

## Context

The architecture says the coding work is real: "Tasks come from executable benchmarks (Terminal-Bench,
SWE-bench) through Harbor." The spike confirmed the real path works — `harbor run -p terminal-bench@2.0/fix-git
-e docker` built the task image, ran the agent and wrote `reward.txt` (`docs/capability-matrix.md`, Harbor
rows). But nothing connected that to a Mesocosm run. `TaskSpec` already carried a `dataset` field and the
validator already accepted `dataset + name`, yet the run manager only ever loaded a task from a local `path`
and silently dropped any task without one. So every task an agent actually did was a hand-written directory
under `examples/tasks/`, and the real-benchmark promise was unfulfilled: the coding work was a stand-in, which
is exactly the kind of cheap substitute AGENTS.md says to replace with the real thing.

`dataset` references name a task in a benchmark registry (`terminal-bench@2.0/fix-git`). Harbor is what fetches
and builds such a task; the spike's standing conclusion (SPIKE.md, open question on Harbor's sandbox) was that
for isolated multi-agent runs the task is *imported into our sandbox* and the verifier runs afterwards, rather
than handing control to Harbor's own run loop.

## Decision

- **`source: harbor, dataset: <ref>` imports a materialized Harbor task directory into the run.**
  `adapters/harbor/task.resolve_dataset(ref, roots)` maps the reference to a relative path
  (`terminal-bench@2.0/fix-git` → `<root>/terminal-bench@2.0/fix-git/`) and returns the first root that holds
  an `instruction.md` there; the existing `load_task` reads it, since a real Terminal-Bench task is already in
  the Harbor layout (`instruction.md`, `task.toml`, `environment/`, `tests/test.sh`) the loader expects. The
  run manager loads `dataset` tasks exactly as it loads `path` tasks (`_load_spec_task`), so rounds, side
  tasks, staggering, verifiers, partial credit and provenance all apply unchanged. Provenance records the
  `dataset` reference alongside the content hashes.
- **Harbor materializes; Mesocosm imports.** Mesocosm does not re-implement fetching a benchmark. The operator
  materializes the task directory with Harbor (`harbor run -p <ref> ...`, or Harbor's task cache) and points
  `MESOCOSM_TASKS_ROOT` (a control-plane environment variable, `os.pathsep`-separated) at the directory that
  holds it; the manifest's own directory is always searched too. An unresolvable reference fails explicitly
  (`FileNotFoundError` naming the roots searched), never silently — there is no `approximated` task.
- **This runs on the control plane only.** Resolving and reading the task directory happens in the run manager,
  outside every agent container. No agent gains a new route; the containment invariant (ADR-0022) is
  untouched. The task's `environment/` and `tests/` are used exactly as a local task's are, so the verifier's
  network needs are the task's own (Terminal-Bench 2.0 verifiers fetch tools at verify time, which is why the
  verifier container may have egress — it runs after the agent, outside its reach; `docs/capability-matrix.md`,
  Harbor row on independent verification).
- **References are sanitized.** An absolute reference or one containing `..` is rejected, so a `dataset` value
  can only ever name a task *under* a configured root.

## Consequences

- A world can now put real benchmark coding tasks in front of its agents (`examples/world-benchmark.yaml`),
  while the world-interaction tasks that no benchmark ships (use the team board, the shared repo, the wiki)
  stay hand-authored but are rewritten to read like real work, not API recipes (follow-up). "Real work" and
  "real multi-agent world" are two different axes; this ADR covers the first.
- Validation stays a spec-shape check: it accepts `dataset + name` without touching the filesystem, because
  whether a benchmark has been materialized is a property of the machine, not the manifest. Resolution happens
  at run time and fails loudly if the task is absent.
- A score from an imported benchmark task run under Mesocosm's conditions (shared workspace, carried memory,
  altered budgets) is still an *adapted* result (`adaptation`, ADR unchanged); importing the task does not make
  the number an official benchmark result.
- Not built here: Mesocosm shelling out to Harbor to auto-materialize a missing task. That couples the run
  manager to a specific Harbor CLI across versions; importing from a prepared root is the stable contract.
  A live Terminal-Bench task running inside the multi-agent sandbox is the next Docker/Colab check; the import
  mechanism and its failure modes are covered by `tests/test_harbor_adapter.py`.
