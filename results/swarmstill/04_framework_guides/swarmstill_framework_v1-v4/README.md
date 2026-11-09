# SwarmStill design guides, v1 to v4

The model-conditioned design guide ("framework") that designer agents received, in the order it was revised.

| File | What changed | Where it was used |
|---|---|---|
| v1_model_conditioned_distillation.md | First guide: describe the target as a signature (rate, shape, pathway), profile the target model, map the pathway onto what the target can do, iterate on evidence | Framework test 1 (arm A) and held-out test A (arm V1). Extracted verbatim from the frozen Test A prompt (prompt_V1.md, sha256 fc60aa31…) |
| v2_model_conditioned_distillation.md | Rewritten from the measured failures: the behavior needs a rationale, corrective feedback and memory together; models reach it by different pathways; probe every lever on the target model | Held-out test A (arm V2) and round-2 small-model designs. Matches the guide section of the frozen Test A prompt (prompt_V2.md) |
| v3_model_conditioned_distillation.md | Adds the capability floor: corrective feedback only works when peers are usually right | Round-3 small-model designs |
| v4_general_framework.md | v3 with everything specific to peer-review collusion removed | Generalization study (AgentCollabBench), arm F. Frozen before any data: sha256 1c2907e4135c13419ccbf77bf280f879891d112054751e874273c6bcaa9b0732, mesocosm commit 0c4ba39 |

v1 and v2 were never stored as separate files before; v2 and v3 are not yet committed to the repository.
