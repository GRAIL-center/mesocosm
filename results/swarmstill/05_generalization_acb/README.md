# Generalization study (AgentCollabBench), pre-registered

Does the general guide (v4) improve designers' predictions of how environment changes move small-model multi-agent
behavior, on behaviors other than collusion? Four AgentCollabBench metrics (IDR instruction decay, RTD tracer
durability, CPR consensus pollution, CLC cross-task leakage), five small models, three changes per metric (chain,
full connectivity, one metric-specific change), 20 tasks per cell. Designers: three sessions per arm and metric,
with the guide (F) or without (N). Code: `studies/swarmstill_general/` (runner, scorer, analysis). The primary test
was pre-registered in `acb_analyze.py` and frozen by sha256 (mesocosm commit 0f438d2) before scoring.

Result (`generalization_analysis.json`, `figure12_generalization.png`):

- **Primary: not supported.** D = e_F − e_N over 60 cells = +0.38 points, 95% hierarchical-bootstrap interval
  [−0.41, 1.18], one-sided sign-flip p = 0.91. F better in 12 cells, N in 14 (sign test p = 0.85).
- **Per metric** (Holm-adjusted p all 1.00): IDR +1.76, RTD −0.50, CPR +0.20, CLC +0.04.
- **Against "no change".** Mean error: no guide 9.7, guide 10.1, answering "no change" 8.0. 50 of the 60 measured
  effects were indistinguishable from zero.
- **Shared miss.** Full connectivity lowered RTD in all five models (by 6 to 35 points); all 30 predictions for it,
  in both arms, said it would rise (by 20 points on average).

Files: `final/merged.jsonl` (per-task scores for every metric × model × condition; the input to the analysis) and
the parts it was merged from (`scores*.jsonl`); `final/analysis.json`; `answers/<arm>-<metric>-<k>/answer.txt`
(designer predictions); `prompts/` (designer prompts and model profiles); `freeze.sha256` and
`freeze_prompts.sha256` (hashes of the prompts and answers, verified before scoring); `conditions/manifest.json`
(the condition task files are regenerated with `acb_conditions.py` from the benchmark); `run_designers.sh`,
`run_all.sh`, `serve_models.sh` (how designers and models were run).
