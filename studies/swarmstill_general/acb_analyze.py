"""Measured intervention effects on AgentCollabBench and scoring of frozen designer predictions.

Usage: python acb_analyze.py <scores.jsonl> <answers_dir> <out.json>

Truth: for each metric, model and condition, the mean official score over tasks scored in both the baseline and the
condition; direction from a paired bootstrap over tasks (2000 draws): "down" or "up" if the 95% interval of the mean
difference excludes 0, else "flat".
Predictions: <answers_dir>/<arm>-<metric>-<k>/answer.txt, each a JSON block {model: {C1|C2|C3: {score, direction}}}.
Per session: absolute MAE (points, x100), direction accuracy, Spearman over model x condition cells of the predicted vs
measured change, and model-ordering accuracy (for each condition, pairs of models ranked by measured change).

Pre-registered primary analysis (`primary`; frozen by sha256 in acb_analyze.sha256 before any score of this study,
baseline or intervention, existed):
- Hypothesis H1: the guide arm (F) predicts intervention outcomes with lower absolute error than the no-guide arm (N).
- Unit: a cell = (metric, model, condition), 4 x 5 x 3 = 60 cells.
- Sessions: an arm's session counts only if its answer gives a numeric score for every cell of its metric that has a
  measured truth. A metric enters only if each arm has at least 2 such sessions. Missing cells are reported, not
  imputed.
- Statistic: d(c) = e_F(c) - e_N(c), where e_arm(c) is the mean over the arm's sessions of |predicted score - measured
  score| in points (x100). D = mean of d over cells.
- Tests:
  - p: one-sided sign-flip permutation of d over cells (100,000 draws, seed 11).
  - 95% CI of D: hierarchical bootstrap (10,000 draws, seed 11). Within each metric, it resamples cells, the sessions
    of each arm (once per metric, so a session's errors stay together) and the tasks of each cell (the measured
    truth is re-estimated).
- Decision: H1 is supported only if p < 0.05 and the CI's upper bound < 0.
- Breadth across behaviors is claimed only if, in addition, D < 0 in at least 3 of the 4 metrics and D stays < 0
  when any single metric is left out.
- Also reported:
  - per metric, D with an exact sign-flip p over its cells and Holm adjustment over metrics;
  - a sign test over cells (F better vs N better by more than 1 point);
  - each arm against the no-change answer (the measured baseline).
- Limitation stated in advance: cells within a metric share designer sessions, so the cell-level tests treat the four
  behaviors as fixed. The H1 claim is over models and conditions within these behaviors. Generalization to other
  behaviors rests on the breadth rule, not on a p-value.
"""

from __future__ import annotations

import json
import math
import random
import re
import statistics
import sys
from collections import defaultdict
from itertools import combinations, product
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


