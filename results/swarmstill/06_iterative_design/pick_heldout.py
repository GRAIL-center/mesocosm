import json, random, glob, os, sys
sys.path.insert(0, "/workspace/AgentCollabBench")
from agentcollabbench.harness.schema import validate_task
used = {os.path.basename(p)[:-5] for p in glob.glob("/workspace/design/loop/*.json")}
pool = {"branching_tree": [], "converging_dag": [], "custom_graph": []}
for p in sorted(glob.glob("/workspace/AgentCollabBench/tasks/*.json")):
    t = json.load(open(p))
    if t.get("metric_applicability") == ["rtd"] and t["topology"]["type"] in pool and t["task_id"] not in used:
        try:
            validate_task(t)
            pool[t["topology"]["type"]].append(t)
        except Exception:
            pass
rng = random.Random(11)
pick = []
for k, n in (("branching_tree", 7), ("converging_dag", 7), ("custom_graph", 6)):
    pick += rng.sample(pool[k], n)
for t in pick:
    json.dump(t, open("/workspace/design/heldout/" + t["task_id"] + ".json", "w"))
print({k: len(v) for k, v in pool.items()}, "picked", len(pick), "loop", len(used))
