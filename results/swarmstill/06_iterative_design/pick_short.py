"""Pick disjoint loop and held-out RTD task sets from single-metric tasks whose speaking order has at most 8 turns.
Seed 11. Loop: 6 branching tree, 6 converging DAG, 6 custom graph. Held-out: the same counts where the pool allows."""
import glob
import json
import os
import random
import shutil
import sys

sys.path.insert(0, "/workspace/AgentCollabBench")
from agentcollabbench.harness.schema import validate_task  # noqa: E402

MAX_ORDER = 8
pool = {"branching_tree": [], "converging_dag": [], "custom_graph": []}
for p in sorted(glob.glob("/workspace/AgentCollabBench/tasks/*.json")):
    t = json.load(open(p))
    if t.get("metric_applicability") == ["rtd"] and t["topology"]["type"] in pool \
            and len(t["topology"]["speaking_order"]) <= MAX_ORDER:
        try:
            validate_task(t)
            pool[t["topology"]["type"]].append(t)
        except Exception:
            pass
rng = random.Random(11)
sets = {"loop2": [], "heldout2": []}
for k in pool:
    pick = rng.sample(pool[k], min(12, len(pool[k])))
    sets["loop2"] += pick[:6]
    sets["heldout2"] += pick[6:12]
for name, tasks in sets.items():
    d = "/workspace/design/" + name
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    for t in tasks:
        json.dump(t, open(f"{d}/{t['task_id']}.json", "w"))
    print(name, len(tasks), {k: sum(t["topology"]["type"] == k for t in tasks) for k in pool},
          "max order", max(len(t["topology"]["speaking_order"]) for t in tasks))
print("pool", {k: len(v) for k, v in pool.items()})
