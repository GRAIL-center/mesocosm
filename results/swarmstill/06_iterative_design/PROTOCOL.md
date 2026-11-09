# Iterative environment design study (frozen before any designer session)

Question. Can a frontier coding agent, testing its own designs on real runs, redesign a multi-agent environment so
that a small model's team reproduces a larger model's behavior signature?

Behavior. AgentCollabBench RTD (tracer durability), scored by the official code: deepest layer at which the root
prompt's tracer ID still appears verbatim, divided by graph depth. Official harness and scorer, unmodified; only the
task JSON changes.

Teacher. Qwen3-8B team in the original environment. Students: Qwen3-4B and Gemma-4-E4B-it teams. Designers: Claude
Opus 5.5 (Claude Code 2.1.317, headless) and GPT-6 Astra (Codex CLI). Four sessions, one per designer x student, run
two at a time (each designer runs one session at a time; sessions share nothing).

Tasks. Loop: 18 single-metric RTD tasks (6 branching trees, 6 converging DAGs, 6 custom graphs). Held-out: 17 other
RTD tasks (6, 6, 5; the custom-graph pool has only 11), never shown to designers. Both are drawn with seed 11 from the
official task files whose speaking order has at most 8 turns (pick_short.py).
Amendment (02:50 UTC, before any designer session and before any run on these sets): the first plan used the 20
generalization-study tasks, whose speaking orders reach 40 turns; one test took over 15 minutes, which made 20 tests
per session infeasible. The reference signatures were re-measured on the new sets (make_reference.py).

Design space (applied to every task): topology original | chain | full | root_broadcast; turns 0.5 | 1 | 1.5 | 2;
salience high | low; distractors all | half | none. Agent prompts, task text, model and scorer are locked.

Signature. Mean RTD (points, x100) in each topology group. Gap(env) = mean over the three groups of
|student signature(env) - teacher signature(original env)|. Alignment(env) = 100 x (1 - gap(env) / gap(original env)),
where the original-environment gap uses the student's own original-environment runs on the same task set. 0 = no
closer than the unchanged environment, 100 = the teacher's signature, negative = farther.

Budget and procedure. Each designer has 20 tests on the 18 loop tasks. Before each test it must submit a numeric
prediction of the student's signature. Each test returns the measured signature, alignment with a task-bootstrap 95%
interval, per-task scores and the prediction error. The designer finalizes one tested design; the operator then runs
that design once on the held-out tasks. The operator does not advise or intervene.

Reference runs (operator). Teacher and both students in the original environment on the held-out tasks (one run
each). Noise floor: the unchanged environment re-run on the loop tasks (students twice, teacher once); the alignment
of those re-runs against the first run's denominator shows how far alignment moves with no design change, and the
teacher's re-run gap to itself bounds the reachable alignment.

Designer setup. Each designer works in its own fresh directory holding the 20 loop task files, the benchmark source,
the spec-applying code, the test tool and a fixed prompt (build_ws.py); same prompt for both designers apart from
the student's name; no framework guide and no results of the earlier studies. Opus runs as a fresh Claude Code
subagent (no operator context; the host session's project instructions are loaded by the harness, disclosed as a
limitation). Astra runs under Codex CLI exec in a workspace-write sandbox with network off and no user config. A test
that fails on the platform is refunded. A session that ends without finalizing is finalized by the operator at its
best in-loop alignment test and reported as operator-finalized.

Outcomes (reported for every session, none dropped).
- Primary: held-out alignment of the finalized design.
- Secondary: best in-loop alignment; alignment by test index; prediction error by test index; final design's in-loop
  minus held-out alignment; tests used; how the design changed across tests.
- Integrity: every tool call and file access of each designer is logged and audited afterwards.