def primary(s: dict, truth: dict, preds: list[tuple[str, str, dict]]) -> dict:
    """The pre-registered F vs N test described in the module docstring."""
    rev = {v: k for k, v in COND.items()}

    def num(p: dict, model: str, c: str) -> float | None:
        try:
            return float(p[model][c]["score"])
        except (KeyError, TypeError, ValueError):
            return None

    sess = defaultdict(list)
    for arm, metric, p in preds:
        need = [(m, rev[c]) for mt, m, c in truth if mt == metric]
        if arm in ("F", "N") and all(num(p, m, c) is not None for m, c in need):
            sess[(arm, metric)].append(p)
    all_metrics = sorted({k[0] for k in truth})
    metrics = [mt for mt in all_metrics if len(sess[("F", mt)]) >= 2 and len(sess[("N", mt)]) >= 2]
    cells = []
    for mt, m, cond in sorted(truth):
        if mt in metrics:
            base, cnd = s[(mt, m, "baseline")], s[(mt, m, cond)]
            cells.append({"metric": mt, "model": m, "condition": cond,
                          "pairs": [(base[t], cnd[t]) for t in sorted(set(base) & set(cnd))],
                          "F": [num(p, m, rev[cond]) for p in sess[("F", mt)]],
                          "N": [num(p, m, rev[cond]) for p in sess[("N", mt)]]})
    res = {"metrics": metrics, "excluded_metrics": [mt for mt in all_metrics if mt not in metrics],
           "sessions": {f"{a}|{mt}": len(sess[(a, mt)]) for a in ("F", "N") for mt in all_metrics},
           "cells": len(cells)}
    if not cells:
        return res

    def cell_d(c: dict, f: list[float], n: list[float], pairs: list[tuple[float, float]]) -> tuple[float, ...]:
        ts = statistics.fmean(y for _, y in pairs)
        ef = 100 * statistics.fmean(abs(p - ts) for p in f)
        en = 100 * statistics.fmean(abs(p - ts) for p in n)
        e0 = 100 * abs(statistics.fmean(b for b, _ in pairs) - ts)
        return ef - en, ef - e0, en - e0

    obs = [cell_d(c, c["F"], c["N"], c["pairs"]) for c in cells]
    d = [o[0] for o in obs]
    big_d = statistics.fmean(d)
    r = random.Random(11)
    hits = sum(statistics.fmean(x if r.random() < 0.5 else -x for x in d) <= big_d + 1e-12 for _ in range(100_000))
    by_m = defaultdict(list)
    for c, o in zip(cells, obs, strict=True):
        by_m[c["metric"]].append((c, o))
    boot = []
    for _ in range(10_000):
        vals = []
        for mt, co in by_m.items():
            fi = [r.randrange(len(sess[("F", mt)])) for _ in sess[("F", mt)]]
            ni = [r.randrange(len(sess[("N", mt)])) for _ in sess[("N", mt)]]
            for c, _ in (r.choice(co) for _ in co):
                vals.append(cell_d(c, [c["F"][i] for i in fi], [c["N"][i] for i in ni],
                                   [r.choice(c["pairs"]) for _ in c["pairs"]]))
        boot.append([statistics.fmean(v[k] for v in vals) for k in range(3)])

    def ci(k: int) -> list[float]:
        xs = sorted(b[k] for b in boot)
        return [xs[249], xs[9749]]

    per = {}
    for mt, co in by_m.items():
        dm = [o[0] for _, o in co]
        mean_m = statistics.fmean(dm)
        flips = list(product((1, -1), repeat=len(dm)))  # at most 15 cells per metric by design: 32,768 sign vectors
        per[mt] = {"cells": len(dm), "D": mean_m,
                   "p_exact": sum(statistics.fmean(x * sg for x, sg in zip(dm, signs, strict=True)) <= mean_m + 1e-12
                                  for signs in flips) / len(flips)}
    run = 0.0
    for i, mt in enumerate(sorted(per, key=lambda k: per[k]["p_exact"])):
        run = max(run, min(1.0, (len(per) - i) * per[mt]["p_exact"]))
        per[mt]["holm_p"] = run
    better, worse = sum(x < -1 for x in d), sum(x > 1 for x in d)
    k, n = min(better, worse), better + worse
    lomo = {mt: statistics.fmean(x for c, x in zip(cells, d, strict=True) if c["metric"] != mt)
            for mt in per if len(per) > 1}
    p_flip = (hits + 1) / 100_001
    supported = p_flip < 0.05 and ci(0)[1] < 0
    res.update({
        "D": big_d, "p_one_sided": p_flip, "ci95": ci(0), "H1_supported": supported,
        "breadth_supported": supported and sum(v["D"] < 0 for v in per.values()) >= 3 and len(per) == 4
        and all(v < 0 for v in lomo.values()),
        "per_metric": per, "leave_one_metric_out_D": lomo,
        "sign_test": {"F_better": better, "N_better": worse,
                      "p_two_sided": min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n) if n else None},
        "F_minus_no_change": {"D": statistics.fmean(o[1] for o in obs), "ci95": ci(1)},
        "N_minus_no_change": {"D": statistics.fmean(o[2] for o in obs), "ci95": ci(2)},
        "cell_d": [{"metric": c["metric"], "model": c["model"], "condition": c["condition"], "d": x}
                   for c, x in zip(cells, d, strict=True)]})
    return res


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
    sessions, preds = [], []
    for d in sorted(answers.glob("*-*-*")):
        arm, metric, _ = d.name.split("-", 2)
        f = d / "answer.txt"
        pred = parse(f.read_text()) if f.exists() else None
        if not pred:
            continue
        preds.append((arm, metric, pred))
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
    prim = primary(s, truth, preds)
    out.write_text(json.dumps({"truth": {"|".join(k): v for k, v in truth.items()}, "sessions": sessions,
                               "summary": summary, "no_change": nochange, "primary": prim}, indent=1))
    for k, v in summary.items():
        print(k, {kk: round(vv, 3) if isinstance(vv, float) else vv for kk, vv in v.items()})
    print("no-change:", {m: {k: round(x, 3) for k, x in v.items()} for m, v in nochange.items()})
    print("primary:", {k: v for k, v in prim.items() if k not in ("cell_d", "per_metric")})


if __name__ == "__main__":
    main()
