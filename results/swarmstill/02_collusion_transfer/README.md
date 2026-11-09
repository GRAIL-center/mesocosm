# Collusion transfer: held-out guide tests and Qwen3-4B design rounds

Teacher behavior: peer-review collusion in the SALT-NLP *Emergent Collusion* release (Gemini 3.1 Flash-Lite).
Designers (Claude Opus 5.5, GPT-6.1 Sol, Qwen3.5-397B) wrote harness designs and frozen predictions; the guide
("framework") versions are in `../04_framework_guides/`. The full monitoring report with every number is
`REPORT.md` (final, Nov 8 18:20 UTC); `incidents.md` lists run incidents; `report_draft.md` is the earlier draft.

Headlines (from `REPORT.md`):

- **Test A (held-out teacher Qwen3.8-27B).** Relative MAE: no guide 44, guide v1 41, guide v2 20, "no change" 63;
  Spearman ρ 0.40 / 0.40 / 0.63. Only v2 designers predicted the two collapses.
- **Qwen3-4B rounds (17 harnesses, scored episodes 1–5).** Episode-1 both-ACCEPT: original 38%, round 1 47%,
  round 2 19%, round 3 11% (teacher 0%). Round 3 − round 1 = −37 points, CI [−65, −8]. The rise (episodes 4–5
  minus 1–2) moved +14, CI [−14, 42], far below the teacher's +36.
- **Test B (Gemma-4-12B on Colab)** is inconclusive (1–4 trajectories per intervention, unstable baseline).

Files:

- `figures/` figures 3–10 (framework probe, feedback transitions, language event study, signature, curves,
  held-out test A and B).
- `scores/` test A (`q27_score*.json`, K = 4/10), test B (`gemma_score.json`), round comparisons
  (`round_compare*.json`, K = 4/5), behavior signature (`signature*.json`), language statistics (`nlp_stats.json`),
  round-3 candidates, and the condition table (`conditions.json`).
- `tables/` signature tables. `harness_flags/` the exact harness settings of every designed condition.
- `prompts/` frozen prompts and sha256 files for test A (`q27/`), test B (`gemma/`, with the frozen profile), the
  distillation rounds (`distill_rounds/`) and the framework probe (`framework_probe/`, with its scores).
- `designer_answers/` raw final answers of the API designers for test A and the framework probe.
- `profiles/` the Qwen3-4B model profiles given to round-2/3 designers.
