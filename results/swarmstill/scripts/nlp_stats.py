"""Statistical and text measures from released traces, our Qwen3-4B runs, and designer outputs.

Usage: python nlp_stats.py <op_dir> <pulled_dir> <ec.py dir> <results_dir>
1. Feedback transitions: P(ACCEPT at t+1 | own verdict at t and whether the verdict review said it was right).
2. Onset-aligned language: share of private reflections / episode messages using four lexicons, by episode
   relative to the trajectory's first mutual ACCEPT.
3. Designer explanations: which causes designers name when predicting interventions.
Bootstrap 95% CIs resample trajectories (2000 draws).
"""
import json, pathlib, random, re, statistics, sys, glob
import pyarrow.parquet as pq
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

op, pulled, ecdir, res = (pathlib.Path(a) for a in sys.argv[1:5])
sys.path.insert(0, str(ecdir))
import ec  # noqa: E402
rng = random.Random(11)
B = 2000


def boot(groups):
    """groups: list of per-trajectory lists of 0/1 outcomes -> (mean, lo, hi, n)."""
    flat = [x for g in groups for x in g]
    if not flat:
        return None
    st = []
    for _ in range(B):
        s = [rng.choice(groups) for _ in groups]
        f = [x for g in s for x in g]
        if f:
            st.append(sum(f) / len(f))
    st.sort()
    return {"p": 100 * sum(flat) / len(flat), "lo": 100 * st[int(.025 * len(st))], "hi": 100 * st[int(.975 * len(st)) - 1], "n": len(flat)}


# ---------------------------------------------------------------- 1. feedback transitions
def released(cond):
    rows = [r for r in pq.read_table(op / "agents" / f"{cond}.parquet").to_pylist() if not r["is_warmup"]]
    traj = {}
    for r in rows:
        traj.setdefault(r["sequence_id"], {}).setdefault(r["agent"], []).append(r)
    out = []
    for s, ag in traj.items():
        for a, eps in ag.items():
            eps.sort(key=lambda r: r["evaluation_episode"])
            out.append((s, [(r["verdict"], r["verdict_correct"]) for r in eps]))
    return out


def ours(cond_dir):
    out = []
    for f in sorted(glob.glob(str(cond_dir / "seq*/*/run.json"))):
        run = json.load(open(f))
        eps = [r for r, t in zip(run["results"], ec.throttle_policies(run)) if ec.is_constrained(r, t)]
        for a in ec.AGENTS:
            out.append((f, [(ec._verdict(r["agents"][a]), r["agents"][a].get("verdict_correct")) for r in eps]))
    return out


def transitions(seqs):
    cells = {k: {} for k in ("reject_wrong", "reject_right", "accept_right", "accept_wrong")}
    for tid, vs in seqs:
        for (v, c), (v2, _) in zip(vs, vs[1:]):
            if v not in ("accept", "reject") or c is None:
                continue
            key = f"{v}_{'right' if c else 'wrong'}"
            cells[key].setdefault(tid, []).append(1 if v2 == "accept" else 0)
    return {k: boot(list(d.values())) for k, d in cells.items()}


MODELS = [("Gemini 3.1 Flash-Lite", released("main__gemini-3-1-flash-lite")),
          ("Gemini 3.1 Flash-Lite, no verdict review", released("feedback__gemini-3-1-flash-lite__no-verdict-review")),
          ("Gemini 3.7 Flash", released("main__gemini-3-7-flash")),
          ("Qwen3.8-27B", released("main__qwen-3-8-27b")),
          ("Qwen3.6-27B", released("main__qwen-3-6-27b")),
          ("Gemma-4-31B", released("main__gemma-4-31b-it")),
          ("Qwen3-4B (ours, partial)", ours(pulled / "ref" / "main"))]
trans = {name: transitions(s) for name, s in MODELS}

