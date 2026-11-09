"""Run one designed environment on AgentCollabBench tasks of one metric with the official harness.
Usage: acb_eval.py <metric: rtd|cpr|idr> <spec.json> <tasks_dir> <model> <base_url> <out_dir>
Spec fields (all optional; default = the original task). Only existing task fields are changed; no text is written.
  all:  topology original|chain|full|root_broadcast (who receives from whom), turns 0.5|1|1.5|2 (speaking order)
  rtd:  salience high|low, distractors all|half|none
  cpr:  seed_position original|first|middle|last (which agent in speaking order receives the false fact)
  idr:  pressure all|half|first (how many of the task's own peer-pressure messages are delivered)
RTD is scored here (programmatic). CPR and IDR runs are saved for judge scoring (acb_score.py).
Writes <out_dir>/<task_id>.json = {"run": ..., "task": ..., "score"?: ...} and <out_dir>/summary.json.
"""
from __future__ import annotations

import copy
import json
import math
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agentcollabbench.harness.evaluate import _compute_metric
from agentcollabbench.harness.provider import OllamaProvider
from agentcollabbench.harness.runner import TaskRunner
from agentcollabbench.harness.schema import validate_task

COMMON = {"topology": ["original", "chain", "full", "root_broadcast"], "turns": [0.5, 1, 1.5, 2]}
ALLOWED = {"rtd": {**COMMON, "salience": ["high", "low"], "distractors": ["all", "half", "none"]},
           "cpr": {**COMMON, "seed_position": ["original", "first", "middle", "last"]},
           "idr": {**COMMON, "pressure": ["all", "half", "first"]}}


def speaking_sequence(task):
    order, seen = [], set()
    for a in task["topology"].get("speaking_order", []) + [x["agent_id"] for x in task["topology"]["agents"]]:
        if a not in seen:
            seen.add(a)
            order.append(a)
    return order


def transform(task, metric, spec):
    t = copy.deepcopy(task)
    ids = speaking_sequence(t)
    topo = spec.get("topology", "original")
    if topo != "original":
        if topo == "chain":
            edges = [[ids[i], ids[i + 1]] for i in range(len(ids) - 1)]
        elif topo == "full":
            edges = [[a, b] for a in ids for b in ids if a != b]
        else:  # root_broadcast: the original edges plus root -> every other agent
            edges = [list(e) for e in t["topology"]["edges"]]
            edges += [[ids[0], b] for b in ids[1:] if [ids[0], b] not in edges]
        t["topology"]["edges"] = edges
        for agent in t["topology"]["agents"]:
            agent["receives_from"] = [a for a, b in edges if b == agent["agent_id"]]
    m = float(spec.get("turns", 1))
    order = t["topology"]["speaking_order"]
    n = max(len(ids), round(len(order) * m))
    t["topology"]["speaking_order"] = (order * math.ceil(n / len(order)))[:n]
    inj = t["injections"][metric]
    if metric == "rtd":
        inj["salience"] = spec.get("salience", inj.get("salience", "high"))
        d = spec.get("distractors", "all")
        if d != "all":
            ds = inj.get("distractors", [])
            inj["distractors"] = ds[:math.ceil(len(ds) / 2)] if d == "half" else []
    elif metric == "cpr":
        pos = spec.get("seed_position", "original")
        if pos != "original":
            seed = {"first": ids[0], "middle": ids[len(ids) // 2], "last": ids[-1]}[pos]
            inj["seed_agent"] = seed
            if "patient_zero" in inj:
                inj["patient_zero"] = seed
    elif metric == "idr":
        p = spec.get("pressure", "all")
        msgs = inj.get("peer_pressure_turns", [])
        if p == "half":
            inj["peer_pressure_turns"] = msgs[:math.ceil(len(msgs) / 2)]
        elif p == "first":
            inj["peer_pressure_turns"] = msgs[:1]
    return t


def main():
    metric, spec = sys.argv[1], json.loads(Path(sys.argv[2]).read_text())
    for k, v in spec.items():
        if k not in ALLOWED[metric] or v not in ALLOWED[metric][k]:
            raise SystemExit(json.dumps({"error": f"unsupported {k}={v!r}", "allowed": ALLOWED[metric]}))
    tasks_dir, model, base_url, out = Path(sys.argv[3]), sys.argv[4], sys.argv[5], Path(sys.argv[6])
    out.mkdir(parents=True, exist_ok=True)
    provider = OllamaProvider(model=model, base_url=base_url, api_key="EMPTY", max_tokens=2048)

    def one(path):
        task = json.loads(path.read_text())
        rec = {"task_id": task["task_id"], "topology": task["topology"]["type"]}
        t0 = time.time()
        try:
            t = transform(task, metric, spec)
            validate_task(t)
            run = TaskRunner(provider=provider, metric=metric).run_task(t).to_dict()
            rec.update(seconds=round(time.time() - t0, 1))
            if metric == "rtd":
                rec["score"], _ = _compute_metric("rtd", run["scenario"], run["output"], run["trace"], {}, None)
            (out / f"{task['task_id']}.json").write_text(json.dumps({"run": run, "task": t, **rec}, default=str))
        except Exception as exc:
            rec.update(error=repr(exc)[:300], traceback=traceback.format_exc()[-1500:])
            (out / f"{task['task_id']}.json").write_text(json.dumps(rec))
        return rec

    with ThreadPoolExecutor(20) as pool:
        recs = list(pool.map(one, sorted(tasks_dir.glob("*.json"))))
    (out / "summary.json").write_text(json.dumps({r["task_id"]: {k: r.get(k) for k in ("topology", "score", "error")}
                                                  for r in recs}))
    print(json.dumps({"done": len(recs), "errors": sum("error" in r for r in recs)}))


if __name__ == "__main__":
    main()
