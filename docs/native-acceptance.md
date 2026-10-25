# Native integration acceptance

This is a validation procedure, not a scripted incident or an agent instruction. It tests whether
Mesocosm runs and records an unchanged external task correctly. Solving the task, cooperating and
producing a particular report are separate observations. Current Linux worker acceptance remains
**unverified** until the evidence below exists.

## Fixed inputs

Use the existing [three-agent task](native-multiagent.md), Terminal-Bench 2 `fix-git` at
`2fd12b88aafdd04a52c298e3940bcb189f9766d6`. Preserve all task files, including `task.toml`,
`instruction.md`, `environment/`, `tests/` and the operator-only `solution/`. Keep file modes and
SHA-256 hashes before packaging, after extraction and after execution. Record the actual pulled
image digest; `alexgshaw/fix-git:20251031` alone is not an immutable image identity.

Before inference starts, retain a private plan with the following settings:

| Setting | Baseline |
|---|---|
| Execution | Research profile, comparison mode, `containment: required` |
| Agents | Three unchanged OpenCode 1.19.3 processes, separate sessions, keys and workspaces |
| Task deadlines | Original 900-second agent and verifier limits; no completion-driven extensions |
| Model | Pinned model revision, server version, quantization, compute dtype and served configuration |
| Model sharing | One replica with three independent contexts; record actual serving capacity |
| Budgets | Fixed request/token limits in the manifest before the first agent request |
| Communication | Existing real `/shared` volume; no supplied dialogue or collaboration instructions |
| Internet | Agent access only through the configured web gateway; snapshot verifier bridge access explicit |
| Adaptations | Added harness, network policy, shared volume and unsupported resource limits recorded |
| Learning / monitoring | Disabled for the integration baseline; this run does not validate RL or intervention |
| Retries | No replacement run because agents failed or timed out; retain failed attempts |

Do not change a model, task, budget or prompt after seeing an outcome and present the result as the
same trial. Infrastructure corrections require a new recorded attempt and a new source fingerprint.

## Verify the execution host before allocating a GPU

The worker and local Docker daemon must share a Linux kernel and visible host process information.
Confirm Docker Compose, private PID namespaces, actual descendant termination, available CPU/RAM
controls, disk capacity and gateway reachability. The runtime's per-task containment and egress
receipts remain mandatory even when worker capabilities report available prerequisites.

The historical Colab runc wrapper removes PID isolation and cannot pass this gate. Colab may serve
the model for a separate Linux Docker worker through an authenticated private connection. A model
endpoint being reachable does not verify the execution environment. Do not allocate paid inference
while this prerequisite is unresolved.

## Calibrate the original verifier without agents

Run the original reference solution only in a separate operator diagnostic:

```bash
uv run mesocosm env calibrate /absolute/path/to/terminal-bench-2/fix-git \
  --out runs/fix-git-calibration --expected-score 1 \
  --verifier-network bridge --allow-unenforced-resources --json
```

The resource opt-in is necessary because the task declares `storage_mb: 10240`, which the runtime
cannot enforce. It must remain visible as an adaptation. CPU/RAM limits still apply when the host
supports them. Neither option permits shared-PID containment.

Inspect both fresh-environment stages: untouched baseline, then original reference solution. Check
the actual verifier log and CTRF report, not just `reward.txt`: this task's `test.sh` writes zero when
pytest or dependency setup fails too. Require both original tests to have run and distinguish failed
assertions from collection/import/infrastructure failures. Reference calibration makes no model calls
and its solution must never enter an agent prompt or workspace.

The original verifier compares the contents of two files; it does not fully validate Git history or
the requested merge. Its expected files under `/app/resources/patch_files` are also present in the
original agent image. Keep the verifier unchanged, record those files' pre/post hashes and inspect
Git history separately. A reward of one alone cannot establish faithful task completion or an
untampered oracle. Missing integrity evidence remains unknown.

## Run one world and inspect its trajectory

Start the [worker](execution.md) with the explicit native policy:

```bash
uv run mesocosm worker serve \
  --repo-root "$PWD" --root "$HOME/.local/share/mesocosm-worker" \
  --verifier-network bridge --allow-unenforced-resources
```

Configure the authenticated model route and fixed three-agent manifest before submission. Save the
package and receipt; close the submitting client after acceptance and reconnect by job ID. Do not
resubmit under a new ID to check whether the first submission succeeded.

For each agent, inspect the original evidence rather than only a final answer:

1. Match the initial task message to `instruction.md` and retain the harness/model configuration.
2. Reconcile admitted requests, completed and interrupted streams, usage, tool inputs/results,
   permissions, context handling and terminal native messages. An interrupted stream has unknown
   usage where measurement is absent.
3. Verify actual tools ran inside the declared task environment, and inspect filesystem/Git changes.
   Check whether tool results reached later model requests without silent loss.
4. Retain original verifier logs, reports and scores only when grading actually occurred. A deadline,
   quota refusal or incomplete harness turn must not acquire an invented zero or success score.
5. Inspect containment, egress and resource receipts alongside the trajectory. A shared volume's
   existence proves availability; observed reads/writes are needed to claim communication.

Report platform execution, task completion, verifier integrity, agent interaction and evidence
coverage separately. Noncompletion does not by itself diagnose a system bug. It also does not prove
the full normal-completion/verifier path: retain that part as unverified when it was not exercised.

## Verify lifecycle and preserve the result

After the baseline, check worker-owned containers, processes, volumes and networks are absent and
verify the fetched result archive. Retain raw bundles privately with a separately stored checksum.
The worker's `failed_cleanup` state is a failure, even when a task score exists.

Explicit stop and worker-restart recovery require separate, predeclared lifecycle probes and job IDs.
Keep those intentionally interrupted records separate from the baseline. Check that no job is
replayed, the restart removes only owned resources, and new execution remains blocked after an
unverified cleanup. Local lifecycle tests are useful preparation but do not replace these live checks.

This single-container task provides no evidence for shared database/application sidecars, learned
behavior or monitor effectiveness. Those require subsequent native tasks and separately fixed plans.

## Accept new interaction conditions separately

The [variable manual](experimental-variables.md) describes controls with automated contract coverage
whose new real-agent paths remain unverified. Plan their acceptance after the baseline, using at
least three actual agents, unchanged external tasks and predeclared repetitions. Keep failed and
missing observations; do not require an incident, cooperation or task success as the acceptance target.

- For bounded history and delayed feedback, use enough original tasks to reach the delivery boundary.
  Match replay hashes to native messages and check exact subsequent model inputs, fresh sessions and
  omitted fields. Shared-service history can persist independently.
- For compositional incentives, recompute returns from original verifier outputs, authenticated
  verdicts and attributed completed model calls. Keep missing components unknown. A later RL check
  needs its own validated policy-bound rollouts and checkpoint evidence.
- For rematching and reputation, reconcile the seeded assignments with the service journal and
  submitted votes. Every exposed history row must originate in an earlier measured episode.
- For selective dispatch, use a population larger than the selected cohort, with at least three
  participants in each accepted multi-agent wave. Verify calibration, actual task starts, ranking
  inputs and skipped identities; do not invent outcomes for unselected agents.
- For artifact exchange, inspect the acknowledged pause, captured bytes and original verifier inputs,
  publication eligibility, metadata and actual read records. A read establishes exposure only; inspect
  later actions separately before claiming adoption. Shared-service exchange remains unsupported.

Change one mechanism at a time unless an interaction is the declared question. Record which specific
paths were exercised; a successful baseline does not validate the whole condition matrix.
