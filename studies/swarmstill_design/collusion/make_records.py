"""Write the 5-scored-episode task sequence records used by the design tests (run inside agent-collusion/task).
task_sequences_50x5: the first 5 pairs of each rep of task_sequences_50x10 (no warm-up).
task_sequences_50x3+50x5: the first 8 pairs of each rep of task_sequences_50x3+50x10 (3 warm-up + 5 scored).
Agents are not told the episode count, so the first five episodes are the same environment as in the 10-episode runs.
"""
from __future__ import annotations

import glob
import json
import os

for src, dst, n in (("task_sequences_50x10", "task_sequences_50x5", 5),
                    ("task_sequences_50x3+50x10", "task_sequences_50x3+50x5", 8)):
    os.makedirs(dst, exist_ok=True)
    k = 0
    for f in sorted(glob.glob(src + "/rep*_sampled_manifest.json")):
        d = json.load(open(f))
        d["pairs"] = d["pairs"][:n]
        json.dump(d, open(os.path.join(dst, os.path.basename(f)), "w"))
        k += 1
    print(dst, k, "reps of", n, "episodes")
