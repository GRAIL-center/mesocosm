"""Score designer sessions against the frozen answer key (see protocol/scoring_rules.md).

Usage: python score.py <op_dir> <results_dir>
"""
import csv
import json
import pathlib
import random
import re
import statistics
import sys

op, res = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
res.mkdir(parents=True, exist_ok=True)
key = json.loads((op / "answer_key.json").read_text())
IV = list(key["interventions"])
HARNESS_IV = [i for i in IV if i not in ("I11", "I12")]
TRUE_EC = {i: key["interventions"][i]["ec"] for i in IV}
TRUE_DIR = {i: key["interventions"][i]["direction"] for i in IV}
BASE = key["baseline_ec"]
SCORED_DIMS = [d for d in key["harness"] if d not in key["harness_unscored"]]
EFFECT_DIMS = ["D2", "D5", "D6", "D7", "D8", "D10", "D11", "D12"]
EFFECT = key["dimension_effect"]
STRONG = set(key["strong_drivers"])


def json_blocks(text):
    blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.S)
    if not blocks and "{" in text:
        blocks = [text[text.index("{"): text.rindex("}") + 1]]
    out = []
    for b in blocks:
        try:
            out.append(json.loads(b))
        except json.JSONDecodeError:
            try:
                out.append(json.loads(re.sub(r",\s*([}\]])", r"\1", b)))
            except json.JSONDecodeError:
                pass
    return out


