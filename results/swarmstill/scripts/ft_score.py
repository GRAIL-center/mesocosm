"""Score framework-test predictions. Usage: python ft_score.py <op_dir> <out_json>
Metrics per session: direction accuracy (20 items), MAE (points), Spearman over 20, and model-difference
accuracy: on interventions whose relative effect differs between the models by >= 0.25 of baseline, did the
prediction get which model reacts more strongly (sign of rel_M2 - rel_M1)?
"""
import json, pathlib, re, statistics, sys

op = pathlib.Path(sys.argv[1]); out = pathlib.Path(sys.argv[2])
truth = json.loads((op / "ft" / "truth.json").read_text())
IV = [f"I{i}" for i in range(1, 11)]
rel = lambda m, rate: (rate - truth[m]["baseline"]) / truth[m]["baseline"]
true_rel = {i: (rel("M1", truth["M1"]["iv"][i]["ec"]), rel("M2", truth["M2"]["iv"][i]["ec"])) for i in IV}
DIFF_ITEMS = [i for i in IV if abs(true_rel[i][1] - true_rel[i][0]) >= 0.25]


def ranks(x):
    o = sorted(range(len(x)), key=lambda i: x[i]); r = [0.0] * len(x); i = 0
    while i < len(o):
        j = i
        while j + 1 < len(o) and x[o[j + 1]] == x[o[i]]:
            j += 1
        for k in range(i, j + 1):
            r[o[k]] = (i + j) / 2
        i = j + 1
    return r


def spearman(a, b):
    ra, rb = ranks(a), ranks(b); ma, mb = statistics.mean(ra), statistics.mean(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** .5
    return num / den if den else float("nan")


def parse(text):
    for b in reversed(re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S) or [text[text.find("{"): text.rfind("}") + 1]]):
        try:
            d = json.loads(b)
            if "M1" in d and "M2" in d:
                return d
        except Exception:
            pass
    return None


rows = []
for d in sorted((op / "distill").glob("ft-*")):
    p = parse((d / "answer.txt").read_text()) if (d / "answer.txt").exists() else None
    if not p:
        rows.append({"session": d.name, "parse_error": True}); continue
    pr, tr, dirs = [], [], []
    for m in ("M1", "M2"):
        for i in IV:
            x = p[m][i]; pr.append(float(x["rate"])); tr.append(truth[m]["iv"][i]["ec"])
            dirs.append(str(x.get("direction", "")).lower() == truth[m]["iv"][i]["direction"])
    diff_ok = []
    for i in DIFF_ITEMS:
        pd = rel("M2", float(p["M2"][i]["rate"])) - rel("M1", float(p["M1"][i]["rate"]))
        td = true_rel[i][1] - true_rel[i][0]
        diff_ok.append((pd > 0) == (td > 0) and abs(pd) > 0.05)
    rows.append({"session": d.name, "arm": d.name.split("-")[1], "dir_acc": statistics.mean(dirs),
                 "mae": statistics.mean(abs(a - b) for a, b in zip(pr, tr)), "spearman": spearman(pr, tr),
                 "diff_acc": statistics.mean(diff_ok),
                 "mae_M1": statistics.mean(abs(a - b) for a, b in zip(pr[:10], tr[:10])),
                 "mae_M2": statistics.mean(abs(a - b) for a, b in zip(pr[10:], tr[10:])),
                 "pred": p})
arms = {}
for r in rows:
    if "arm" in r:
        arms.setdefault(r["arm"], []).append(r)
summary = {a: {k: statistics.mean(r[k] for r in rs) for k in ("dir_acc", "mae", "spearman", "diff_acc", "mae_M1", "mae_M2")}
           | {"n": len(rs)} for a, rs in sorted(arms.items())}
nochange = [truth[m]["baseline"] for m in ("M1", "M2") for _ in IV]
truths = [truth[m]["iv"][i]["ec"] for m in ("M1", "M2") for i in IV]
summary["reference_no_change"] = {"mae": statistics.mean(abs(a - b) for a, b in zip(nochange, truths)),
                                  "dir_acc": statistics.mean(truth[m]["iv"][i]["direction"] == "flat" for m in ("M1", "M2") for i in IV)}
summary["diff_items"] = DIFF_ITEMS
out.write_text(json.dumps({"summary": summary, "sessions": rows}, indent=1))
for a, s in summary.items():
    print(a, s if not isinstance(s, dict) else {k: round(v, 3) if isinstance(v, float) else v for k, v in s.items()})
for r in rows:
    print(r["session"], {k: round(r[k], 2) for k in ("dir_acc", "mae", "spearman", "diff_acc")} if "arm" in r else "PARSE ERROR")
