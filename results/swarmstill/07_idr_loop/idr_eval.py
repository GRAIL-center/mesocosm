"""Run one designed environment on IDR tasks with the official AgentCollabBench harness and score it.

Usage: idr_eval.py <spec.json> <tasks_dir> <model> <model_base_url> <judge_model> <judge_base_url> <out_dir>

Spec (every field optional; default = the original task):
  topology:   original | chain | full | root_broadcast   (who receives from whom)
  turns:      0.5 | 1 | 1.5 | 2                           (speaking-order length multiplier)
  escalation: original | none                             (none: the second, higher-pressure trigger prompt is
                                                           replaced by the first, as in the generalization study)
Only the task JSON changes; the harness, the agents' prompts, the judge prompt and the scorer are the official ones.
The topology and turns transforms are those of the teammate's design_eval.py (06_iterative_design).

Writes <out_dir>/<task_id>.json (score and per-turn violations) and <out_dir>/summary.json with the signature:
  idr            mean official IDR score over tasks
  low_violation  share of low-pressure trigger turns judged a violation
  high_violation share of high-pressure trigger turns judged a violation
"""

from __future__ import annotations

import copy
import json
import math
import re
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
           "escalation": ["original", "none"]}
TURN = re.compile(r"violated=(True|False), trigger_type='(\w+)'")


def speaking_sequence(task: dict) -> list[str]:
    order, seen = [], set()
    for a in task["topology"].get("speaking_order", []) + [x["agent_id"] for x in task["topology"]["agents"]]:
        if a not in seen:
            seen.add(a)
            order.append(a)
    return order


def transform(task: dict, spec: dict) -> dict:
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
    if spec.get("escalation", "original") == "none":
        inj = t["injections"]["idr"]
        inj["trigger_prompts"] = [inj["trigger_prompts"][0]] * len(inj["trigger_prompts"])
    return t


def turns_of(detailed: object) -> list[tuple[bool, str]]:
    """(violated, trigger_type) per judged turn, from the IDRResult object or its repr."""
    items = getattr(detailed, "turn_results", None)
    if items is not None:
        return [(bool(x.violated), str(x.trigger_type)) for x in items]
    return [(v == "True", t) for v, t in TURN.findall(repr(detailed))]


def signature(recs: list[dict]) -> dict:
    ok = [r for r in recs if "score" in r]
    turns = [t for r in ok for t in r["turns"]]

    def share(kind: str) -> float | None:
        xs = [v for v, t in turns if t == kind]
        return sum(xs) / len(xs) if xs else None

    return {"tasks": len(ok), "errors": len(recs) - len(ok),
            "idr": sum(r["score"] for r in ok) / len(ok) if ok else None,
            "low_violation": share("low_pressure"), "high_violation": share("high_pressure")}


def main() -> None:
    spec = json.loads(Path(sys.argv[1]).read_text())
    for k, v in spec.items():
        if k not in ALLOWED or v not in ALLOWED[k]:
            raise SystemExit(json.dumps({"error": f"unsupported {k}={v!r}", "allowed": ALLOWED}))
    tasks_dir, model, base, jmodel, jbase, out = (Path(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5],
                                                   sys.argv[6], Path(sys.argv[7]))
    out.mkdir(parents=True, exist_ok=True)
    provider = OllamaProvider(model=model, base_url=base, api_key="EMPTY", max_tokens=2048)
    judge = OllamaProvider(model=jmodel, base_url=jbase, api_key="EMPTY", max_tokens=4096)

    def one(path: Path) -> dict:
        task = json.loads(path.read_text())
        rec: dict = {"task_id": task["task_id"], "topology": task["topology"]["type"]}
        t0 = time.time()
        try:
            t = transform(task, spec)
            validate_task(t)
            run = TaskRunner(provider=provider, metric="idr").run_task(t).to_dict()
            score, detailed = _compute_metric("idr", run["scenario"], run["output"], run["trace"],
                                              {"judge_provider": judge}, None)
            rec.update(score=score, turns=turns_of(detailed), seconds=round(time.time() - t0, 1))
            (out / f"{task['task_id']}.run.json").write_text(json.dumps({"task": t, "run": run}, default=str))
        except Exception as exc:  # recorded, never silently dropped
            rec.update(error=repr(exc)[:300], traceback=traceback.format_exc()[-1500:])
        (out / f"{task['task_id']}.json").write_text(json.dumps(rec))
        return rec

    paths = sorted(p for p in tasks_dir.glob("*.json") if not p.name.endswith(".run.json"))
    with ThreadPoolExecutor(12) as pool:
        recs = list(pool.map(one, paths))
    summary = {"spec": spec, "signature": signature(recs),
               "per_task": {r["task_id"]: {k: r.get(k) for k in ("topology", "score", "error")} for r in recs}}
    (out / "summary.json").write_text(json.dumps(summary))
    print(json.dumps(summary["signature"]))


if __name__ == "__main__":
    main()
