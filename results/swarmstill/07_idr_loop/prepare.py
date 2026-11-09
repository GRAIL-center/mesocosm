"""Operator setup for the IDR loop-design study: task files, loop/held-out split, teacher reference signatures.

Usage: python3 prepare.py <train.jsonl> <merged.jsonl> <manifest.json> <out_dir>

- Tasks: the 20 IDR tasks of the teammate's generalization study (manifest.json), original JSON from the
  AgentCollabBench train split (unchanged).
- Split (fixed rule, ignores scores): the 12 task ids with the smallest sha256("loop|<task_id>") are loop tasks; the
  other 8 are held-out tasks, never shown to a designer.
- Teacher: Qwen3-8B team, original environment, from the teammate's scored runs (merged.jsonl, model q8). Its
  signature on each split: mean IDR score, and the shares of low- and high-pressure trigger turns judged violations.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

TURN = re.compile(r"violated=(True|False), trigger_type='(\w+)'")


def load_tasks(path: Path, ids: set[str]) -> dict[str, dict]:
    out = {}
    for line in path.read_text().splitlines():
        r = json.loads(line)
        if r.get("task_id") not in ids:
            continue
        task = {k: v for k, v in r.items() if not k.endswith("_json") and k != "topology_type"}
        task["topology"] = json.loads(r["topology_json"])
        task["injections"] = json.loads(r["injections_json"])
        task["ground_truth"] = json.loads(r["ground_truth_json"])
        if isinstance(task["metric_applicability"], str):
            task["metric_applicability"] = json.loads(task["metric_applicability"])
        out[task["task_id"]] = task
    return out


def signature(rows: list[dict]) -> dict:
    turns = [(v == "True", t) for r in rows for v, t in TURN.findall(r["detailed"])]

    def share(kind: str) -> float:
        xs = [v for v, t in turns if t == kind]
        return sum(xs) / len(xs)

    return {"tasks": len(rows), "idr": sum(r["score"] for r in rows) / len(rows),
            "low_violation": share("low_pressure"), "high_violation": share("high_pressure")}


def main() -> None:
    train, merged, manifest, out = map(Path, sys.argv[1:5])
    ids = json.loads(manifest.read_text())["idr"]
    order = sorted(ids, key=lambda t: hashlib.sha256(f"loop|{t}".encode()).hexdigest())
    split = {"loop": order[:12], "heldout": order[12:]}
    tasks = load_tasks(train, set(ids))
    missing = sorted(set(ids) - set(tasks))
    if missing:
        raise SystemExit(f"tasks not found in {train}: {missing}")
    rows = [json.loads(line) for line in merged.read_text().splitlines()]
    teacher = {r["task_id"]: r for r in rows if r["metric"] == "idr" and r["condition"] == "baseline"
               and r["model"] == "q8" and "score" in r}
    ref = {}
    for name, tids in split.items():
        d = out / "tasks" / name
        d.mkdir(parents=True, exist_ok=True)
        for t in tids:
            (d / f"{t}.json").write_text(json.dumps(tasks[t], indent=1))
        ref[name] = signature([teacher[t] for t in tids])
        ref[name]["topologies"] = {t: tasks[t]["topology"]["type"] for t in tids}
    (out / "split.json").write_text(json.dumps(split, indent=1) + "\n")
    (out / "teacher_reference.json").write_text(json.dumps(ref, indent=1) + "\n")
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "topologies"} for k, v in ref.items()}, indent=1))


if __name__ == "__main__":
    main()
