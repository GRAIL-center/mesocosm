# IDR-LOOP-1: iterative design vs random search, with held-out tasks (instruction decay)

Pre-registered control for the iterative-design study: can a frontier designer, testing its own designs on real
runs, change a small team's environment (S/E only) so that it reproduces a larger team's AgentCollabBench
instruction-decay (IDR) signature, does it beat random search with the same budget, and does the result hold on
tasks the designer never saw? Student Qwen3-1.7B team; target Qwen3-8B team in the original environment (the
teammate's scored runs in `05_generalization_acb`); designer Claude Opus 5.5; run 2026-11-09 on a Colab L4.

## Result

Both arms cut the loss on the 12 loop tasks from 37.8 to about 9 points, almost entirely by removing the escalating
trigger. On the 8 held-out tasks neither final design was closer to the target than the original environment
(designer 35.4, random 35.7, original 32.7). Neither pre-registered hypothesis was supported:

- H1 designer < random: −0.3 points, 95% bootstrap CI [−9.6, 9.6].
- H2 designer < original: +2.7 points, 95% bootstrap CI [−16.3, 24.8].

Why it did not transfer (`FAILURE_ANALYSIS.md`): the setting fixed escalation-triggered cascades, which dominated
the loop tasks by chance of the split (9 of 12 loop tasks were escalation-sensitive, 1 of 8 held-out tasks), and
selection on noisy runs inflated the loop numbers (a repeat of the designer's best setting scored 17.3 instead of
9.6). No setting changed the main model difference: the 1.7B team gave in to the first, mild request 36–38% of the
time on both splits, against 0% for the 8B team. Full numbers: `RESULTS.md`; figure: `fig_idr_loop.png`.

Not pooled with `06_iterative_design`: different student (1.7B vs 4B), budget (8 vs 5 or 20 tests) and pressure
lever (trigger text replaced vs messages dropped).

## Files

| File | Content |
|---|---|
| `PROTOCOL.md`, `freeze.sha256` | Pre-registration, frozen before any run (`sha256sum -c freeze.sha256` passes; one non-hash line) |
| `split.json`, `teacher_reference.json`, `random_specs.json` | Loop/held-out split by sha256, target signatures, the random arm's 8 settings |
| `idr_eval.py` | Applies a spec to the official task JSON and runs the official AgentCollabBench harness and IDR scorer |
| `judge_proxy.py` | Local OpenAI-compatible endpoint forwarding the judge (DeepSeek V4 Flash) to the Adaption API; key from the environment, never logged |
| `prepare.py`, `extract_tasks.py` | Operator setup (task split, teacher reference) |
| `analyze.py` | Pre-registered held-out analysis (bootstrap over tasks) → `analysis.json` |
| `make_fig.py` | `fig_idr_loop.png` from `results_loop.json` and `analysis.json` |
| `RESULTS.md`, `FAILURE_ANALYSIS.md` | Results and the failure analysis |
| `results_loop.json` | Signature and loss of every loop test, with the designer's predictions |
| `per_task_turns_with_teammate.json` | Every run's per-task score and judged-turn string (`V`/`.` = violated/held, `l`/`h` = low/high pressure), plus the teammate's q17 and q8 baselines |
| `designer_brief.md`, `designer/log.jsonl` | The designer's brief; its designs, predictions, rationales, results and final choice |
| `designer/designer_transcript.jsonl` | The designer subagent's full transcript |
| `designer/ops_log.md` | Operations log: every deviation, stop and rerun, with times |
| `raw/idr-loop-runs-20261109.tar.gz` | All raw runs (see below) |

## Raw runs (committed by maintainer decision)

The repository rule is that run data is not committed (`AGENTS.md`, `.gitignore`, and the section "What is not
committed" in `../README.md`). For this study the maintainer asked for the raw logs to be included, so
`raw/idr-loop-runs-20261109.tar.gz` (20,007,255 bytes, sha256
`8ea8e90a0563113f6dcf9c1a274b36a4c2093371a05b105806ae191034bb9f75`) holds the Colab `/content/idr` directory:

- `runs/<run>/TASK-*.run.json`: the transformed task and the full harness output for every task and run (each agent's
  messages, the injected pressure messages, the final output); `runs/<run>/TASK-*.json` and `summary.json`: scores and
  judged turns. Runs: `orig_loop`, `orig_heldout`, `D1`–`D7`, `R1`–`R8`.
- `sealed/`: the two held-out runs of the final designs; `aborted/D7_stopped`: the test-7 run stopped by operator
  error (not used); `smoke/`: the one-task smoke run.
- `specs/`, `tasks/` (the 20 AgentCollabBench IDR task files used), the scripts, `vllm_q17.log`, `judge_proxy.log`.

The judge's free-text reasons were not saved (only its per-turn verdicts). The task files and the task text inside
the run records come from AgentCollabBench (github.com/aritra741/AgentCollabBench, MIT License).

## Reproduce

1. Install AgentCollabBench (commit f016f60) and serve `Qwen/Qwen3-1.7B` with vLLM as `q17`
   (`--max-model-len 32768 --default-chat-template-kwargs '{"enable_thinking": false}'`).
2. `ADAPTION_API_KEY=... python3 judge_proxy.py 8090`.
3. `python3 idr_eval.py <spec.json> <tasks_dir> q17 http://127.0.0.1:8000/v1 deepseek-v4-flash http://127.0.0.1:8090/v1 <out_dir>`
   for each spec (`{}` = original environment).
4. `python3 analyze.py runs teacher_reference.json final.json analysis.json`, with `final.json` naming the runs
   (as used: `{"D": "../sealed/H_D_chain_1_none", "R": "../sealed/H_R1", "orig_loop": "orig_loop",
   "orig_heldout": "orig_heldout", "loop_runs": {"D": ["D1", …, "D7"], "R": ["R1", …, "R8"]}}`).
