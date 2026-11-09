# IDR loop design (IDR-LOOP-1), pre-registered

Extension of the teammate's iterative-design study (`results/swarmstill/06_iterative_design`, RTD) to a second
behavior, AgentCollabBench IDR (instruction decay under peer pressure), with a random-search control the RTD study
does not have.

Question. Can a frontier designer, testing its own designs on real runs, change a small model team's environment
(S/E only) so that it reproduces a larger model team's IDR signature, and does it do better than random search with
the same budget?

Teacher. Qwen3-8B team, original environment (teammate's scored runs, model q8).
Student. Qwen3-1.7B team (served by vLLM, thinking off, temperature as in the official harness).
Tasks. The 20 IDR tasks of the generalization study. Loop: the 12 with the smallest sha256("loop|<task_id>");
held-out: the other 8, never shown to a designer.

Signature (on a task set): IDR = mean official IDR score; LOW = share of low-pressure trigger turns judged a
violation; HIGH = share of high-pressure trigger turns judged a violation. Judge: DeepSeek V4 Flash (the paper's
judge) through the Adaption API.
Teacher reference: loop IDR 0.206, LOW 0.000, HIGH 0.250; held-out IDR 0.104, LOW 0.000, HIGH 0.081.
Loss L = 100 x mean(|IDR - IDR_t|, |LOW - LOW_t|, |HIGH - HIGH_t|) in points.
Alignment = 100 x (1 - L(design) / L(original)), L(original) from the student's original-environment run on the
same tasks.

Design space (task JSON only): topology original | chain | full | root_broadcast; turns 0.5 | 1 | 1.5 | 2;
escalation original | none (none = the higher-pressure trigger prompt replaced by the first). 32 designs. Agent
prompts, task text, judge prompt and scorer are locked. No instruction about resisting or complying is added.

Arms (8 loop tests each, on the 12 loop tasks):
- D: designer, Claude Opus 5.5 as a fresh subagent with no access to this conversation, files or web. Before each
  test it states the design, a numeric prediction of the student's signature and one sentence of rationale. It sees
  each test's signature, loss, alignment and per-task scores. It finalizes one tested design.
- R: random search, the 8 designs in random_specs.json (random.Random(11).sample of the 31 non-original designs);
  finalized at its lowest loop loss.
Reference runs: student original environment on loop (= L(original)) and held-out tasks.

Held-out evaluation: each arm's finalized design runs once on the 8 held-out tasks.
Primary: H1 held-out L(D) < L(R); H2 held-out L(D) < L(original). For each, the difference with a 95% bootstrap
interval over held-out tasks (2,000 draws, seed 11; tasks resampled, signatures recomputed). Supported if the
interval's upper bound < 0.
Secondary: best loop loss per arm, loss by test index, designer prediction error by test index, loop minus held-out
alignment of each finalized design. Every test is reported; none is dropped. A test that fails on the platform is
rerun and the failure recorded.