# ---------------------------------------------------------------- 2. onset-aligned language
LEX = {"Rule / raw log": r"raw[- ]?log|complete (raw )?log|verdict policy|\bpolicy\b|verbatim|omission|complian",
       "Pragmatic exception": r"pragmatic|practical(ly)?|in practice|technically impossible|substance over|spirit of|reasonable to accept|trust",
       "Feedback / past episodes": r"feedback|previous episode|last episode|earlier episode|prior episode|lesson|verdict review",
       "Agreement": r"\bagree|\bdeal\b|both accept|accept each other|mutual(ly)? accept|accept (your|each)"}
LEXR = {k: re.compile(v, re.I) for k, v in LEX.items()}
runs = pq.read_table(op / "runs.parquet").to_pylist()


def event_study(cond, agents_file, episodes_file):
    onset = {r["sequence_id"]: r["onset_evaluation_episode"] for r in runs if r["condition_id"] == cond}
    refl = [r for r in pq.read_table(op / "agents" / agents_file).to_pylist() if not r["is_warmup"]]
    msgs = [r for r in pq.read_table(episodes_file).to_pylist() if not r["is_warmup"]]
    out = {"reflections": {}, "messages": {}}
    for src, rows in (("reflections", refl), ("messages", msgs)):
        for k, rx in LEXR.items():
            per_rel = {}
            for r in rows:
                o = onset.get(r["sequence_id"])
                if not o:
                    continue
                rel = r["evaluation_episode"] - o
                if rel < -4 or rel > 3:
                    continue
                text = (r["reflection"] or "") if src == "reflections" else " ".join(m["content"] for m in (r["communication"] or []))
                per_rel.setdefault(rel, {}).setdefault(r["sequence_id"], []).append(1 if rx.search(text) else 0)
            out[src][k] = {rel: boot(list(d.values())) for rel, d in sorted(per_rel.items())}
    return out


ES = {"Gemini 3.1 Flash-Lite": event_study("main__gemini-3-1-flash-lite", "main__gemini-3-1-flash-lite.parquet",
                                           op / "parquet" / "main__gemini-3-1-flash-lite.parquet"),
      "Gemini 3.7 Flash": event_study("main__gemini-3-7-flash", "main__gemini-3-7-flash.parquet",
                                      op / "parquet37" / "main__gemini-3-7-flash.parquet")}

# ---------------------------------------------------------------- 3. designer explanations
CAUSE = {"reward / incentive": r"reward|incentiv|pay|paid|payoff|maximi[sz]|dominant strategy|points?\b",
         "feedback / being told": r"feedback|verdict review|told|correctness signal|learn(s|ed)? that|judged|marked wrong",
         "memory / history": r"memory|history|carry|precedent|remember|earlier episode|prior episode"}
CR = {k: re.compile(v, re.I) for k, v in CAUSE.items()}
expl = {}
for d in sorted(glob.glob(str(res / "sessions" / "*"))):
    name = pathlib.Path(d).name
    t = open(pathlib.Path(d) / "stage2.txt").read()
    m = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", t, re.S) or [t[t.find("{"): t.rfind("}") + 1]]
    try:
        p = json.loads(m[-1])
    except Exception:
        continue
    for iid, v in p.items():
        mech = str(v.get("mechanism", ""))
        expl.setdefault(name, []).append({k: bool(rx.search(mech)) for k, rx in CR.items()} | {"iid": iid, "rate": v.get("rate")})
groups = {}
for name, items in expl.items():
    g = ("traces" if "-ev-" in name else "no traces")
    groups.setdefault(g, []).extend(items)
cause_share = {g: {k: 100 * statistics.mean(i[k] for i in items) for k in CAUSE} | {"n": len(items)} for g, items in groups.items()}
# Interventions whose true mechanism is feedback/rationale removal (I5-I8): what do designers blame?
frag = [i for items in expl.values() for i in items if i["iid"] in ("I5", "I6", "I7", "I8")]
frag_share = {k: 100 * statistics.mean(i[k] for i in frag) for k in CAUSE} | {"n": len(frag)}

(res / "nlp_stats.json").write_text(json.dumps({"transitions": trans, "event_study": ES, "designer_causes": cause_share,
                                                "designer_causes_I5_I8": frag_share, "lexicons": LEX, "causes": CAUSE}, indent=1))

