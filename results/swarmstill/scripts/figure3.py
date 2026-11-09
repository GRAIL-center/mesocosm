"""Figure 3: framework test on the weaker model's fragile interventions.
Usage: python figure3.py <op_dir> <out_png>
Rows: interventions where the weaker model (Gemini 3.1 Flash-Lite) collapses. Markers: mean prediction per arm
(GPT-6.1 Sol designers, 3 sessions each), small dots = sessions; black diamond = released result.
"""
import json, pathlib, re, statistics, sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

op, out = pathlib.Path(sys.argv[1]), sys.argv[2]
truth = json.loads((op / "ft" / "truth.json").read_text())
INK, INK2, MUTED, GRID, SURF = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#fcfcfb"
ARMS = [("C_none", "No model information", "#898781"), ("B_profile", "Model profile", "#2a78d6"),
        ("A_framework", "Profile + written guide", "#eb6834"), ("D_probe", "Profile + two measured probes", "#1baf7a")]
ITEMS = [("I6", "Remove verdict feedback (probe shown in D)"), ("I5", "Hide the reward"),
         ("I7", "Separate rewards"), ("I8", "Pay for every ACCEPT + no verdict feedback")]
plt.rcParams.update({"font.size": 9, "axes.edgecolor": MUTED, "axes.labelcolor": INK2, "xtick.color": INK2,
                     "ytick.color": INK2, "axes.spines.top": False, "axes.spines.right": False,
                     "figure.facecolor": SURF, "axes.facecolor": SURF, "savefig.facecolor": SURF})


def parse(t):
    for b in reversed(re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", t, re.S) or [t[t.find("{"): t.rfind("}") + 1]]):
        try:
            d = json.loads(b)
            if "M1" in d:
                return d
        except Exception:
            pass


preds = {a: [] for a, _, _ in ARMS}
for d in sorted((op / "distill").glob("ft-*")):
    p = parse((d / "answer.txt").read_text()) if (d / "answer.txt").exists() else None
    arm = d.name.split("-")[1]
    if p and arm in preds:
        preds[arm].append(p)
fig, ax = plt.subplots(figsize=(7.2, 3.9))
ys = {it: len(ITEMS) - k for k, (it, _) in enumerate(ITEMS)}
offs = [0.27, 0.09, -0.09, -0.27]
for (arm, label, color), off in zip(ARMS, offs):
    for it, _ in ITEMS:
        vals = [float(p["M1"][it]["rate"]) for p in preds[arm]]
        if not vals:
            continue
        y = ys[it] + off
        ax.scatter(vals, [y] * len(vals), s=10, color=color, alpha=0.35, linewidths=0, zorder=2)
        ax.scatter([statistics.mean(vals)], [y], s=44, color=color, zorder=4, label=label if it == ITEMS[0][0] else None)
for it, _ in ITEMS:
    ax.scatter([truth["M1"]["iv"][it]["ec"]], [ys[it]], marker="D", s=40, color=INK, zorder=5,
               label="Released result" if it == ITEMS[0][0] else None)
ax.axvline(truth["M1"]["baseline"], color=INK2, lw=1, ls="--", zorder=1)
ax.text(truth["M1"]["baseline"] + 0.8, len(ITEMS) + 0.55, "unchanged 42.4%", fontsize=7.5, color=INK2)
ax.set_yticks([ys[it] for it, _ in ITEMS], [lab for _, lab in ITEMS], fontsize=8)
ax.set_xlim(-2, 100); ax.set_ylim(0.5, len(ITEMS) + 0.8)
ax.set_xlabel("Predicted episode collusion for the weaker model (%)")
ax.grid(axis="x", color=GRID, lw=0.6, zorder=0)
ax.legend(loc="upper center", bbox_to_anchor=(0.42, -0.17), ncol=3, fontsize=7.5, frameon=False)
fig.tight_layout()
fig.savefig(out, dpi=200)
print("saved", out)
