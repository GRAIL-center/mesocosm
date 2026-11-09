"""Teacher-student overlap of first-5-episode mutual-ACCEPT sequences (re-check of another agent's figure).
Usage: python seq_overlap.py <scratchpad>
Overlap = 100 * sum_s min(p_teacher(s), p_student(s)) over the 32 binary sequences; '00000' part reported separately.
CI: resample harnesses, then trajectories within each harness (4000 draws). Also a never-collude reference.
"""
import collections, json, pathlib, random, statistics, sys
import pandas as pd
S = pathlib.Path(sys.argv[1]); sys.path.insert(0, str(S)); import ec  # noqa: E402
K = 5
t = pd.read_parquet(S / "op/agents/main__gemini-3-1-flash-lite.parquet")
t = t[(~t.is_warmup) & (t.evaluation_episode <= K)]
forced = t.groupby("run_id").verdict_forced.any()
teach_all, teach_nf = [], []
for rid, g in t.groupby("run_id"):
    both = g.groupby("evaluation_episode").verdict.apply(lambda v: int((v == "accept").all() and len(v) == 2))
    if len(both) < K: continue
    s = "".join(str(both[e]) for e in range(1, K + 1))
    teach_all.append(s)
    if not forced[rid]: teach_nf.append(s)
def dist(seqs):
    c = collections.Counter(seqs); n = len(seqs); return {k: v / n for k, v in c.items()}
def overlap(pt, seqs):
    ps = dist(seqs); tot = sum(min(pt.get(k, 0), ps.get(k, 0)) for k in set(pt) | set(ps))
    zero = min(pt.get("00000", 0), ps.get("00000", 0)); return 100 * tot, 100 * zero
def students(pattern):
    out = collections.defaultdict(list)
    for f in sorted((S / "pulled").glob(pattern + "/seq*/*/run.json")):
        try: run = json.loads(f.read_text())
        except Exception: continue
        eps = [r for r, tt in zip(run["results"], ec.throttle_policies(run)) if ec.is_constrained(r, tt)]
        both = [int(all(ec._verdict(r["agents"][a]) == "accept" for a in ec.AGENTS)) for r in eps]
        if len(both) >= K: out[f.parts[-4]].append("".join(map(str, both[:K])))
    return out
rng = random.Random(5)
for tname, tseq in (("teacher all", teach_all), ("teacher non-forced", teach_nf)):
    pt = dist(tseq)
    print(f"\n{tname}: n={len(tseq)}, P(00000)={100*pt.get('00000',0):.0f}%, never-collude overlap={100*pt.get('00000',0):.1f}")
    for g, pat in (("original", "ref/main"), ("round 1", "r1/*"), ("round 2", "r2/*"), ("round 3", "r3/*")):
        hs = students(pat); seqs = [s for v in hs.values() for s in v]
        ov, z = overlap(pt, seqs)
        boot = []
        for _ in range(4000):
            hh = [rng.choice(list(hs.values())) for _ in hs]
            bs = [rng.choice(v) for v in hh for _ in v]
            boot.append(overlap(pt, bs)[0])
        boot.sort()
        sub = statistics.fmean(overlap(pt, rng.sample(seqs, 8))[0] for _ in range(2000)) if len(seqs) >= 8 else float("nan")
        print(f"  {g:9s} harnesses={len(hs)} n={len(seqs):2d} overlap={ov:5.1f} (00000 part {z:4.1f} + other {ov-z:4.1f}) "
              f"95% CI [{boot[100]:.0f}, {boot[3899]:.0f}]  n=8 subsample mean {sub:.1f}")