# ---------------------------------------------------------------- figures
INK, INK2, MUTED, GRID, SURF = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#fcfcfb"
BLUE, ORANGE, AQUA, YELLOW = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
plt.rcParams.update({"font.size": 9, "axes.edgecolor": MUTED, "axes.labelcolor": INK2, "xtick.color": INK2,
                     "ytick.color": INK2, "axes.spines.top": False, "axes.spines.right": False,
                     "figure.facecolor": SURF, "axes.facecolor": SURF, "savefig.facecolor": SURF})
fig, ax = plt.subplots(figsize=(7.2, 3.8))
names = [n for n, _ in MODELS]
ys = list(range(len(names)))[::-1]
for y, n in zip(ys, names):
    for key, color, off, lab in (("reject_wrong", ORANGE, 0.14, "After a REJECT judged wrong"),
                                 ("reject_right", BLUE, -0.14, "After a REJECT judged right")):
        c = trans[n][key]
        if not c:
            continue
        ax.errorbar(c["p"], y + off, xerr=[[c["p"] - c["lo"]], [c["hi"] - c["p"]]], fmt="o", color=color, ms=6,
                    ecolor=color, elinewidth=1.2, capsize=0, label=lab if n == names[0] else None)
        ax.text(c["hi"] + 1.5, y + off, f"{c['p']:.0f}% (n={c['n']})", va="center", fontsize=7, color=INK2)
ax.set_yticks(ys, names, fontsize=8)
ax.set_xlim(0, 115); ax.set_xlabel("Agents who ACCEPT in the next episode (%, 95% CI)")
ax.grid(axis="x", color=GRID, lw=0.6)
ax.legend(loc="upper center", bbox_to_anchor=(0.45, -0.16), ncol=2, fontsize=7.5, frameon=False)
fig.tight_layout(); fig.savefig(res / "figure4_feedback_transitions.png", dpi=200); plt.close(fig)

fig, axes = plt.subplots(1, 2, figsize=(7.6, 3.3), sharey=True)
for ax, (model, es) in zip(axes, ES.items()):
    for (k, color) in zip(LEX, (MUTED, ORANGE, BLUE, AQUA)):
        pts = es["reflections"][k]
        xs = sorted(x for x in pts if pts[x]["n"] >= 10); ys_ = [pts[x]["p"] for x in xs]  # drop points with n<10
        ax.plot(xs, ys_, "-o", color=color, lw=2, ms=4, label=k)
    ax.axvline(0, color=INK2, lw=1, ls="--")
    ax.set_title(model, fontsize=9, color=INK, loc="left")
    ax.set_xlabel("Episode relative to first mutual ACCEPT")
    ax.grid(color=GRID, lw=0.6)
axes[0].set_ylabel("Private reflections using the term (%)")
h, l = axes[0].get_legend_handles_labels()
fig.legend(h, l, loc="lower center", ncol=4, fontsize=7.5, frameon=False)
fig.tight_layout(rect=(0, 0.07, 1, 1)); fig.savefig(res / "figure5_language_event_study.png", dpi=200); plt.close(fig)

# ---------------------------------------------------------------- print
print("== 1. P(ACCEPT next episode)")
for n in names:
    t = trans[n]
    f = lambda c: f"{c['p']:5.1f} [{c['lo']:.0f},{c['hi']:.0f}] n={c['n']}" if c else "  -  "
    print(f"{n:42s} REJECT-wrong {f(t['reject_wrong'])} | REJECT-right {f(t['reject_right'])} | ACCEPT-right {f(t['accept_right'])}")
print("== 2. Flash-Lite reflections by episode relative to onset (rel: %)")
for k in LEX:
    pts = ES["Gemini 3.1 Flash-Lite"]["reflections"][k]
    print(f"  {k:26s}", "  ".join(f"{rel:+d}:{v['p']:4.0f}" for rel, v in pts.items()))
print("== 3. Designer explanations naming each cause (%)", json.dumps(cause_share), "\n   I5-I8:", json.dumps(frag_share))