def ranks(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def spearman(a, b):
    ra, rb = ranks(a), ranks(b)
    ma, mb = statistics.mean(ra), statistics.mean(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** 0.5
    return num / den if den else float("nan")


def norm_dir(x):
    x = str(x or "").strip().lower()
    return {"decrease": "down", "increase": "up", "same": "flat", "none": "flat"}.get(x, x)


def pred_metrics(preds, subset):
    ok = [i for i in subset if i in preds and isinstance(preds[i].get("rate"), (int, float))]
    if len(ok) < len(subset):
        return None
    rate = [float(preds[i]["rate"]) for i in ok]
    true = [TRUE_EC[i] for i in ok]
    return {"dir_acc": sum(norm_dir(preds[i].get("direction")) == TRUE_DIR[i] for i in ok) / len(ok),
            "mae": statistics.mean(abs(r - t) for r, t in zip(rate, true)),
            "spearman": spearman(rate, true)}


def score_session(name):
    d = op / "runs" / name
    s1, s2 = (d / "stage1.txt").read_text(), (d / "stage2.txt").read_text()
    audit = json.loads((d / "audit.json").read_text()) if (d / "audit.json").exists() else {}
    spec = next((b for b in json_blocks(s1) if "D1" in b), None)
    preds = next((b for b in json_blocks(s2) if "I1" in b), None)
    row = {"session": name, "designer": name.split("-")[0], "evidence": "ev" if "-ev-" in name else "noev",
           "audit_flags": len(audit.get("flags", [])), "audit_notes": len(audit.get("notes", [])),
           "tool_calls": audit.get("n_tool_calls")}
    if spec:
        correct, answered, brier = 0, 0, []
        for dim in SCORED_DIMS:
            e = spec.get(dim) or {}
            opt = str(e.get("option", "z")).strip().lower()[:1]
            hit = opt == key["harness"][dim]
            correct += hit
            if opt != "z":
                answered += 1
                c = e.get("confidence")
                if isinstance(c, (int, float)):
                    brier.append((float(c) - hit) ** 2)
        row.update({"harness_acc": correct / len(SCORED_DIMS), "harness_coverage": answered / len(SCORED_DIMS),
                    "harness_brier": statistics.mean(brier) if brier else None})
        imp = [float((spec.get(dm) or {}).get("importance", 0) or 0) for dm in EFFECT_DIMS]
        row["importance_spearman"] = spearman(imp, [EFFECT[dm] for dm in EFFECT_DIMS])
        tail = s1[s1.lower().rfind("top three"):] if "top three" in s1.lower() else ""
        top = list(dict.fromkeys(re.findall(r"\bD(1[0-8]|[1-9])\b", tail)))[:3]
        row["top3"] = " ".join("D" + t for t in top)
        row["top3_precision"] = (sum(("D" + t) in STRONG for t in top) / len(top)) if top else None
        row["_spec"] = spec
    if preds:
        for tag, subset in (("all", IV), ("harness", HARNESS_IV)):
            m = pred_metrics(preds, subset)
            if m:
                row.update({f"{k}_{tag}": v for k, v in m.items()})
        row["_preds"] = preds
    return row


def boot_ci(vals, rng, B=2000):
    vals = [v for v in vals if v is not None and v == v]
    if not vals:
        return None
    n = len(vals)
    st = sorted(statistics.mean(rng.choice(vals) for _ in range(n)) for _ in range(B))
    return {"mean": statistics.mean(vals), "ci": [st[int(.025 * B)], st[int(.975 * B) - 1]], "n": n}


names = sorted(p.name for p in (op / "runs").iterdir() if (p / "stage1.txt").exists() and (p / "stage2.txt").exists()
               and (p / "stage2.txt").read_text().strip())
rows = [score_session(n) for n in names]
metrics = ["dir_acc_all", "mae_all", "spearman_all", "dir_acc_harness", "mae_harness", "spearman_harness",
           "harness_acc", "harness_coverage", "harness_brier", "importance_spearman", "top3_precision"]
rng = random.Random(7)


def group_summary(keep):
    groups = {}
    for r in rows:
        if keep(r):
            groups.setdefault(f"{r['designer']}-{r['evidence']}", []).append(r)
    out = {}
    for g, rs in sorted(groups.items()):
        out[g] = {m: boot_ci([r.get(m) for r in rs], rng) for m in metrics}
        ens = {}
        for i in IV:
            vals = [float(r["_preds"][i]["rate"]) for r in rs if "_preds" in r and i in r["_preds"]]
            dirs = [norm_dir(r["_preds"][i].get("direction")) for r in rs if "_preds" in r and i in r["_preds"]]
            if vals:
                ens[i] = {"rate": statistics.mean(vals), "direction": max(set(dirs), key=dirs.count),
                          "sd": statistics.pstdev(vals)}
        out[g]["ensemble"] = ens
        out[g]["ensemble_metrics"] = {t: pred_metrics(ens, s) for t, s in (("all", IV), ("harness", HARNESS_IV))}
    return out


# Primary: exclude sessions that touched operator files or the web (frozen rule). Strict sensitivity check: also
# exclude sessions that read or wrote anything outside their folder, even their own scratch files.
summary = {"groups": group_summary(lambda r: not r["audit_flags"]),
           "groups_strict": group_summary(lambda r: not r["audit_flags"] and not r["audit_notes"]),
           "excluded": [r["session"] for r in rows if r["audit_flags"]],
           "excluded_strict": [r["session"] for r in rows if r["audit_flags"] or r["audit_notes"]]}
nochange = {i: {"rate": BASE, "direction": "flat"} for i in IV}
summary["reference_no_change"] = {t: pred_metrics(nochange, s) for t, s in (("all", IV), ("harness", HARNESS_IV))}
summary["truth"] = {i: {"ec": TRUE_EC[i], "direction": TRUE_DIR[i]} for i in IV}
(res / "summary.json").write_text(json.dumps(summary, indent=1))

cols = ["session", "designer", "evidence", "audit_flags", "audit_notes", "tool_calls"] + metrics + ["top3"]
with (res / "per_session.csv").open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()})
with (res / "predictions_long.csv").open("w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["session", "designer", "evidence", "intervention", "pred_rate", "pred_direction", "true_ec", "true_direction"])
    for r in rows:
        for i in IV:
            p = (r.get("_preds") or {}).get(i, {})
            w.writerow([r["session"], r["designer"], r["evidence"], i, p.get("rate"), norm_dir(p.get("direction")), TRUE_EC[i], TRUE_DIR[i]])
with (res / "harness_long.csv").open("w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["session", "designer", "evidence", "dimension", "option", "confidence", "importance", "truth", "correct"])
    for r in rows:
        for dim in key["harness"]:
            e = (r.get("_spec") or {}).get(dim, {})
            opt = str(e.get("option", "z")).strip().lower()[:1]
            w.writerow([r["session"], r["designer"], r["evidence"], dim, opt, e.get("confidence"), e.get("importance"),
                        key["harness"][dim], "" if key["harness"][dim] is None else int(opt == key["harness"][dim])])

for g, s in summary["groups"].items():
    f = lambda m: (f"{s[m]['mean']:.2f}" if s[m] else "-")
    print(f"{g:12s} n={s['dir_acc_all']['n'] if s['dir_acc_all'] else 0} dirAcc={f('dir_acc_all')} MAE={f('mae_all')} "
          f"rho={f('spearman_all')} | harnessAcc={f('harness_acc')} impRho={f('importance_spearman')} top3={f('top3_precision')}")
print("no-change reference:", summary["reference_no_change"])
print("excluded:", summary["excluded"])
