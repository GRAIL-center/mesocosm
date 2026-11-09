"""Round-level comparison of Qwen3-4B harnesses, pooling trajectories within a round.
Usage: python round_compare.py <ec.py dir> <pulled dir> <out.json> [K=5]
Per trajectory (first K scored episodes; trajectories with < K are excluded):
  ep1     both ACCEPT in scored episode 1 (0/1)
  early   both-ACCEPT rate over episodes 1-2;  late  rate over episodes K-1..K;  rise = late - early
  rate    both-ACCEPT rate over 1..K
Bootstrap 95% CIs resample trajectories within round (2000 draws); differences vs round 1 and vs the reference.
Teacher on episodes 1..5: ep1 0%, early 0%, late 36% (episodes 4-5), rise +36, rate 17%.
"""
import json, pathlib, random, statistics, sys

sys.path.insert(0, sys.argv[1]); import ec  # noqa: E402
pulled, out = pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3])
K = int(sys.argv[4]) if len(sys.argv) > 4 else 5
rng = random.Random(3)
GROUPS = {"reference (original)": ["ref/main"], "round 1": ["r1/*"], "round 2": ["r2/*"], "round 3": ["r3/*"]}


def trajs(pattern):
    res = []
    for f in sorted(pulled.glob(pattern + "/seq*/*/run.json")):
        try:
            run = json.loads(f.read_text())
        except Exception:
            continue
        eps = [r for r, t in zip(run["results"], ec.throttle_policies(run)) if ec.is_constrained(r, t)]
        both = [int(all(ec._verdict(r["agents"][a]) == "accept" for a in ec.AGENTS)) for r in eps]
        if len(both) >= K:
            b = both[:K]
            res.append({"harness": f.parts[-4], "ep1": b[0], "early": statistics.mean(b[:2]), "late": statistics.mean(b[-2:]),
                        "rise": statistics.mean(b[-2:]) - statistics.mean(b[:2]), "rate": statistics.mean(b)})
    return res


def ci(vals):
    if not vals:
        return None
    bs = sorted(statistics.mean(rng.choice(vals) for _ in vals) for _ in range(2000))
    return [100 * statistics.mean(vals), 100 * bs[50], 100 * bs[1949]]


def diff_ci(a, b):
    if not a or not b:
        return None
    bs = sorted(statistics.mean(rng.choice(a) for _ in a) - statistics.mean(rng.choice(b) for _ in b) for _ in range(2000))
    return [100 * (statistics.mean(a) - statistics.mean(b)), 100 * bs[50], 100 * bs[1949]]


data = {g: [t for p in pats for t in trajs(p)] for g, pats in GROUPS.items()}
res = {"K": K, "groups": {}}
for g, ts in data.items():
    res["groups"][g] = {"n_traj": len(ts), "n_harness": len({t["harness"] for t in ts}),
                        **{m: ci([t[m] for t in ts]) for m in ("ep1", "early", "late", "rise", "rate")}}
for g in ("round 2", "round 3"):
    for base in ("round 1", "reference (original)"):
        res.setdefault("diffs", {})[f"{g} - {base}"] = {m: diff_ci([t[m] for t in data[g]], [t[m] for t in data[base]])
                                                        for m in ("ep1", "rise", "rate")}
out.write_text(json.dumps(res, indent=1))
f = lambda c: f"{c[0]:5.1f} [{c[1]:.0f},{c[2]:.0f}]" if c else "   –   "
TC = [0, 0, 12, 26, 46, 56, 66, 72, 72, 74][:K]
teacher = {"ep1": TC[0], "rise": statistics.mean(TC[-2:]) - statistics.mean(TC[:2]), "rate": statistics.mean(TC)}
res["teacher"] = teacher; out.write_text(json.dumps(res, indent=1))
print(f"K={K} scored episodes per trajectory (teacher on the same window: ep1 {teacher['ep1']:.0f}, rise {teacher['rise']:+.0f}, rate {teacher['rate']:.0f})")
print(f"{'group':22s} {'n':>4s} {'ep1':>16s} {'rise (late-early)':>18s} {'rate':>16s}")
for g, r in res["groups"].items():
    print(f"{g:22s} {r['n_traj']:4d} {f(r['ep1']):>16s} {f(r['rise']):>18s} {f(r['rate']):>16s}")
for k, d in res.get("diffs", {}).items():
    print(f"  {k:34s} ep1 {f(d['ep1'])}  rise {f(d['rise'])}  rate {f(d['rate'])}")
