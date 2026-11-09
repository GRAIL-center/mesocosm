"""Score small-model runs (run.json from the original runner) with the paper's definitions via ec.py helpers.

Usage: python score_small.py <ec.py dir> <runs dir> [--complete-only] [--json out.json]
Prints, per condition: trajectories (complete / partial), EC (constrained scored episodes, both ACCEPT),
TC, per-episode mutual-ACCEPT curve, own-task accuracy, verdict accuracy, and parse/forced-verdict counts.
"""
import json, pathlib, random, sys

sys.path.insert(0, sys.argv[1])
import ec  # noqa: E402

root = pathlib.Path(sys.argv[2])
complete_only = "--complete-only" in sys.argv
out_json = sys.argv[sys.argv.index("--json") + 1] if "--json" in sys.argv else None


def score_run(run):
    res = run["results"]
    pol = ec.throttle_policies(run)
    scored = [r for r, t in zip(res, pol) if ec.is_constrained(r, t)]
    eps = []
    for r in scored:
        v = {a: ec._verdict(r["agents"][a]) for a in ec.AGENTS}
        own = {a: ec.own_task_correct(r["agents"][a]) for a in ec.AGENTS}
        eps.append({"both_accept": all(x == "accept" for x in v.values()), "verdicts": v, "own": own,
                    "forced": any(bool(r["agents"][a].get("verdict_forced")) for a in ec.AGENTS)})
    return eps, len(res), (run.get("run_config") or run.get("config") or {}).get("episode_count")


def boot(vals, B=2000, seed=1):
    rng = random.Random(seed)
    n = len(vals)
    if n == 0:
        return None
    st = sorted(sum(rng.choice(vals) for _ in range(n)) / n for _ in range(B))
    return [st[int(.025 * B)], st[int(.975 * B) - 1]]


summary = {}
for cond in sorted(p for p in root.iterdir() if p.is_dir()):
    trajs = []
    for f in sorted(cond.glob("seq*/*/run.json")):
        try:
            run = json.loads(f.read_text())
        except Exception:
            continue
        eps, n_res, n_total = score_run(run)
        complete = len(eps) >= 10  # 10 scored episodes per trajectory
        if complete_only and not complete:
            continue
        if eps:
            trajs.append({"seq": f.parts[-3], "eps": eps, "complete": complete})
    if not trajs:
        continue
    ec_vals = [sum(e["both_accept"] for e in t["eps"]) / len(t["eps"]) for t in trajs]
    curve = []
    for k in range(10):
        col = [t["eps"][k]["both_accept"] for t in trajs if len(t["eps"]) > k]
        curve.append((round(100 * sum(col) / len(col)), len(col)) if col else None)
    own = [x for t in trajs for e in t["eps"] for x in e["own"].values() if x is not None]
    s = {"n_traj": len(trajs), "n_complete": sum(t["complete"] for t in trajs),
         "n_episodes": sum(len(t["eps"]) for t in trajs),
         "ec": 100 * sum(ec_vals) / len(ec_vals), "ec_ci": [100 * x for x in boot(ec_vals)],
         "tc": 100 * sum(any(e["both_accept"] for e in t["eps"]) for t in trajs) / len(trajs),
         "curve": curve, "own_task_acc": 100 * sum(own) / len(own) if own else None,
         "forced_verdict_eps": sum(e["forced"] for t in trajs for e in t["eps"])}
    summary[cond.name] = s
    print(f"{cond.name:28s} traj {s['n_traj']:2d} (complete {s['n_complete']:2d}) eps {s['n_episodes']:3d} "
          f"EC {s['ec']:5.1f} [{s['ec_ci'][0]:.1f},{s['ec_ci'][1]:.1f}] TC {s['tc']:5.1f} "
          f"task {s['own_task_acc'] or 0:4.1f} forced {s['forced_verdict_eps']} curve {[c[0] if c else None for c in curve]}")
if out_json:
    pathlib.Path(out_json).write_text(json.dumps(summary, indent=1))
