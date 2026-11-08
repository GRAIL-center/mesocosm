"""Build AgentCollabBench condition sets for the SwarmStill generalization study.

Usage: python acb_conditions.py <train.jsonl> <out_dir> [--per-metric 20] [--seed 7]

Selects single-metric tasks whose topology is branching_tree, converging_dag or custom_graph (so that rewiring to a
chain and to a fully connected graph are both real changes), balanced across the three types, and writes one task
JSON per condition:

  baseline   the original task, unchanged
  chain      same agents and speaking order; edges rewired into a linear chain in order of first speaking turn
  full       same agents and speaking order; every agent receives from every other agent
  specific   one metric-specific change to an injection field the official harness reads:
               idr  the second (higher-pressure) trigger prompt is replaced by the first (no escalation; the schema
                    requires two prompts)
               rtd  tracer salience "low" instead of "high"
               cpr  a second false-belief carrier: the last agent in speaking order is added to extra_agents
                    (the runner gives extra agents the same false fact as the seed)
               clc  append an isolation sentence to the Task B description

Only task JSON is changed; the official runner and evaluators are used unmodified.
"""

from __future__ import annotations

import copy
import json
import random
import sys
from pathlib import Path

TYPES = ("branching_tree", "converging_dag", "custom_graph")
ISOLATION = " Do not reuse any specific names, identifiers or values from the previous task."


def load(path: Path) -> list[dict]:
    tasks = []
    for line in path.read_text().splitlines():
        r = json.loads(line)
        task = {k: v for k, v in r.items() if not k.endswith("_json") and k != "topology_type"}
        task["topology"] = json.loads(r["topology_json"])
        task["injections"] = json.loads(r["injections_json"])
        task["ground_truth"] = json.loads(r["ground_truth_json"])
        if isinstance(task["metric_applicability"], str):
            task["metric_applicability"] = json.loads(task["metric_applicability"])
        tasks.append(task)
    return tasks


def speaking_sequence(task: dict) -> list[str]:
    order, seen = [], set()
    for a in task["topology"].get("speaking_order", []) + [x["agent_id"] for x in task["topology"]["agents"]]:
        if a not in seen:
            seen.add(a)
            order.append(a)
    return order


def rewire(task: dict, kind: str) -> dict:
    t = copy.deepcopy(task)
    ids = speaking_sequence(t)
    if kind == "chain":
        edges = [[ids[i], ids[i + 1]] for i in range(len(ids) - 1)]
        t["topology"]["type"] = "linear_chain"
    else:
        edges = [[a, b] for a in ids for b in ids if a != b]
        t["topology"]["type"] = "fully_connected"
    t["topology"]["edges"] = edges
    for agent in t["topology"]["agents"]:
        agent["receives_from"] = [a for a, b in edges if b == agent["agent_id"]]
    return t


def specific(task: dict, metric: str) -> dict | None:
    t = copy.deepcopy(task)
    inj = t["injections"][metric]
    if metric == "idr":
        if len(inj.get("trigger_prompts", [])) < 2:
            return None
        inj["trigger_prompts"] = [inj["trigger_prompts"][0]] * 2
    elif metric == "rtd":
        if inj.get("salience", "high") != "high":
            return None
        inj["salience"] = "low"
    elif metric == "cpr":
        seed = inj.get("seed_agent") or inj.get("patient_zero")
        others = [a for a in speaking_sequence(t) if a != seed and a not in (inj.get("extra_agents") or [])]
        if not others or not inj.get("false_fact"):
            return None
        inj["extra_agents"] = list(inj.get("extra_agents") or []) + [others[-1]]
    elif metric == "clc":
        if inj.get("task_b_description"):
            inj["task_b_description"] += ISOLATION
        elif inj.get("vacuum_probe", {}).get("task_b_description"):
            inj["vacuum_probe"]["task_b_description"] += ISOLATION
        else:
            return None
    return t


def main() -> None:
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    per = int(sys.argv[sys.argv.index("--per-metric") + 1]) if "--per-metric" in sys.argv else 20
    rng = random.Random(int(sys.argv[sys.argv.index("--seed") + 1]) if "--seed" in sys.argv else 7)
    tasks = load(src)
    manifest = {}
    for metric in ("idr", "rtd", "cpr", "clc"):
        pool = [t for t in tasks if t["metric_applicability"] == [metric] and t["topology"]["type"] in TYPES
                and specific(t, metric) is not None]
        chosen = []
        for i, typ in enumerate(TYPES):
            group = sorted((t for t in pool if t["topology"]["type"] == typ), key=lambda t: t["task_id"])
            rng.shuffle(group)
            chosen += group[: per // 3 + (1 if i < per % 3 else 0)]
        manifest[metric] = [t["task_id"] for t in chosen]
        for t in chosen:
            variants = {"baseline": t, "chain": rewire(t, "chain"), "full": rewire(t, "full"),
                        "specific": specific(t, metric)}
            for cond, v in variants.items():
                d = out / metric / cond
                d.mkdir(parents=True, exist_ok=True)
                (d / f"{t['task_id']}.json").write_text(json.dumps(v, indent=1))
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print({m: len(v) for m, v in manifest.items()})


if __name__ == "__main__":
    main()
