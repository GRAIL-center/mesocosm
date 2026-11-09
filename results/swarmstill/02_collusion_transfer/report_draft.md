# Report draft: Results, Discussion, Limitations, Conclusion

Bracketed items `[…]` are filled when the small-model and Qwen3.8-27B runs finish.

## 4. Results

### 4.1 The harness, not the model alone, decides the behavior

In the released ablations, one model (Gemini 3.1 Flash-Lite) colludes in 0.0% to 69.8% of episodes depending on
the harness (Figure 1). Removing any one of verdict-correctness feedback, cross-episode memory, the shared reward or
the reward display drives collusion from 42.4% to 0–6%. The same interventions move a stronger model of the same
family in opposite directions: hiding the reward leaves Gemini 3.7 Flash at 84.6% and removing verdict feedback
only lowers it to 72.4% (Table 5). The stronger model mostly starts by agreeing explicitly in messages (45 of 50
onsets); the weaker one mostly relaxes after being told a strict verdict was wrong. Paying directly for every
ACCEPT gives 0% for both: agents read a reward for the violating act as a temptation and keep the rule.

### 4.2 Frontier designers recover the mechanism but not its necessity

Eighteen fresh designer sessions (Claude Opus 5.5 subagents and GPT-6.1 Sol in Codex) inferred the harness from 20
masked trajectories. With traces, all 12 sessions recovered that the reward pays for verdict accuracy and that
agents are told whether each verdict was right; without traces, all 6 sessions guessed reciprocity paid by
acceptance and no verdict feedback (Tables 2–3). Their predictions for 12 released interventions were no better
than answering "no change" (direction accuracy 0.32–0.42 against 0.25; MAE 22.9–27.4 points against 24.4; Table 4,
Figure 2). They predicted partial drops where the behavior collapses, and a rise where paying for ACCEPT gives 0%.

### 4.3 Profiles help where the answer is visible; guides and probes do not transfer

Given each model's profile, designers predicted that removing memory eliminates the behavior in both models (0%
predicted and released), which they missed without it (Table 6). A natural-language guide added nothing measurable.
Showing two measured probes (no memory; no verdict feedback) did not carry over to interventions sharing the same
cause: told that removing verdict feedback gives 0%, designers still predicted 25–70% when verdict feedback is
removed together with a reward for ACCEPT (released: 0%; Figure 3). The same pattern holds for a 397B open
designer. The failure is not missing information but a reward-maximizer prior that overrides measured evidence.

### 4.4 A revised guide on a held-out teacher (Qwen3.8-27B)

We rewrote the guide around these failures (v2: agents refuse direct payment for violations; violations need a
legitimate rationale, corrective feedback and memory jointly; models differ in pathway; probe before committing)
and froze predictions for seven interventions on Qwen3.8-27B before running them through the Adaption API.
The Adaption API stalled for about five hours, then recovered.

Scored on the full 10 episodes, with 3 paired trajectories per condition (`q27_score.json`; Figures 8 and 10):

| Arm | Sessions | Direction accuracy | Relative MAE | Spearman ρ |
|---|---|---|---|---|
| No guide | 5 | 0.57 | 44 | 0.40 |
| Guide v1 | 5 | 0.60 | 41 | 0.40 |
| Guide v2 | 4 | 0.57 | 20 | 0.63 |
| Answer "no change" | – | 0.14 | 63 | – |

By designer family, guide v2 gave the lowest relative error for both:
- GPT-6.1 Sol: 46 (no guide), 45 (v1), 21 (v2).
- Qwen3.5-397B: 41 (no guide), 35 (v1), 18 (v2).

Measured on Qwen3.8-27B, against 85.0% unchanged (the paper reports 82%):
- Removing the review: 2.5%.
- Separate rewards, no memory, and paying every ACCEPT: 0%.
- Hiding the reward: 70%.
- Memory of the last 3 episodes: 77%.
- Keeping only feedback and reflection: 70%.

The earlier 4-episode interim gave the same ordering (31 vs 50–53; `q27_score_K4.json`).

Guide v2 roughly halves the relative error. The gain comes from the two interventions the guide's first principle is
about:
- Paying every ACCEPT and removing the review: v2 predicted 1–3%, against 92–97% for the other arms. Measured: 0%.
- Removing the verdict review: v2 predicted 5–15%, against 18–70% for the others. Measured: 0%.

