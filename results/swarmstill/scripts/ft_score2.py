"""Framework test, all designers. Scores only the 8 interventions never shown as probes (I1 and I6 excluded),
so every arm is compared on the same items.

Usage: python ft_score2.py <op_dir> <adaption_dir> <out_json>
Codex answers: op/distill/ft-<arm>-<rep>/answer.txt (designer gpt-6.1-sol).
Adaption answers: <adaption_dir>/ft-<arm>-<designer>-<rep>.txt.
"""
import json, pathlib, re, statistics, sys

op, ad, out = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3])
truth = json.loads((op / "ft" / "truth.json").read_text())
ITEMS = ["I2", "I3", "I4", "I5", "I7", "I8", "I9", "I10"]
rel = lambda m, x: (x - truth[m]["baseline"]) / truth[m]["baseline"]
DIFF = [i for i in ITEMS if abs(rel("M2", truth["M2"]["iv"][i]["ec"]) - rel("M1", truth["M1"]["iv"][i]["ec"])) >= 0.25]
FRAGILE = ["I5", "I7", "I8"]  # M1 collapses to 0-6%


def parse(text):
    blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S) or [text[text.find("{"): text.rfind("}") + 1]]
    for b in reversed(blocks):
        try:
            d = json.loads(b)
            if "M1" in d and "M2" in d:
                return d
        except Exception:
            pass
    return None


def score(p):
    pr, tr, dirs = [], [], []
    for m in ("M1", "M2"):
        for i in ITEMS:
            x = p[m][i]; pr.append(float(x["rate"])); tr.append(truth[m]["iv"][i]["ec"])
            dirs.append(str(x.get("direction", "")).lower() == truth[m]["iv"][i]["direction"])
    diff = [((rel("M2", float(p["M2"][i]["rate"])) - rel("M1", float(p["M1"][i]["rate"]))) > 0) ==
            ((rel("M2", truth["M2"]["iv"][i]["ec"]) - rel("M1", truth["M1"]["iv"][i]["ec"])) > 0) for i in DIFF]
    frag = [float(p["M1"][i]["rate"]) for i in FRAGILE]
    return {"dir_acc": statistics.mean(dirs), "mae": statistics.mean(abs(a - b) for a, b in zip(pr, tr)),
            "diff_acc": statistics.mean(diff), "m1_fragile_pred": statistics.mean(frag)}


rows = []
for d in sorted((op / "distill").glob("ft-*")):
    p = parse((d / "answer.txt").read_text()) if (d / "answer.txt").exists() else None
    rows.append({"designer": "gpt-6.1-sol", "arm": d.name.split("-")[1], "rep": d.name.split("-")[-1], "p": p})
for f in sorted(ad.glob("ft-*.txt")):
    parts = f.stem.split("-")
    rows.append({"designer": parts[2], "arm": parts[1], "rep": parts[3], "p": parse(f.read_text())})
table = {}
for r in rows:
    key = (r["designer"], r["arm"])
    if r["p"] is None:
        table.setdefault(key, {"parse_errors": 0})["parse_errors"] = table.get(key, {}).get("parse_errors", 0) + 1
        continue
    try:
        s = score(r["p"])
    except Exception:
        table.setdefault(key, {"parse_errors": 0})["parse_errors"] = table.get(key, {}).get("parse_errors", 0) + 1
        continue
    table.setdefault(key, {"parse_errors": 0}).setdefault("runs", []).append(s)
res = {}
for (des, arm), v in sorted(table.items()):
    runs = v.get("runs", [])
    agg = {k: statistics.mean(x[k] for x in runs) for k in ("dir_acc", "mae", "diff_acc", "m1_fragile_pred")} if runs else {}
    res[f"{des}|{arm}"] = {"n": len(runs), "parse_errors": v["parse_errors"], **agg}
nochange = statistics.mean(abs(truth[m]["baseline"] - truth[m]["iv"][i]["ec"]) for m in ("M1", "M2") for i in ITEMS)
res["_reference"] = {"no_change_mae": nochange, "items": ITEMS, "diff_items": DIFF,
                     "m1_fragile_truth": statistics.mean(truth["M1"]["iv"][i]["ec"] for i in FRAGILE)}
out.write_text(json.dumps(res, indent=1))
print(f"{'designer|arm':30s} n  err  dirAcc  MAE   diffAcc  M1-fragile-pred")
for k, v in res.items():
    if k.startswith("_"):
        continue
    if v["n"]:
        print(f"{k:30s} {v['n']}  {v['parse_errors']}   {v['dir_acc']:.2f}  {v['mae']:5.1f}  {v['diff_acc']:.2f}    {v['m1_fragile_pred']:5.1f}")
    else:
        print(f"{k:30s} 0  {v['parse_errors']}")
print("reference:", res["_reference"])
