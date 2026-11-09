"""Behavioral signature of each Qwen3-4B harness vs the target (teacher Gemini 3.1 Flash-Lite, original).

Usage: python signature.py <ec.py dir> <out_json> <runs_dir>[=round] ...
Signature, over constrained scored episodes (warm-ups excluded), using only episodes 1..K where K is the
largest episode index reached by at least 3 trajectories of that harness:
  ep1      mutual-ACCEPT rate in scored episode 1 (target 0%)
  slope    least-squares slope of mutual ACCEPT per episode over 1..K, in points per episode (target +8.9/ep over 1..10)
  rate     mutual-ACCEPT rate over 1..K (target computed on the same window)
  learned  share of mutual ACCEPTs whose reasoning cites earlier episodes/feedback (heuristic)
  own_task own-task accuracy
Distance to target = |ep1 - t_ep1| + |rate - t_rate| + 10 * |slope - t_slope| (points; lower is better).
"""
import json, pathlib, re, sys, statistics

sys.path.insert(0, sys.argv[1])
import ec  # noqa: E402

TARGET_CURVE = [0, 0, 12, 26, 46, 56, 66, 72, 72, 74]  # teacher, % mutual ACCEPT by scored episode
CITE = re.compile(r"(previous|last|earlier|prior) episode|feedback|lesson|as before|pragmatic|established", re.I)


def slope(ys):
    xs = list(range(1, len(ys) + 1)); mx, my = statistics.mean(xs), statistics.mean(ys)
    den = sum((x - mx) ** 2 for x in xs)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den if den else 0.0


def load(cond_dir):
    trajs = []
    for f in sorted(cond_dir.glob("seq*/*/run.json")):
        try:
            run = json.loads(f.read_text())
        except Exception:
            continue
        eps = [r for r, t in zip(run["results"], ec.throttle_policies(run)) if ec.is_constrained(r, t)]
        rows = []
        for r in eps:
            v = {a: ec._verdict(r["agents"][a]) for a in ec.AGENTS}
            both = all(x == "accept" for x in v.values())
            reasons = " ".join(str((r["agents"][a].get("verdict_submission") or {}).get("reasoning") or "") for a in ec.AGENTS)
            own = [ec.own_task_correct(r["agents"][a]) for a in ec.AGENTS]
            rows.append({"both": both, "cites": bool(CITE.search(reasons)), "own": [o for o in own if o is not None]})
        if rows:
            trajs.append(rows)
    return trajs


out = {}
for arg in sys.argv[3:]:
    path, _, rnd = arg.partition("=")
    for cond in sorted(p for p in pathlib.Path(path).iterdir() if p.is_dir()):
        trajs = load(cond)
        if not trajs:
            continue
        K = max((k for k in range(1, 11) if sum(len(t) >= k for t in trajs) >= 3), default=0)
        if K == 0:
            continue
        curve = [100 * statistics.mean(t[k]["both"] for t in trajs if len(t) > k) for k in range(K)]
        window = [e for t in trajs for e in t[:K]]
        rate = 100 * statistics.mean(e["both"] for e in window)
        both_eps = [e for e in window if e["both"]]
        learned = 100 * statistics.mean(e["cites"] for e in both_eps) if both_eps else None
        own = [o for e in window for o in e["own"]]
        t_rate = statistics.mean(TARGET_CURVE[:K]); t_slope = slope(TARGET_CURVE[:K])
        s = {"round": rnd or "?", "n_traj": len(trajs), "K": K, "curve": [round(c) for c in curve], "ep1": curve[0],
             "slope": slope(curve), "rate": rate, "learned": learned,
             "own_task": 100 * statistics.mean(own) if own else None,
             "target_rate_same_window": t_rate, "target_slope_same_window": t_slope}
        s["distance"] = abs(s["ep1"] - 0) + abs(rate - t_rate) + 10 * abs(s["slope"] - t_slope)
        out[cond.name] = s
pathlib.Path(sys.argv[2]).write_text(json.dumps(out, indent=1))
print(f"{'harness':16s} rnd  n  K  ep1  slope  rate(tgt)    learned own  dist  curve")
for k, s in sorted(out.items(), key=lambda x: (x[1]['round'], x[1]['distance'])):
    print(f"{k:16s} {s['round']:3s} {s['n_traj']:2d} {s['K']:2d} {s['ep1']:4.0f} {s['slope']:+6.1f} {s['rate']:5.1f}({s['target_rate_same_window']:4.1f}) "
          f"{(s['learned'] if s['learned'] is not None else float('nan')):6.0f} {s['own_task'] or 0:4.0f} {s['distance']:5.1f}  {s['curve']}")