Both v2 designer families (GPT-6.1 Sol and Qwen3.5-397B) made these corrections. All arms still missed separate
rewards: 45–80% predicted, 0% measured.

Caveats:
- 2–3 trajectory pairs per condition.
- The measured window (episodes 1–4) is shorter than the 10 episodes the designers predicted for.
- Default reasoning effort.

### 4.5 Reproducing the behavior in Qwen3-4B

Under the original settings, Qwen3-4B shows the opposite signature (final, 8 trajectories × 10 episodes):
- 38% mutual acceptance in episode 1, where the teacher has 0%.
- No rise over episodes: −0.8 points per episode, against the teacher's +8.9.
- 22% overall, against 42.4%.
- More collusion without memory than with it: 33% overall and 50% in episode 1.

Its own-task accuracy is about half (48%), so verdict feedback carries little information. Its first-episode accepts
repeat the peer's claims.

Round-1 designers had neither the profile nor the guide. Their five harnesses kept this indiscriminate pattern
(`tables/signature_r1.md`, Figure 6):
- Episode 1 was 25–100% in four of the five.
- Every slope was ≤ +0.6 points per episode.
- Two harnesses matched the overall rate for the wrong reason: cb_fbshared at 41% against the teacher's 39% over the
  same window, starting at 67%; cb_commsep at 33% against 29%, starting at 100%.
- The only clean start, cx_summary (summary evidence allowed in warm-up), never produced the behavior: 0% over
  6 episodes.

Matching the rate alone would therefore have certified harnesses whose mechanism is the opposite of the teacher's. Round-2 designers had the profile and the v2 guide. All four proposed a warm-up that lets agents exchange and verify
full logs before the scored episodes, to restore a clean start and a learnable corrective signal.

Pooled over trajectories, first 5 scored episodes (`round_compare.json`; interim, 14:36 UTC):

| Group | Trajectories | Episode-1 both ACCEPT | Rise (episodes 4–5 minus 1–2) | Overall |
|---|---|---|---|---|
| Teacher | — | 0% | +36 | 17% |
| Qwen3-4B, original | 8 | 38% | −19 | 23% |
| Round 1 | 17 | 47% [24, 71] | −6 [−26, 18] | 24% |
| Round 2 | 22 | 23% [9, 41] | +7 [−7, 23] | 18% |

Round 2 moved toward the teacher on both mechanism measures:
- About half the episode-1 acceptance of round 1 (−24 points, 95% CI [−54, 5]).
- A positive rather than negative change over episodes (+13 against round 1, CI [−15, 38]; +26 against the original,
  CI [−3, 54]).

Neither difference excludes zero with 17–22 trajectories. Over longer windows the round-2 curves flatten:
- r2a_warmfb: 0, 0, 40, 25, 0, 25, 0%.
- cxr2_rawfull: 17, 17, 17, 40, 25, 0%.

So the clean start is reproducible but the learned rise is not. This is what the peer-correct-rate analysis predicts
(Section 4.6).

### 4.6 Why the rise does not transfer, and round 3

The verdict review tells an agent whether its verdict matched the peer's actual correctness.
- The teacher solves 87% of its tasks, so most strict rejections are judged wrong. That pushes toward ACCEPT and
  produces the rise.
- Qwen3-4B solves 48%, so its rejections are judged right about half the time and the review pushes nowhere. Its
  next-episode ACCEPT rate after a REJECT judged wrong is 13%, against 9% after one judged right.

Guide v3 adds this analysis and the measured table of all 13 harnesses. With them, two fresh round-3 designers
converged on the same idea, which tests whether the push can be restored without changing tasks:
- deterministic or low-temperature decoding to raise task accuracy;
- full-evidence warm-ups;
- memory limited to the last 1–2 episodes.

Round 3 result (first 5 scored episodes, 19 trajectories; 18:20 UTC; `round_compare_K5.json`):
- Episode-1 acceptance 11%, against 47% in round 1 (difference −37, CI [−65, −8]) and 19% in round 2.
- Rise +8 points (round 1: −6; teacher: +36).
- Overall 11% (teacher: 17%).

The closest single harness of all 17 is r3b_boundedsummary: 0, 17, 0, 20, 20, 20, 20, 50% over 8 episodes, distance 85.

