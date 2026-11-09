"""Run one designed environment on RTD tasks with the official AgentCollabBench harness and score it.
Usage: design_eval.py <spec.json> <tasks_dir> <model> <port> <out_dir>
Spec (every field optional; default = the original task):
  topology: original | chain | full | root_broadcast   (who receives from whom)
  turns: 0.5 | 1 | 1.5 | 2                              (speaking-order length multiplier)
  salience: high | low                                  (how the constraint line is formatted)
  distractors: all | half | none                        (distractor lines next to the constraint)
Only the task JSON changes; the harness, prompts of the agents and the scorer are the official ones.
Writes <out_dir>/<task_id>.json (score), <out_dir>/<task_id>.run.json (transformed task and full trace)
and <out_dir>/summary.json.
"""
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

ALLOWED = {"topology": ["original", "chain", "full", "root_broadcast"], "turns": [0.5, 1, 1.5, 2],
           "salience": ["high", "low"], "distractors": ["all", "half", "none"]}


def speaking_sequence(task):
    order, seen = [], set()
    for a in task["topology"].get("speaking_order", []) + [x["agent_id"] for x in task["topology"]["agents"]]:
        if a not in seen:
            seen.add(a)
            order.append(a)
    return order


def transform(task, spec):
    t = copy.deepcopy(task)
    topo = spec.get("topology", "original")
    ids = speaking_sequence(t)
    if topo != "original":
        if topo == "chain":
            edges = [[ids[i], ids[i + 1]] for i in range(len(ids) - 1)]
        elif topo == "full":
            edges = [[a, b] for a in ids for b in ids if a != b]
        else:  # root_broadcast: keep the original edges and add root -> every other agent
            edges = [list(e) for e in t["topology"]["edges"]]
            edges += [[ids[0], b] for b in ids[1:] if [ids[0], b] not in edges]
        t["topology"]["edges"] = edges
        for agent in t["topology"]["agents"]:
            agent["receives_from"] = [a for a, b in edges if b == agent["agent_id"]]
    m = float(spec.get("turns", 1))
    order = t["topology"]["speaking_order"]
    n = max(len(ids), round(len(order) * m))
    t["topology"]["speaking_order"] = (order * math.ceil(n / len(order)))[:n]
    inj = t["injections"]["rtd"]
    inj["salience"] = spec.get("salience", inj.get("salience", "high"))
    d = spec.get("distractors", "all")
    if d != "all":
        ds = inj.get("distractors", [])
        inj["distractors"] = ds[:math.ceil(len(ds) / 2)] if d == "half" else []
    return t


def main():
    spec = json.loads(Path(sys.argv[1]).read_text())
    for k, v in spec.items():
        if k not in ALLOWED or v not in ALLOWED[k]:
            raise SystemExit(json.dumps({"error": f"unsupported {k}={v!r}", "allowed": ALLOWED}))
    tasks_dir, model, port, out = Path(sys.argv[2]), sys.argv[3], sys.argv[4], Path(sys.argv[5])
    out.mkdir(parents=True, exist_ok=True)
    provider = OllamaProvider(model=model, base_url=f"http://127.0.0.1:{port}/v1", api_key="EMPTY", max_tokens=2048)

    def one(path):
        task = json.loads(path.read_text())
        rec = {"task_id": task["task_id"], "topology": task["topology"]["type"]}
        t0 = time.time()
        try:
            t = transform(task, spec)
            validate_task(t)
            run = TaskRunner(provider=provider, metric="rtd").run_task(t).to_dict()
            score, _ = _compute_metric("rtd", run["scenario"], run["output"], run["trace"], {}, None)
            rec.update(score=score, seconds=round(time.time() - t0, 1))
            (out / f"{task['task_id']}.run.json").write_text(json.dumps({"task": t, "run": run}, default=str))
        except Exception as exc:
            rec.update(error=repr(exc)[:300], traceback=traceback.format_exc()[-1500:])
        (out / f"{task['task_id']}.json").write_text(json.dumps(rec))
        return rec

    with ThreadPoolExecutor(20) as pool:
        recs = list(pool.map(one, sorted(p for p in tasks_dir.glob("*.json") if not p.name.endswith(".run.json"))))
    summary = {r["task_id"]: {k: r.get(k) for k in ("topology", "score", "error")} for r in recs}
    (out / "summary.json").write_text(json.dumps(summary))
    print(json.dumps({"done": len(recs), "errors": sum("error" in r for r in recs)}))


if __name__ == "__main__":
    main()
