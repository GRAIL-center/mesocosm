"""Held-out test A scoring: frozen predictions (arms N, V1, V2) vs measured Qwen3.8-27B runs.

Usage: python q27_score.py <ec.py dir> <q27 runs dir> <op_dir> <out_json> [--min-eps 4]
Truth uses scored episodes 1..K common to each condition (K = min over conditions of the episode index reached by
>= 3 trajectories, capped at 10), so partial runs compare like with like. Predictions are converted to relative
change from the paper's 82% baseline and compared with the measured relative change from our own baseline run.
Direction truth: paired bootstrap over sequence ids of the episode-level difference (down / flat / up).
"""
import json, pathlib, random, re, statistics, sys

sys.path.insert(0, sys.argv[1])
import ec  # noqa: E402

runs_dir, op, out = pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3]), pathlib.Path(sys.argv[4])
MIN_EPS = int(sys.argv[sys.argv.index("--min-eps") + 1]) if "--min-eps" in sys.argv else 4
MAP = {"J1": "no_verdict_review", "J2": "separate", "J3": "mem0", "J4": "no_reward", "J5": "acceptance",
       "J6": "mem3", "J7": "fb_reflection"}
PAPER_BASE = 82.0


def load(cond):
    out = {}
    for f in sorted((runs_dir / cond).glob("seq*/*/run.json")):
        try:
            run = json.loads(f.read_text())
        except Exception:
            continue
        eps = [r for r, t in zip(run["results"], ec.throttle_policies(run)) if ec.is_constrained(r, t)]
        seq = int(re.search(r"seq(\d+)", str(f)).group(1))
        out[seq] = [all(ec._verdict(r["agents"][a]) == "accept" for a in ec.AGENTS) for r in eps]
    return out


data = {c: load(c) for c in ["main", *MAP.values()]}
MIN_TRAJ = int(sys.argv[sys.argv.index("--min-traj") + 1]) if "--min-traj" in sys.argv else 2
reach = {c: max((k for k in range(1, 11) if sum(len(v) >= k for v in d.values()) >= MIN_TRAJ), default=0) for c, d in data.items()}
K = MIN_EPS  # common window; conditions that have not reached it with >= MIN_TRAJ trajectories are left out and reported
if reach["main"] < K:
    sys.exit(f"main not at K={K}: per-condition reach {reach}")
FULL = dict(MAP)
MAP = {j: c for j, c in FULL.items() if reach[c] >= K}
MISSING = sorted(set(FULL) - set(MAP))
if len(MAP) < 3:
    sys.exit(f"only {len(MAP)} conditions scorable at K={K}: reach {reach}")
rate = lambda d: 100 * statistics.mean(x for v in d.values() if len(v) >= K for x in v[:K])
base = rate(data["main"])
rng = random.Random(5)
truth = {}
for j, c in MAP.items():
    seqs = [s for s in data[c] if s in data["main"] and len(data[c][s]) >= K and len(data["main"][s]) >= K]
    diffs = [statistics.mean(data[c][s][:K]) - statistics.mean(data["main"][s][:K]) for s in seqs]
    st = sorted(statistics.mean(rng.choice(diffs) for _ in diffs) for _ in range(2000)) if diffs else [0, 0]
    lo, hi = st[int(.025 * len(st))], st[int(.975 * len(st)) - 1]
    r = rate(data[c])
    truth[j] = {"condition": c, "rate": r, "rel": (r - base) / base if base else 0.0, "n_pairs": len(seqs),
                "direction": "down" if hi < 0 else "up" if lo > 0 else "flat"}


def parse(t):
    for b in reversed(re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", t, re.S) or [t[t.find("{"): t.rfind("}") + 1]]):
        try:
            d = json.loads(b)
            if "J1" in d:
                return d
        except Exception:
            pass


def ranks(x):
    o = sorted(range(len(x)), key=lambda i: x[i]); r = [0.0] * len(x)
    for pos, i in enumerate(o):
        r[i] = pos
    return r


def spearman(a, b):
    ra, rb = ranks(a), ranks(b); ma, mb = statistics.mean(ra), statistics.mean(rb)
    den = (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** .5
    return sum((x - ma) * (y - mb) for x, y in zip(ra, rb)) / den if den else float("nan")


sessions = []
for d in sorted((op / "distill").glob("q27-*")):
    sessions.append(("gpt-6.1-sol", d.name.split("-")[1], d / "answer.txt"))
for f in sorted((op / "q27_adaption").glob("ft-*.txt")):
    p = f.stem.split("-"); sessions.append((p[2], p[1], f))
rows = []
for des, arm, f in sessions:
    p = parse(f.read_text()) if f.exists() else None
    if not p:
        continue
    pr = [(float(p[j]["rate"]) - PAPER_BASE) / PAPER_BASE for j in MAP]
    tr = [truth[j]["rel"] for j in MAP]
    dirs = [str(p[j].get("direction", "")).lower() == truth[j]["direction"] for j in MAP]
    rows.append({"designer": des, "arm": arm, "dir_acc": statistics.mean(dirs),
                 "rel_mae": 100 * statistics.mean(abs(a - b) for a, b in zip(pr, tr)), "spearman": spearman(pr, tr),
                 "pred": {j: p[j]["rate"] for j in MAP}})
summary = {}
for r in rows:
    summary.setdefault(r["arm"], []).append(r)
agg = {a: {"n": len(rs), **{k: statistics.mean(x[k] for x in rs) for k in ("dir_acc", "rel_mae", "spearman")}}
       for a, rs in sorted(summary.items())}
nochange = {"dir_acc": statistics.mean(truth[j]["direction"] == "flat" for j in MAP),
            "rel_mae": 100 * statistics.mean(abs(truth[j]["rel"]) for j in MAP)}
out.write_text(json.dumps({"K": K, "scored": sorted(MAP), "missing": MISSING, "reach": reach, "min_traj": MIN_TRAJ, "baseline_rate": base, "truth": truth, "arms": agg, "no_change": nochange, "sessions": rows}, indent=1))
print(f"K={K} episodes; our baseline (main) rate {base:.1f}%; scored {sorted(MAP)}; missing {MISSING}")
for j, t in truth.items():
    print(f"  {j} {t['condition']:18s} {t['rate']:5.1f}% rel {100*t['rel']:+6.1f}% {t['direction']} (pairs {t['n_pairs']})")
for a, s in agg.items():
    print(f"arm {a:3s} n={s['n']} dir_acc={s['dir_acc']:.2f} rel_MAE={s['rel_mae']:.1f} rho={s['spearman']:.2f}")
print("no-change reference:", {k: round(v, 2) for k, v in nochange.items()})