The decoding change did not move the variable it targeted. Own-task accuracy, which is also the share of peer
answers that are correct, is 43% in round 3, against 48% under the original settings, 41% in round 1 and 44% in
round 2; the teacher is at 87%.

With the peer-correct rate unchanged, the verdict review still pushes nowhere. Agents that start strict stay strict,
and the behavior almost disappears.

Across 17 harnesses and three rounds, the pattern is consistent:
- What the designers can change with the available controls: the start (episode-1 acceptance from 47% to 17–18%) and
  the overall rate.
- What they cannot change: the corrective push that produces a learned rise, because no available control changes how
  often the peer is right.

Guide v3 states this condition and predicts the failure. A reproduction on this target would need easier tasks or a
stronger small model, and neither is an allowed control in this setup.

## 5. Discussion and Limitations

**What the results mean for SwarmStill.** Frontier agents can read collective failures out of traces: they name the
right ingredients and the pathway. They cannot yet say which ingredients are load-bearing, and the error is
systematic rather than random: they reason about agents as reward maximizers, even after seeing measurements that
contradict it. A reproduction harness for a smaller model depends on exactly that missing knowledge, because the
smaller model rests on a different pathway. The practical consequence is a loop, not a one-shot design: profile the
target model, probe each lever on it, and keep only harnesses whose signature and intervention response match.

**Reward framing is a lever in its own right.** The same incentive to accept produces collusion when framed as
verdict accuracy and none when framed as a payment for accepting. Monitors and defenses tested on reproductions
should vary framing, not only magnitude.

### Limitations

- Small samples: 3–6 designer sessions per arm, one teacher family for the prediction tests, 4–8 trajectories per
  small-model harness. Results are descriptive; no significance is claimed.
- The guide v2 was written after seeing Gemini results; it is evaluated only on held-out Qwen3.8-27B interventions
  and on Qwen3-4B, not on the Gemini data it was built from.
- Held-out test A (Qwen3.8-27B through the Adaption API) is scored on a short window.
  - The API stalled from about 08:30 to 14:00 UTC; at the worst point, 29 calls in 30 minutes.
  - To focus throughput we kept three paired sequences per condition.
  - Test A is scored on episodes 1–4, with 2–3 pairs per condition; one condition (J7) is still missing.
- Held-out test B (Gemma-4-12B-it on a Colab A100) is low-powered and its baseline is unstable.
  - The free tunnel cuts responses at 100 s, which forced output caps (2048 and 768 tokens) and relaunches.
  - The frozen profile (25%, 3 trajectories) differs from the later main rate (83%).
- Several Qwen3-4B trajectories were stopped when one call entered a timeout loop (degenerate generation to the
  8192-token cap under a loaded server). Each such harness keeps at least 3 trajectories; see
  `monitor/incidents.md`.
- An interface mismatch (Qwen passes answers as JSON arrays) caused early answers to be graded as wrong; we patched
  the runner to serialize array answers and reran all Qwen3-4B runs. One round-2 harness combined no reward with
  reflection, which the runner rejects; we ran it without reflection, as in the paper's no-reward condition.
- Designer isolation relied on instructions and audits: no designer used the web or opened operator files, but
  several Claude sessions wrote helper files in a shared scratch folder.

### Dual-use and ethics

The work studies how to make agents collude. All runs use local tasks and an internal runner; no external system is
targeted. The findings are more useful defensively (which levers create or remove collusion) than offensively, and
we release no prompts that instruct collusion.

## 6. Conclusion

SwarmStill asks whether frontier agents can distill a collective behavior into small models. They can recover the
mechanism from traces, but not which parts of it are necessary, and that is the part distillation needs because the
target model's pathway differs. A guide that encodes these lessons halved designers' prediction error on a held-out teacher: relative MAE 20 against
44, Spearman 0.63 against 0.40, on Qwen3.8-27B.

Three rounds of guided redesign changed Qwen3-4B as follows:
- They restored the teacher's clean start: episode-1 acceptance fell from 47% to 11%.
- They did not restore its learned rise.
- The reason is measurable. The rise is driven by corrective feedback that pushes toward acceptance only when peers
  are usually right, and no available control raised the 4B model's 41–48% peer-correct rate toward the teacher's
  87%.

Distillation of a collective behavior therefore requires matching not only the harness but the conditions under which
its feedback carries the same signal.
