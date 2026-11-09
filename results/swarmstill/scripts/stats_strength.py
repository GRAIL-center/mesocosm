"""Exact tests for the two SwarmStill headline claims, with the dependence structure made explicit.
Usage: python stats_strength.py <scratchpad> <results dir> <out.json>

Test A (held-out Qwen3.8-27B): session-level exact permutation tests (plain and stratified by designer family),
item-level exact Wilcoxon signed-rank over the 7 interventions, and leave-one-intervention-out differences.
Round comparison (Qwen3-4B, K=5): Fisher exact on episode-1, and harness-cluster bootstrap / harness-level
permutation, since trajectories share a harness. Holm correction over the reported family of tests.
"""
import itertools
import json
import math
import pathlib
import random
import statistics
import sys

S, RES, OUT = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3])
out = {}

# ---------- Test A ----------
qa = json.loads((RES / "q27_score_K10.json").read_text())
items = qa["scored"]
truth = {j: qa["truth"][j]["rate"] for j in items}
base = qa["baseline_rate"]
sess = [s for s in qa["sessions"] if s["arm"] in ("N", "V1", "V2")]
for s in sess:
    s["err"] = {j: abs(s["pred"][j] - truth[j]) for j in items}
    s["abs_mae"] = statistics.mean(s["err"].values())


def perm_p(a, b, strata=None):
    """One-sided exact p for mean(a) < mean(b) by relabelling sessions (within strata if given)."""
    obs = statistics.mean(a["v"]) - statistics.mean(b["v"])
    pool = a["rows"] + b["rows"]
    groups = {}
    for r in pool:
        groups.setdefault(r["fam"] if strata else 0, []).append(r)
    na = {g: sum(1 for r in a["rows"] if (r["fam"] if strata else 0) == g) for g in groups}
    splits = [list(itertools.combinations(range(len(rs)), na[g])) for g, rs in groups.items()]
    keys = list(groups)
    hit = tot = 0
    for combo in itertools.product(*splits):
        A, B = [], []
        for g, idx in zip(keys, combo):
            rs = groups[g]
            A += [rs[i]["v"] for i in idx]
            B += [rs[i]["v"] for i in range(len(rs)) if i not in idx]
        tot += 1
        hit += (statistics.mean(A) - statistics.mean(B)) <= obs + 1e-12
    return obs, hit / tot, tot


def arm(name, key):
    rows = [{"v": s[key], "fam": s["designer"]} for s in sess if s["arm"] == name]
    return {"rows": rows, "v": [r["v"] for r in rows]}


def wilcoxon_exact(d):
    """Two-sided exact signed-rank p (zeros dropped, average ranks for ties)."""
    d = [x for x in d if abs(x) > 1e-9]
    n = len(d)
    order = sorted(range(n), key=lambda i: abs(d[i]))
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and abs(abs(d[order[j + 1]]) - abs(d[order[i]])) < 1e-9:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    wplus = sum(r for r, x in zip(ranks, d) if x > 0)
    dist = [sum(r for r, s in zip(ranks, signs) if s) for signs in itertools.product([0, 1], repeat=n)]
    mean = sum(ranks) / 2
    p = sum(1 for w in dist if abs(w - mean) >= abs(wplus - mean) - 1e-9) / len(dist)
    return {"n_nonzero": n, "W_plus": wplus, "p_two_sided": p}


A = {}
for key in ("rel_mae", "abs_mae"):
    for other in ("N", "V1"):
        obs, p, tot = perm_p(arm("V2", key), arm(other, key))
        obs_s, p_s, tot_s = perm_p(arm("V2", key), arm(other, key), strata=True)
        A[f"{key}: V2 - {other}"] = {"diff": obs, "perm_p_one_sided": p, "n_splits": tot,
                                     "stratified_p_one_sided": p_s, "stratified_splits": tot_s}
per_item = {}
for j in items:
    per_item[j] = {"condition": qa["truth"][j]["condition"], "truth": truth[j]}
    for a in ("N", "V1", "V2"):
        per_item[j][a] = statistics.mean(s["err"][j] for s in sess if s["arm"] == a)
A["per_item_abs_error_pp"] = per_item
d = [per_item[j]["V2"] - per_item[j]["N"] for j in items]
A["item_wilcoxon_V2_vs_N_abs"] = wilcoxon_exact(d)
A["items_V2_better_by_10pp_or_more"] = [per_item[j]["condition"] for j in items if per_item[j]["N"] - per_item[j]["V2"] >= 10]
A["items_V2_worse"] = [per_item[j]["condition"] for j in items if per_item[j]["V2"] > per_item[j]["N"] + 1]
A["leave_one_item_out_abs_diff_V2_minus_N"] = {
    per_item[j]["condition"]: statistics.mean(per_item[k]["V2"] - per_item[k]["N"] for k in items if k != j) for j in items}
A["drop_J1_J5_abs_diff_V2_minus_N"] = statistics.mean(
    per_item[k]["V2"] - per_item[k]["N"] for k in items if k not in ("J1", "J5"))
A["n_sessions"] = {a: sum(1 for s in sess if s["arm"] == a) for a in ("N", "V1", "V2")}
A["n_designer_families"] = len({s["designer"] for s in sess})
A["within_arm_session_sd_abs_mae"] = {a: statistics.pstdev([s["abs_mae"] for s in sess if s["arm"] == a])
                                      for a in ("N", "V1", "V2")}
