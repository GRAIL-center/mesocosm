# Alignment score and sequence overlap (post hoc)

Computed after the Qwen3-4B rounds (`../02_collusion_transfer/`) to give one interpretable number per harness.

- **Alignment** = 100 × (1 − MAE / MAE_null), where MAE is the mean absolute difference between a harness's
  per-episode both-ACCEPT curve and the teacher's, and MAE_null is the error of a flat curve. Windows K = 5 and K = 8
  episodes; intervals bootstrap trajectories within harnesses; p is a one-sided harness-permutation test
  (`alignment.json`, `figure11_alignment.png`, script `../scripts/alignment.py`, figure `../scripts/fig_alignment.py`).

| Window | Original | Round 1 | Round 2 | Round 3 | Round 3 − round 1 |
|---|---|---|---|---|---|
| K = 5 | −62.6 | −60.4 | −12.5 | 3.7 | +64.1 [22.7, 105.8], p = 0.024 |
| K = 8 | 3.5 | 2.9 | 9.1 | 19.1 | +16.2 [−4.0, 38.1], p = 0.20 |

- **Sequence overlap** (`../scripts/seq_overlap.py`) between each harness's episode outcome sequences and the
  teacher's: original 37.5, round 1 51.1, round 2 61.4, round 3 64.5. A harness that never colludes scores 48, so
  part of the gain is the all-reject sequence; the script prints the decomposition.
- `stats_strength.json`: test A and round comparisons re-analysed with Holm correction
  (`../scripts/stats_strength.py`).

Both analyses were defined after the data were seen; treat them as descriptive.
