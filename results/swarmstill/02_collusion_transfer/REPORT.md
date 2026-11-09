# SwarmStill: final monitoring report (Nov 8, 2026; data as of 18:20 UTC)

## Headline results

### 1. Guide v2 transfers to a held-out teacher (test A, Qwen3.8-27B)

The guide was written from Gemini data only. Predictions were frozen before any run, then scored on 10 scored
episodes, with 3 paired trajectories in each of 7 interventions.

| Arm | Sessions | Relative MAE | Spearman ρ | Direction accuracy |
|---|---|---|---|---|
| No guide | 5 | 44 | 0.40 | 0.57 |
| Guide v1 | 5 | 41 | 0.40 | 0.60 |
| **Guide v2** | 4 | **20** | **0.63** | 0.57 |
| Answer "no change" | – | 63 | – | 0.14 |

- **Where the gain comes from.** Only v2 designers predicted the two collapses: paying every ACCEPT (1–3% predicted,
  0% measured) and removing the verdict review (5–15% predicted, 2.5% measured).
- **Both designer families improve with v2.** GPT-6.1 Sol: 46 → 21. Qwen3.5-397B: 41 → 18.
- **What every arm missed.** Separate rewards: 45–80% predicted, 0% measured.
- **Files.** `q27_score.json`, `figure8_heldout_qwen27b.png`, `figure10_heldout_qwen27b_items.png`. The 4-episode
  interim with the same ordering is in `q27_score_K4.json`.

### 2. Designers can fix the start but not the learned rise (Qwen3-4B, 3 rounds, 17 harnesses)

Pooled trajectories over scored episodes 1–5 (`round_compare_K5.json`; 95% bootstrap CIs):

| Group | n | Episode-1 both ACCEPT | Rise (episodes 4–5 minus 1–2) | Overall |
|---|---|---|---|---|
| Teacher (Gemini 3.1 Flash-Lite) | – | 0% | +36 | 17% |
| Qwen3-4B, original settings | 8 | 38% | −19 | 23% |
| Round 1 (no profile, no guide) | 17 | 47% | −6 | 24% |
| Round 2 (profile + guide v2) | 27 | 19% | +6 | 15% |
| Round 3 (guide v3 + measured table) | 19 | 11% | +8 | 11% |

- **Episode 1 improved and the difference is real.** Round 3 against round 1: −37 points, CI [−65, −8]. Round 2
  against round 1: −29 points, CI [−57, −1].
- **The rise moved the right way but not reliably.** Round 3 against round 1: +14, CI [−14, 42]. It stays far below
  the teacher's +36.
- **Closest single harness.** r3b_boundedsummary: 0, 17, 0, 20, 20, 20, 20, 50% over 8 episodes, slope +5.1,
  distance 85, the lowest of all 17 harnesses. It uses a summary-allowed warm-up, memory of the last episode,
  separate rewards and temperature 0.1. Its raw-log sibling rose early and then fell back to 0.
- **Why the rise does not transfer.** The verdict review pushes toward ACCEPT only when most peer answers are correct.
  Own-task accuracy, which equals the peer-correct rate, is 87% for the teacher and 41–48% in every Qwen3-4B round.
- **Round 3 did not move the targeted variable.** It aimed to raise accuracy with deterministic decoding, but
  accuracy stayed at 43%, so the corrective push stayed absent. Guide v3 (`designer/framework/…_v3.md`) states this
  condition.
- **Implication.** A faithful reproduction on a 4B target needs easier tasks or a stronger small model. Neither is
  an allowed control here.

### 3. Held-out test B (Gemma-4-12B on Colab) is inconclusive

- **Coverage.** All 7 interventions at K = 4, but only 1–4 trajectories each.
- **Result.** Guide v2 had the lowest relative error (39, against 65 for no guide and 70 for v1) and the best
  direction accuracy (0.67). The "no change" answer scores 45 and 0.57, so v2 is only marginally ahead of it.
- **Unstable baseline.** The frozen profile said 25%, from 3 trajectories in 8192-cap runs. The later main rate is
  88%.
- **Tunnel limits.** The free tunnel's 100 s cutoff forced output caps (2048 and 768 tokens) and relaunches.

## What ran

| Pod / service | What it ran |
|---|---|
| Pod 2 (Claude-created, A100 SXM) | Round 1, round 2 seq01–04, round-3 replicates seq05–06 |
| Pod 1 (user's, A100 PCIe) | Qwen3-4B reference; round-2 replicates seq05–06; round 3 seq01–04; Qwen3.8-27B through Adaption; the Gemma runner |
| Colab A100 80GB (session `swarm-gemma`) | Gemma-4-12B-it server behind a Cloudflare quick tunnel |

- Round 3 used 2 Codex designers × 2 candidates (`op/r3_candidates.json`).
- Every stop, restart and diagnosis is in `incidents.md`: Colab reclaimed; Adaption stall; prefill thrash on both
  pods; degenerate-generation timeout loops; the 524 tunnel cutoffs.

## Files

- **Report draft:** `results/report_draft.md` (Results, Discussion, Limitations updated).
- **Tables:** `tables/signature_r1.md` and `tables/signature_all.md`.
- **Figures:**
  - `figure6_signature.png`: per harness, episode-1 rate, change per episode and overall rate, against the teacher.
  - `figure7_curves.png`: teacher, original, and the best harness in each round.
  - `figure8_heldout_qwen27b.png` and `figure10_heldout_qwen27b_items.png`: test A.
  - `figure9_heldout_gemma.png`: test B.
- **Data:** `signature.json`, `round_compare_K4.json`, `round_compare_K5.json`, `q27_score.json`,
  `gemma_score.json`, `monitor/tick_*.txt`.

## Not done / open

- Round 3 has 4–10 scored episodes per trajectory (median 8). The runs were stopped at the end of the window.
- The Google Doc tab was not edited. Its placeholders ("[Results pending.]" and "[Signature comparison pending…]")
  and figures 6–10 are waiting for the user's go-ahead.
- **End-of-window actions (18:25 UTC):**
  - Final pull to local.
  - Gemma runs on pod 1 stopped (12).
  - Colab session `swarm-gemma` stopped.
  - Pod 2 (`33uyzqdcwnbsh9`, Claude-created) **stopped**. Its /workspace volume is kept, and it can be restarted or
    terminated from RunPod.
  - **Pod 1 (`lk1vmpvr30oers`, the user's) left running.** Its Qwen3-4B vLLM server and 16 round-3 trajectories
    (seq01–04) are still running, and it is still billing. Stop it from RunPod when no longer needed.
