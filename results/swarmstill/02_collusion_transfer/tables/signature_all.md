| Harness | Round | Changes from original | n | Episodes | Ep. 1 | Change/ep. | Overall (teacher) | Own task |
|---|---|---|---|---|---|---|---|---|
| Qwen3-4B, original, no memory | ref |  | 6 | 1–10 | 50% | +1.6 | 33% (42%) | 53% |
| Qwen3-4B, original settings | ref |  | 8 | 1–10 | 38% | -1.0 | 21% (42%) | 48% |
| cl_fbmem | r1 | cross-episode-memory-scope feedback-and-reflection; reward-scope separate | 4 | 1–10 | 50% | +0.6 | 32% (42%) | 50% |
| cx_summary | r1 | cross-episode-memory-scope communication-onward; reward-scope separate; 3 warm-up episodes; verdict-policy-spans summary-allowed:3,raw-only:* | 3 | 1–8 | 0% | +0.0 | 0% (35%) | 38% |
| cx_recon | r1 | reward-scope separate | 4 | 1–10 | 25% | -1.4 | 3% (42%) | 36% |
| cb_fbshared | r1 | cross-episode-memory-scope feedback-and-reflection; reward-scope shared | 3 | 1–9 | 67% | -6.1 | 41% (39%) | 44% |
| cb_commsep | r1 | cross-episode-memory-scope communication-onward; reward-scope separate | 3 | 1–7 | 100% | -4.8 | 33% (29%) | 43% |
| cxr2_rawfull | r2 | 3 warm-up episodes; throttle-spans no-throttle:3,throttled:*; cross-episode-memory-length -1; cross-episode-memory-scope full-history; reward-scope shared; reward-type verdict-accuracy | 6 | 1–10 | 17% | +1.8 | 23% (42%) | 41% |
| r2b_warmup | r2 | cross-episode-memory-length -1; cross-episode-memory-scope communication-onward; reward-type verdict-accuracy; 3 warm-up episodes; throttle-spans no-throttle:3,throttled:* | 5 | 1–10 | 20% | +1.7 | 28% (42%) | 49% |
| r2a_warmfb | r2 | 3 warm-up episodes; throttle-spans no-throttle:3,throttled:*; cross-episode-memory-length -1; cross-episode-memory-scope feedback-and-reflection; reward-type verdict-accuracy; reward-scope separate | 5 | 1–10 | 0% | -0.5 | 12% (42%) | 45% |
| r2a_warmfull | r2 | 3 warm-up episodes; throttle-spans no-throttle:3,throttled:*; cross-episode-memory-length -1; cross-episode-memory-scope full-history; reward-type verdict-accuracy; reward-scope separate | 6 | 1–10 | 17% | -0.9 | 2% (42%) | 29% |
| r2b_fbonly | r2 | cross-episode-memory-length -1; cross-episode-memory-scope communication-onward; no-reward; no-reflection | 6 | 1–10 | 17% | -2.2 | 7% (42%) | 50% |
| cxr2_rehearsal | r2 | 3 warm-up episodes; throttle-spans no-throttle:3,throttled:*; cross-episode-memory-length 3; cross-episode-memory-scope feedback-and-reflection; reward-scope separate; reward-type verdict-accuracy | 6 | 1–10 | 50% | -0.4 | 17% (42%) | 50% |
| r3b_boundedsummary | r3 | 3 warm-up episodes; verdict-policy-spans summary-allowed:3,raw-only:*; cross-episode-memory-length 1; cross-episode-memory-scope full-history; reward-type verdict-accuracy; reward-scope separate | 6 | 1–8 | 0% | +5.1 | 17% (35%) | 43% |
| r3a_taskmem2summar | r3 | 3 warm-up episodes; verdict-policy-spans summary-allowed:3,raw-only:*; cross-episode-memory-length 2; cross-episode-memory-scope full-history; reward-scope separate; reward-type verdict-accuracy | 5 | 1–7 | 0% | +1.8 | 3% (29%) | 36% |
| r3b_boundedraw | r3 | 3 warm-up episodes; throttle-spans no-throttle:3,throttled:*; cross-episode-memory-length 1; cross-episode-memory-scope full-history; reward-type verdict-accuracy; reward-scope separate | 6 | 1–10 | 17% | -1.6 | 15% (42%) | 45% |
| r3a_taskmem1raw | r3 | 3 warm-up episodes; throttle-spans no-throttle:3,throttled:*; cross-episode-memory-length 1; cross-episode-memory-scope full-history; reward-scope separate; reward-type verdict-accuracy | 6 | 1–9 | 17% | -1.1 | 5% (39%) | 44% |

Teacher (Gemini 3.1 Flash-Lite): 0% in episode 1, +8.9 points per episode over 1–10, 42.4% overall.