out["test_A"] = A

# ---------- Round comparison ----------
sys.path.insert(0, str(S))
import ec  # noqa: E402

K = 5


def trajs(pattern):
    res = []
    for f in sorted((S / "pulled").glob(pattern + "/seq*/*/run.json")):
        try:
            run = json.loads(f.read_text())
        except Exception:
            continue
        eps = [r for r, t in zip(run["results"], ec.throttle_policies(run)) if ec.is_constrained(r, t)]
        both = [int(all(ec._verdict(r["agents"][a]) == "accept" for a in ec.AGENTS)) for r in eps]
        if len(both) >= K:
            b = both[:K]
            res.append({"harness": f.parts[-4], "ep1": b[0],
                        "rise": statistics.mean(b[-2:]) - statistics.mean(b[:2]), "rate": statistics.mean(b)})
    return res


data = {"r1": trajs("r1/*"), "r2": trajs("r2/*"), "r3": trajs("r3/*")}


def fisher_two_sided(a, n1, c, n2):
    """a successes of n1 vs c successes of n2."""
    m, N = a + c, n1 + n2

    def pr(x):
        return math.comb(n1, x) * math.comb(n2, m - x) / math.comb(N, m)

    p0 = pr(a)
    return sum(pr(x) for x in range(max(0, m - n2), min(m, n1) + 1) if pr(x) <= p0 * (1 + 1e-9))


def cluster_boot(a, b, metric, B=4000, seed=11):
    rng = random.Random(seed)
    ha, hb = {}, {}
    for t in a:
        ha.setdefault(t["harness"], []).append(t[metric])
    for t in b:
        hb.setdefault(t["harness"], []).append(t[metric])
    la, lb = list(ha.values()), list(hb.values())
    bs = []
    for _ in range(B):
        sa = [x for c in (rng.choice(la) for _ in la) for x in c]
        sb = [x for c in (rng.choice(lb) for _ in lb) for x in c]
        bs.append(statistics.mean(sa) - statistics.mean(sb))
    bs.sort()
    obs = statistics.mean([t[metric] for t in a]) - statistics.mean([t[metric] for t in b])
    return {"diff_pp": 100 * obs, "ci95_pp": [100 * bs[int(.025 * B)], 100 * bs[int(.975 * B) - 1]],
            "n_harness": [len(la), len(lb)]}


def harness_perm(a, b, metric):
    """One-sided exact p that harness means in a are lower than in b (harness = unit)."""
    def means(ts):
        h = {}
        for t in ts:
            h.setdefault(t["harness"], []).append(t[metric])
        return [statistics.mean(v) for v in h.values()]
    ma, mb = means(a), means(b)
    obs = statistics.mean(ma) - statistics.mean(mb)
    pool = ma + mb
    hit = tot = 0
    for idx in itertools.combinations(range(len(pool)), len(ma)):
        A = [pool[i] for i in idx]
        B_ = [pool[i] for i in range(len(pool)) if i not in idx]
        tot += 1
        hit += statistics.mean(A) - statistics.mean(B_) <= obs + 1e-12
    return {"harness_mean_diff_pp": 100 * obs, "p_one_sided": hit / tot, "n_splits": tot}


Rr = {"n_traj": {k: len(v) for k, v in data.items()},
      "n_harness": {k: len({t["harness"] for t in v}) for k, v in data.items()}}
for g in ("r2", "r3"):
    a, b = data[g], data["r1"]
    sa, sb = sum(t["ep1"] for t in a), sum(t["ep1"] for t in b)
    Rr[f"{g} vs r1"] = {
        "ep1_counts": f"{sa}/{len(a)} vs {sb}/{len(b)}",
        "ep1_fisher_p_two_sided": fisher_two_sided(sa, len(a), sb, len(b)),
        "ep1_cluster_boot": cluster_boot(a, b, "ep1"),
        "ep1_harness_perm": harness_perm(a, b, "ep1"),
        "rise_cluster_boot": cluster_boot(a, b, "rise"),
    }
out["rounds_K5"] = Rr

# ---------- Holm over the headline family ----------
fam = {
    "A: V2<N rel_mae (session perm, stratified)": A["rel_mae: V2 - N"]["stratified_p_one_sided"],
    "A: V2<N abs error (item Wilcoxon)": A["item_wilcoxon_V2_vs_N_abs"]["p_two_sided"],
    "R: ep1 r3<r1 (Fisher)": Rr["r3 vs r1"]["ep1_fisher_p_two_sided"],
    "R: ep1 r2<r1 (Fisher)": Rr["r2 vs r1"]["ep1_fisher_p_two_sided"],
    "R: ep1 r3<r1 (harness perm)": Rr["r3 vs r1"]["ep1_harness_perm"]["p_one_sided"],
}
order = sorted(fam, key=fam.get)
m = len(order)
holm, run = {}, 0.0
for i, k in enumerate(order):
    run = max(run, min(1.0, (m - i) * fam[k]))
    holm[k] = {"p": fam[k], "holm_p": run}
out["holm"] = holm
OUT.write_text(json.dumps(out, indent=1))
print(json.dumps(out, indent=1))
