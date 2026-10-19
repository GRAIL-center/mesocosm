# Native three-agent acceptance: Colab A100, 2026-10-19

Run `run_1792440611816_34cdbe` met the execution criterion: three actual OpenCode servers
completed model calls, shell tools and the unchanged external task verifier in separate native task
containers. The run status was `ok`; **all three task rewards were 0.0**. This validates the execution
path, not successful Git repair, agent cooperation, or benchmark performance.

| Agent | Successful model requests | Completed Bash calls | Original verifier reward |
|---|---:|---:|---:|
| agent-1 | 4 | 3 | 0.0 |
| agent-2 | 4 | 3 | 0.0 |
| agent-3 | 3 | 2 | 0.0 |

All 11 requests returned HTTP 200 and were identified by an agent key. Container IDs and harness
session IDs were distinct. Gateway request intervals overlapped across all three identities; these
intervals include queueing and transport and do not measure simultaneous GPU decoding. The eight
completed tools ran real `git status` / `git log` commands with exit code zero. The agents stopped
without recovering the missing changes. Each original verifier ran its two tests, both failed, and
wrote `reward.txt` containing `0`. Agents received the original task instruction; no solution or
expected score was supplied.
There were 725 collected events. No service exchange or cooperation was observed.

## Inputs and evidence identity

- Mesocosm base: `613d5fcbf82af87654ea3f62b746121860a704a2`, plus this change.
- Runtime source digest: `3d4654fab8d94cc321768c1388f85313084a58f4d7898fb9009bc229075fe5bc`.
  This hashes sorted `src/**/*.py` as relative POSIX path, NUL, file bytes, NUL. The downloaded
  evidence and final local runtime source matched.
- External task: `harbor-framework/terminal-bench-2`, commit
  `2fd12b88aafdd04a52c298e3940bcb189f9766d6`, task `fix-git`. Its Git diff was empty after the run.
- Original image: `alexgshaw/fix-git:20251031`, resolved base image ID
  `sha256:b041e51b3fd55da33dc43648fcfb28f4887374a8676c77b0836923422e7fde29`.
  Native working directory: `/app/personal-site`; native user: `root`.
- Model: `Qwen/Qwen3-4B-Instruct-2507`, revision
  `cdbee75f17c01a7cc42f958dc650907174af0554`; one replica, three independent agent contexts.
- Hardware: NVIDIA A100-SXM4-40GB, driver 580.82.07. Serving: Python 3.12,
  vLLM 0.32.0, torch 2.13.0+cu130, eager mode, context 32768, maximum three sequences.
- Harness: published `opencode-ai@1.19.3`; rootless Docker 29.9.1.

The preserved bundle contains `manifest.json` with credentials redacted, `events.jsonl`,
`receipts.jsonl`, native gateway requests/responses, native agent messages, and each agent's
`verifier.log`, `environment.json`, CTRF results and original reward file. Raw run data remains
outside version control. `verification.json` records the above counts, individual IDs, task file
hashes and source digest; `operator-warmup.json` is explicitly separate from agent evidence.

## Adaptations and remaining scope

This is an adapted benchmark run. The native image receives the released harness, Node 22,
curl/CA/libstdc++ packages, workspace bind mounts and a shared volume. The harness layer is not a
byte-identical task environment and can replace a task's `/usr/local/bin/node`; tasks requiring that
exact Node executable need a separate compatibility check. The manifest disables authored world
services and agent internet. Only
the verifier receives bridge internet to install its original dependencies. CPU/RAM limits and
storage quotas could not be enforced on this Colab host; the explicit override and receipts record
that omission. Colab's special runc wrapper removes the PID namespace and exposes host `/proc` and
`/sys`; this run establishes no production process-isolation claim. The previous interrupted
attempt also required host-side termination of three orphaned harness processes before recreation.

The first supported native profile is Debian/Ubuntu, single-container, filesystem-based tasks.
Task Compose stacks, task GPUs, separate verifier images, live-process verification and harnesses
other than OpenCode remain unsupported. Prompt-token budgets are checked at response boundaries.
The shared volume permits communication; this task did not demonstrate its use by agents.

## Failures corrected before acceptance

1. `run_1792440130137_2b6373` failed before inference because the harness layer guessed the npm
   executable filename. The layer now preserves npm's complete installation prefix and official
   executable link, and checks `opencode --version` during the build. The wrapper source enters
   the native image key.
2. `run_1792440357095_807c58` was interrupted after vLLM's lazy sampling compilation could not
   find `ninja`. Activating the serving virtual environment fixed PATH. Three genuine sampled
   operator warmup requests then succeeded before the accepted run; they are excluded from the
   11 agent requests.

Local validation: 259 tests passed; Ruff passed. Full mypy still reports 33 errors across 16 files,
including missing optional dependencies/stubs and existing annotation problems; it is not a clean
type-check result. The paid Colab session was terminated after the evidence was downloaded.

Reproduction and deployment caveats: [native multi-agent runbook](native-multiagent.md).
