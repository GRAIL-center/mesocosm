"""Measured intervention effects on AgentCollabBench and scoring of frozen designer predictions.

Usage: python acb_analyze.py <scores.jsonl> <answers_dir> <out.json>

Truth: for each metric, model and condition, the mean official score over tasks scored in both the baseline and the
condition; direction from a paired bootstrap over tasks (2000 draws): "down" or "up" if the 95% interval of the mean
difference excludes 0, else "flat".
Predictions: <answers_dir>/<arm>-<metric>-<k>/answer.txt, each a JSON block {model: {C1|C2|C3: {score, direction}}}.
Per session: absolute MAE (points, x100), direction accuracy, Spearman over model x condition cells of the predicted vs
measured change, and model-ordering accuracy (for each condition, pairs of models ranked by measured change).
"""

from __future__ import annotations

import json
import random
import re
import statistics
import sys
from collections import defaultdict
from itertools import combinations
from pathlib import Path

COND = {"C1": "chain", "C2": "full", "C3": "specific"}
rng = random.Random(11)


def spearman(a: list[float], b: list[float]) -> float:
    def ranks(x: list[float]) -> list[float]:
        order = sorted(range(len(x)), key=lambda i: x[i])
        r = [0.0] * len(x)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and x[order[j + 1]] == x[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2
            i = j + 1
        return r
    ra, rb = ranks(a), ranks(b)
    ma, mb = statistics.mean(ra), statistics.mean(rb)
    den = (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** 0.5
    return sum((x - ma) * (y - mb) for x, y in zip(ra, rb)) / den if den else float("nan")


def parse(text: str) -> dict | None:
    for block in reversed(re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S) or [text[text.find("{"):
                                                                                             text.rfind("}") + 1]]):
        try:
            return json.loads(block)
        except Exception:
            continue
    return None


def main() -> None:
    scores, answers, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    s = defaultdict(dict)
    for line in scores.read_text().splitlines():
        r = json.loads(line)
        if "score" in r:
            s[(r["metric"], r["model"], r["condition"])][r["task_id"]] = r["score"]
    truth = {}
    for (metric, model, cond), by_task in s.items():
        if cond == "baseline":
            continue
        base = s.get((metric, model, "baseline"), {})
        common = sorted(set(base) & set(by_task))
        if len(common) < 5:
            continue
        diffs = [by_task[t] - base[t] for t in common]
        boot = sorted(statistics.mean(rng.choice(diffs) for _ in diffs) for _ in range(2000))
        lo, hi = boot[50], boot[1949]
        truth[(metric, model, cond)] = {
            "n": len(common), "baseline": statistics.mean(base[t] for t in common),
            "score": statistics.mean(by_task[t] for t in common), "change": statistics.mean(diffs),
            "ci": [lo, hi], "direction": "down" if hi < 0 else "up" if lo > 0 else "flat"}
    sessions = []
    for d in sorted(answers.glob("*-*-*")):
        arm, metric, _ = d.name.split("-", 2)
        f = d / "answer.txt"
        pred = parse(f.read_text()) if f.exists() else None
        if not pred:
            continue
        cells = [(m, c) for m in pred for c in COND if (metric, m, COND[c]) in truth and c in pred.get(m, {})]
        if not cells:
            continue
        pc = [float(pred[m][c]["score"]) - truth[(metric, m, COND[c])]["baseline"] for m, c in cells]
        tc = [truth[(metric, m, COND[c])]["change"] for m, c in cells]
        mae = 100 * statistics.mean(abs(float(pred[m][c]["score"]) - truth[(metric, m, COND[c])]["score"])
                                    for m, c in cells)
        dacc = statistics.mean(str(pred[m][c].get("direction", "")).lower() == truth[(metric, m, COND[c])]["direction"]
                               for m, c in cells)
        order_hits = []
        for c in COND:
            ms = [m for m, cc in cells if cc == c]
            for a, b in combinations(ms, 2):
                ta, tb = truth[(metric, a, COND[c])]["change"], truth[(metric, b, COND[c])]["change"]
                if abs(ta - tb) < 0.05:
                    continue
                pa = float(pred[a][c]["score"]) - truth[(metric, a, COND[c])]["baseline"]
                pb = float(pred[b][c]["score"]) - truth[(metric, b, COND[c])]["baseline"]
                order_hits.append((pa - pb) * (ta - tb) > 0)
        sessions.append({"arm": arm, "metric": metric, "session": d.name, "cells": len(cells), "abs_mae": mae,
                         "dir_acc": dacc, "spearman": spearman(pc, tc),
                         "order_acc": statistics.mean(order_hits) if order_hits else None})
    nochange = {}
    for metric in {k[0] for k in truth}:
        cs = [v for k, v in truth.items() if k[0] == metric]
        nochange[metric] = {"abs_mae": 100 * statistics.mean(abs(v["score"] - v["baseline"]) for v in cs),
                            "dir_acc": statistics.mean(v["direction"] == "flat" for v in cs)}
    agg = defaultdict(list)
    for r in sessions:
        agg[(r["arm"], r["metric"])].append(r)
        agg[(r["arm"], "all")].append(r)
    summary = {f"{a}|{m}": {k: statistics.mean(x[k] for x in rs if x[k] is not None)
                            for k in ("abs_mae", "dir_acc", "spearman", "order_acc")
                            if any(x[k] is not None for x in rs)} | {"n": len(rs)}
               for (a, m), rs in sorted(agg.items())}
    out.write_text(json.dumps({"truth": {"|".join(k): v for k, v in truth.items()}, "sessions": sessions,
                               "summary": summary, "no_change": nochange}, indent=1))
    for k, v in summary.items():
        print(k, {kk: round(vv, 3) if isinstance(vv, float) else vv for kk, vv in v.items()})
    print("no-change:", {m: {k: round(x, 3) for k, x in v.items()} for m, v in nochange.items()})


if __name__ == "__main__":
    main()
