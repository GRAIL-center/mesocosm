| Harness | Round | Changes from original | n | Episodes | Ep. 1 | Change/ep. | Overall (teacher) | Own task |
|---|---|---|---|---|---|---|---|---|
| Qwen3-4B, original, no memory | ref |  | 6 | 1–10 | 50% | +1.6 | 33% (42%) | 53% |
| Qwen3-4B, original settings | ref |  | 8 | 1–10 | 38% | -0.8 | 22% (42%) | 48% |
| cx_summary | r1 | cross-episode-memory-scope communication-onward; reward-scope separate; 3 warm-up episodes; verdict-policy-spans summary-allowed:3,raw-only:* | 3 | 1–6 | 0% | +0.0 | 0% (23%) | 33% |
| cl_fbmem | r1 | cross-episode-memory-scope feedback-and-reflection; reward-scope separate | 4 | 1–10 | 50% | +0.6 | 32% (42%) | 50% |
| cx_recon | r1 | reward-scope separate | 4 | 1–10 | 25% | -1.4 | 3% (42%) | 36% |
| cb_fbshared | r1 | cross-episode-memory-scope feedback-and-reflection; reward-scope shared | 3 | 1–9 | 67% | -6.1 | 41% (39%) | 44% |
| cb_commsep | r1 | cross-episode-memory-scope communication-onward; reward-scope separate | 3 | 1–7 | 100% | -4.8 | 33% (29%) | 43% |

Teacher (Gemini 3.1 Flash-Lite): 0% in episode 1, +8.9 points per episode over 1–10, 42.4% overall.
