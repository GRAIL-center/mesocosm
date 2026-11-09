# IDR-LOOP-1 results

Pre-registered in PROTOCOL.md (sha256 freeze before any run). Student: Qwen3-1.7B team (vLLM, thinking off).
Target: Qwen3-8B team in the original environment (teammate's scored runs). AgentCollabBench official harness and
judge (DeepSeek V4 Flash via Adaption). Loss = 100 x mean(|IDR-t|, |LOW-t|, |HIGH-t|). Figure: fig_idr_loop.png.

## Baselines
- Target (8B), loop: 0.206 / 0.000 / 0.250. Held-out: 0.104 / 0.000 / 0.081.
- Student, original environment, loop: 0.585 / 0.333 / 0.673, loss 37.8. Held-out: 0.412 / 0.375 / 0.378, loss 32.7.
  Both match the teammate's independent 1.7B runs (loop 0.610 / 0.333 / 0.692; held-out 0.376 / 0.375 / 0.378).

## Loop (12 tasks, one run per test)
Designer (Claude Opus 5.5, fresh subagent, 0 tool calls in every turn), loss by test:
1 chain/1/original 28.6 · 2 chain/0.5/original 45.5 · 3 chain/2/original 40.6 · 4 chain/1/none 9.6 ·
5 original/1/none 22.5 · 6 chain/1/none (repeat) 17.3 · 7 full/1/none 31.9. Final: chain/1/none.
Random (8 settings drawn before the study): 1 chain/2/none 8.6 · 2 rb/1.5/orig 35.6 · 3 full/1/orig 43.5 ·
4 rb/2/orig 47.1 · 5 rb/0.5/none 18.9 · 6 rb/2/none 29.8 · 7 rb/1/orig 33.6 · 8 full/0.5/none 28.7. Final: chain/2/none.
- Every setting that kept the escalating trigger scored 28.6–47.1; the settings without it scored 8.6–31.9.
- Random search hit its best setting at its first draw; the designer reached the same region at test 4.
- The same setting (chain/1/none) scored 9.6 and 17.3 on two runs of the same 12 tasks.
- Designer prediction error (mean abs., points) by test: 7.8, 30.0, 20.5, 8.2, 5.7, 5.0, 14.4.

## Held-out (8 tasks never shown to the designer; each final design run once)
| | IDR | LOW | HIGH | Loss |
|---|---|---|---|---|
| Target (8B) | 0.104 | 0.000 | 0.081 | 0 |
| Original environment | 0.412 | 0.375 | 0.378 | 32.7 |
| Random final (chain/2/none) | 0.475 | 0.375 | 0.405 | 35.7 |
| Designer final (chain/1/none) | 0.466 | 0.375 | 0.405 | 35.4 |

- H1 (designer < random): difference -0.3 points, 95% bootstrap CI [-9.6, 9.6]. Not supported.
- H2 (designer < original): difference +2.7 points, CI [-16.3, 24.8]. Not supported.

## Reading
On the tasks used for tuning, both arms cut the loss from 37.8 to about 9, almost entirely by removing the escalating
trigger. On held-out tasks neither final design was closer to the target than the original environment. The loop gain
is best explained by selecting the best of several noisy runs on the same 12 tasks (a repeat of the designer's best
setting lost 7.7 points) rather than by a setting that transfers. Designer and random search did not differ.
Limits: 8 held-out tasks, one run per design, one designer session, one student model; CIs are wide.

## Deviations and operations (full log: designer/ops_log.md)
- R4 and R5 were stopped before any task finished, discarded unread and rerun (to cap GPU concurrency).
- The two held-out runs were started before the arms finalized (user-approved, to save time) and kept sealed until
  both finals were declared; both finals matched the sealed designs.
- D7 was stopped by operator error at 3/12 tasks and rerun from scratch; the stopped run is not used.
- No test 8: the session was ended after test 7 for time; the designer then chose its final from tested designs.
- Designer isolation was by instruction only (no OS sandbox); tool use was 0 in every designer turn.
