# Condition experiment integration: Colab A100, 2026-10-21 UTC

This acceptance exercise checks system composition: real agents, an external task environment,
original verification, condition execution and separate outcome/incentive records. It does not
require successful task repair, cooperation or a target behavior.

Both trials returned `status: ok`, with three separate harness sessions, original verifier results
and incentive records per trial. **All original scores and allocated rewards were 0.0.** The
individual condition met the three-agent model/tool/verifier execution criterion. The team-mean
condition's first agent returned without calling a tool, so the predeclared stricter criterion
requiring every agent in both conditions to use a tool was **not met** (`criterion_met: false`).

| Condition | Agent | Successful model requests | Completed Bash calls | Original score | Allocated reward |
|---|---|---:|---:|---:|---:|
| individual | agent-1 | 3 | 2 | 0.0 | 0.0 |
| individual | agent-2 | 7 | 6 | 0.0 | 0.0 |
| individual | agent-3 | 3 | 2 | 0.0 | 0.0 |
| team_mean | agent-1 | 1 | 0 | 0.0 | 0.0 |
| team_mean | agent-2 | 4 | 3 | 0.0 | 0.0 |
| team_mean | agent-3 | 5 | 4 | 0.0 | 0.0 |

All 23 model requests returned HTTP 200 and were attributed by agent key. Seventeen Bash calls
completed; two returned exit code 128, so completion does not mean every command succeeded.
Each condition had three distinct containers and harness sessions, with overlapping gateway request
intervals across the three identities. Intervals include queueing/transport and do not measure
simultaneous GPU decoding. The conditions ran sequentially, not as six concurrent agents.

The individual run was `run_1792552739741_bb0eee` (893 events); team mean was
`run_1792553106979_046a19` (954 events). Both retained three separate task outcomes and three
incentive records, including reward-source evidence. Equal zero inputs exercise both allocation
paths but do not demonstrate different allocation outputs; that arithmetic has
[unit-test evidence](../tests/test_rewards.py). The task repository's diff remained empty.

Five agents used tools and produced controller-owned `native/<agent>/tools/raw_log.jsonl` journals.
The agent with no tool use had no tool journal. None of the six task workspaces contained an
injected `raw_log.jsonl`. The monitor was explicitly disabled, not recorded as having judged the run safe.
Both summaries recorded `learning.mode: none`, zero weight updates and no feedback events.

## Inputs and execution scope

- Live runtime commit: `7852762f51da28c4f268ca853b4aa5f5cef77a94`.
- Live runtime source digest: `6a4138ef528b77966924decb96c49e636a55c72d1b02335cc97b4a587f65e4c9`.
  The digest hashes sorted `src/**/*.py` as relative POSIX path, NUL, file bytes, NUL.
- External task: `harbor-framework/terminal-bench-2`, revision
  `2fd12b88aafdd04a52c298e3940bcb189f9766d6`, task `fix-git`.
- Model: `Qwen/Qwen3-4B-Instruct-2507`, revision
  `cdbee75f17c01a7cc42f958dc650907174af0554`. One shared replica serves three independent
  OpenCode processes, sessions, gateway identities and workspaces per condition.
- Hardware: NVIDIA A100-SXM4-40GB, driver 580.82.07. Python 3.12, vLLM 0.32.0,
  torch 2.13.0, eager serving, context length 32768 and maximum three sequences.
- Harness: released OpenCode 1.19.3; Docker 29.9.2. The original task image resolved to
  `sha256:b041e51b3fd55da33dc43648fcfb28f4887374a8676c77b0836923422e7fde29`.
- Conditions: `rewards.source: verifier`, `allocation: individual` versus `team_mean`,
  `learning.mode: none`, one trial per condition. `order: randomized`, `order_seed: 0` produced
  individual then team mean. The monitor is disabled.

Sampling warmup is an operator check and is excluded from agent measurements. The task, original
verifier and model are held fixed. There is no retry or prompt adjustment to obtain a desired score
or behavior. One trial per condition does not establish repeatability, a reward effect or statistical
validity. The scheduling seed controls only the order, not agent generation.

The operator ran the following from `/content/mesocosm` as `dock`, with
`MESOCOSM_TASKS_ROOT=/home/dock/datasets`, in RootlessKit's network namespace and a private mount
namespace whose resolver used `10.0.2.3`:

```bash
.venv/bin/mesocosm experiment run runs/conditions/conditions.yaml \
  --out runs/conditions/experiment --vllm-host 10.0.2.2 \
  --task-runtime native --verifier-network bridge --allow-unenforced-resources --json
```

The saved input uses the same base world with only `rewards.allocation` changed between conditions.
The evidence archive includes sanitized manifests, both bundles, the full experiment plan/report,
source/task hashes, actual gateway/harness/verifier records and operator interventions. Its SHA-256
is `562f465e7ea70d44840e2a94f1737ff871cc4237d4824415a3039e7a805b2cd8`.

## Environment and lifecycle boundaries

The native image adds the released harness and its runtime dependencies to the declared task image.
Only the original verifier has bridge internet for dependency installation. Agents use the configured
isolated network. CPU/RAM/storage enforcement is omitted under the explicit Colab override, and the
Colab runc wrapper removes PID isolation and exposes host `/proc` and `/sys`. These are the same
[native profile limitations](native-multiagent.md) as the earlier acceptance exercise.

After the first condition completed, `docker compose down` stalled. Terminating only the completed
condition's three OpenCode processes and gateway process from the host allowed the same experiment
to continue. Final teardown required the same intervention after the second condition's measurements
were complete. Both interventions are preserved separately from agent activity. The run does not
establish unattended condition transitions on Colab.

The subsequent lifecycle fix limits teardown to 120 seconds, terminates only the spawned Compose
client process group on timeout/cancellation, and records uncertain cleanup separately from completed
trial outcomes. It prevents subsequent trials from starting after cleanup failure. This fix has
[subprocess](../tests/test_stack.py), [experiment](../tests/test_experiment.py) and
[CLI](../tests/test_experiment_cli.py) regression tests; it was added after the above live source was
frozen and is not verified by this A100 run. It does not repair Colab's process-isolation limitations
or automatically kill container/host workloads.

A separate operator check rebuilt the changed services image and inspected `/data/wiki` in a real
container: the directory existed with zero entries. This establishes absence of implicit wiki pages
in that image, not model-driven use of the wiki API or a validation of explicit seed loading.
The inspected image was `sha256:e0b162eb6b344d384451e77a928e48c0e8439e1034dfb9ea95973f33f0ad986b`.

## What this does not establish

Peer-derived incentives, mixed allocation, next-task context feedback, new monitor execution,
weight-updating RL, production isolation and broad task compatibility need separate evidence.
The default fixture-free services image is checked independently; it is not injected into the
external task to manufacture behavior. Raw run bundles remain outside version control.
The paid Colab session was terminated after download; a subsequent inventory reported no active sessions.

Local validation after the lifecycle fixes: 348 tests passed (four expected legacy-scenario deprecation
warnings), Ruff passed, and targeted type checks passed for the experiment/reward/config/monitor/stack
modules. Full-project mypy still reports 34 errors across 17 files, including existing annotation issues
and missing optional dependency stubs. It is not a passing project-wide type check.
