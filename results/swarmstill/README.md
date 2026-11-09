# SwarmStill results

SwarmStill asks whether a frontier coding agent can design, by iterating, an environment in which a team of small
models reproduces a target multi-agent behavior, with every model's weights left unchanged (B = F(M, P, S, E): the
designer changes S and E only). This directory collects the derived results of every SwarmStill study run so far,
with the prompts, pre-registrations, hashes and scripts that produced them.

| Folder | Study | Status | Headline |
|---|---|---|---|
| `01_harness_inference_pilot/` | Can designers infer the harness behind a released multi-agent behavior (Emergent Collusion, Gemini 3.1 Flash-Lite) from masked traces, and predict 12 released ablations? | done | Traces fix the incentive/feedback structure that priors get wrong; intervention predictions no better than "no change" |
| `02_collusion_transfer/` | Model-conditioned guide on held-out teachers (test A Qwen3.8-27B, test B Gemma-4-12B) and three rounds of designer-built harnesses for Qwen3-4B | done | Guide v2 relative MAE 20 vs 44 without guide (test A); designs fixed episode-1 behavior (−37 pts, CI [−65, −8]) but not the learned rise |
| `03_alignment_overlap/` | Trajectory alignment score and sequence overlap for the Qwen3-4B rounds | done (post hoc) | Alignment (K = 5) round 1 −60.4 → round 3 +3.7; round 3 − round 1 = +64.1, CI [22.7, 105.8], one-sided p = 0.024 |
| `04_framework_guides/` | The design guide, v1 to v4 | done | v4 is the frozen general form used in 05 |
| `05_generalization_acb/` | Pre-registered test: does guide v4 improve designers' predictions on four AgentCollabBench behaviors and five small models? | done | Not supported: D = +0.38 points [−0.41, 1.18], one-sided p = 0.91 |
| `06_iterative_design/` | Iterative design: Claude Opus 5.5 and GPT-6 Astra redesign an AgentCollabBench RTD environment over 20 real test runs so Qwen3-4B / Gemma-4-E4B teams match a Qwen3-8B team | running | Protocol and tooling only; results pending |
| `07_idr_loop/` | Pre-registered control on instruction decay: Claude Opus 5.5 designer vs random search (8 tests each) for a Qwen3-1.7B team against the Qwen3-8B target, judged on 8 held-out tasks | done | Loop loss 37.8 → about 9 for both arms, but no held-out gain: designer − random −0.3 [−9.6, 9.6], designer − original +2.7 [−16.3, 24.8] |
| `scripts/` | Analysis, scoring and figure scripts shared by 01–03 | | |

Each folder has its own README with the method, the files and the numbers as computed at the time.

## What is not committed, and where it lives

- **Raw runs and trajectories.** Small-model rollouts, agent conversations, AgentCollabBench traces and designer
  event logs stay on the run machines and in the operator's local scratch space (`pulled/`, `small_runs*/`,
  `op/runs`, `op/distill/*/events.jsonl`, `gen/backup`). The repository rule is that run data is never committed
  (`AGENTS.md`, `.gitignore`). Exception, by maintainer decision: `07_idr_loop/raw/` and `07_idr_loop/designer/`.
- **Third-party data.** Copies of the Emergent Collusion release (parquet tables, masked trajectories) and of the
  AgentCollabBench task files are not redistributed; scripts and manifests here regenerate them from the originals.
- **Operational files.** Monitoring ticks, pod health files, pod setup and polling scripts, credentials and host
  addresses are excluded. Pod hosts and ports in the iterative-design tooling are replaced with `<POD_HOST>` and
  `<POD_PORT>`.
- **No activations** were captured in these studies.
