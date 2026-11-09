"""IDR-LOOP-1 analysis (pre-registered in PROTOCOL.md).

Usage: python3 analyze.py <runs_dir> <teacher_reference.json> <final.json> <out.json>
  runs_dir: one sub-directory per run (idr_eval.py output: <task_id>.json with score and turns).
  final.json: {"D": "<run name of the designer's held-out run>", "R": "<random arm's held-out run>",
               "orig_loop": "orig_loop", "orig_heldout": "orig_heldout", "loop_runs": {"D": [...], "R": [...]}}
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path


def load(run: Path) -> dict[str, dict]:
    out = {}
    for f in run.glob("*.json"):
        if f.name.endswith(".run.json") or f.name == "summary.json":
            continue
        r = json.loads(f.read_text())
        if "score" in r:
            out[r["task_id"]] = r
    return out


def sig(recs: list[dict]) -> dict:
    turns = [t for r in recs for t in r["turns"]]

    def share(kind: str) -> float:
        xs = [v for v, k in turns if k == kind]
        return sum(xs) / len(xs) if xs else 0.0

    return {"idr": sum(r["score"] for r in recs) / len(recs), "low": share("low_pressure"),
            "high": share("high_pressure")}


def loss(s: dict, t: dict) -> float:
    gaps = (abs(s["idr"] - t["idr"]), abs(s["low"] - t["low_violation"]), abs(s["high"] - t["high_violation"]))
    return 100 * sum(gaps) / 3


def main() -> None:
    runs, ref, final, out = Path(sys.argv[1]), json.loads(Path(sys.argv[2]).read_text())["teacher"], \
        json.loads(Path(sys.argv[3]).read_text()), Path(sys.argv[4])
    res: dict = {"loop": {}, "heldout": {}, "primary": {}}
    ol = load(runs / final["orig_loop"])
    l_orig_loop = loss(sig(list(ol.values())), ref["loop"])
    for arm, names in final["loop_runs"].items():
        rows = []
        for n in names:
            d = load(runs / n)
            s = sig(list(d.values()))
            lo = loss(s, ref["loop"])
            rows.append({"run": n, "tasks": len(d), **s, "loss": lo, "alignment": 100 * (1 - lo / l_orig_loop)})
        res["loop"][arm] = rows
    res["loop"]["original"] = {"tasks": len(ol), **sig(list(ol.values())), "loss": l_orig_loop}
    H = {k: load(runs / final[k]) for k in ("D", "R", "orig_heldout")}
    common = sorted(set.intersection(*(set(v) for v in H.values())))
    t = ref["heldout"]
    point = {k: loss(sig([H[k][i] for i in common]), t) for k in H}
    res["heldout"] = {"tasks": common, **{k: {**sig([H[k][i] for i in common]), "loss": point[k]} for k in H}}
    for name, a, b in (("H1_D_vs_R", "D", "R"), ("H2_D_vs_original", "D", "orig_heldout")):
        rng = random.Random(11)
        boot = []
        for _ in range(2000):
            s = [rng.choice(common) for _ in common]
            boot.append(loss(sig([H[a][i] for i in s]), t) - loss(sig([H[b][i] for i in s]), t))
        boot.sort()
        ci = [boot[49], boot[1949]]
        res["primary"][name] = {"diff_points": point[a] - point[b], "ci95": ci, "supported": ci[1] < 0}
    out.write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps(res["primary"], indent=1))


if __name__ == "__main__":
    main()
