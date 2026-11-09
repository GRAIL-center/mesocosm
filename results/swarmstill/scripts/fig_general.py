"""figure12_generalization.png from acb_analyze.py output.
Usage: python fig_general.py <analysis.json> <out.png>
Top row: measured change in the official score (points) per behavior, condition and model, with 95% CIs.
Bottom: pre-registered primary test. Each dot is one cell's d = |F error| - |N error| (points; below 0 favors the
guide). Bars are per-behavior means; the rightmost marker is the overall D with its hierarchical-bootstrap 95% CI.
"""
import json
import pathlib
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

a = json.loads(pathlib.Path(sys.argv[1]).read_text())
INK, INK2, MUTED, GRID, SURF = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#fcfcfb"
MODELS = [("q17", "Qwen3-1.7B"), ("q4", "Qwen3-4B"), ("q8", "Qwen3-8B"), ("g4", "Gemma-4-E4B"), ("l3", "Llama-3.2-3B")]
COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]  # reference categorical slots 1-5, fixed order
METRICS = [("idr", "Instruction decay", "IDR"), ("rtd", "Tracer durability", "RTD"), ("cpr", "Consensus pollution", "CPR"),
           ("clc", "Cross-task leakage", "CLC")]
CONDS = [("chain", "Chain"), ("full", "Full"), ("specific", "Specific")]
plt.rcParams.update({"font.size": 9, "axes.edgecolor": MUTED, "axes.labelcolor": INK2, "xtick.color": INK2,
                     "ytick.color": INK2, "axes.spines.top": False, "axes.spines.right": False,
                     "figure.facecolor": SURF, "axes.facecolor": SURF, "savefig.facecolor": SURF})
fig = plt.figure(figsize=(7.6, 6.4))
gs = fig.add_gridspec(2, 4, height_ratios=[1, 1.05], hspace=0.55, wspace=0.35)
truth = a["truth"]
vals = [100 * v["change"] for v in truth.values()] + [100 * x for v in truth.values() for x in v["ci"]]
lim = max(10, max(abs(x) for x in vals) * 1.1) if vals else 50
for col, (m, mlabel, _) in enumerate(METRICS):
    ax = fig.add_subplot(gs[0, col])
    ax.axhline(0, color=MUTED, lw=1)
    for j, (mid, _) in enumerate(MODELS):
        for x, (c, _) in enumerate(CONDS):
            t = truth.get(f"{m}|{mid}|{c}")
            if not t:
                continue
            xx = x + (j - 2) * 0.13
            lo, hi = 100 * t["ci"][0], 100 * t["ci"][1]
            ax.plot([xx, xx], [lo, hi], color=COLORS[j], lw=1.5, solid_capstyle="round", zorder=3)
            ax.scatter([xx], [100 * t["change"]], s=22, color=COLORS[j], edgecolor=SURF, lw=1, zorder=4)
    ax.set_xticks(range(3), [c[1] for c in CONDS], fontsize=8)
    ax.set_ylim(-lim, lim)
    ax.grid(axis="y", color=GRID, lw=0.6)
    ax.set_title(mlabel, fontsize=8.5, color=INK, loc="left")
    if col == 0:
        ax.set_ylabel("Measured change (points)")
ax = fig.add_subplot(gs[1, :])
prim = a.get("primary", {})
ax.axhline(0, color=MUTED, lw=1)
if prim.get("cells"):
    ticks = []
    for x, (m, mlabel, short) in enumerate(METRICS):
        ds = [c for c in prim["cell_d"] if c["metric"] == m]
        for c in ds:
            j = [mm for mm, _ in MODELS].index(c["model"])
            off = (j - 2) * 0.07 + {"chain": -0.02, "full": 0.0, "specific": 0.02}[c["condition"]]
            ax.scatter([x + off], [c["d"]], s=16, color=COLORS[j], alpha=0.8, lw=0, zorder=3)
        pm = prim["per_metric"].get(m)
        if pm:
            ax.plot([x - 0.25, x + 0.25], [pm["D"], pm["D"]], color=INK, lw=2, zorder=4)
        ticks.append(f"{short}\nD {pm['D']:+.1f}, Holm p {pm['holm_p']:.2f}" if pm else short)
    lo, hi = prim["ci95"]
    ax.plot([4.2, 4.2], [lo, hi], color=INK, lw=2.5, solid_capstyle="round", zorder=4)
    ax.scatter([4.2], [prim["D"]], s=60, color=INK, zorder=5)
    ax.text(4.4, prim["D"], f"D = {prim['D']:+.1f}\n[{lo:.1f}, {hi:.1f}]\np = {prim['p_one_sided']:.3f}\n"
            f"H1 {'supported' if prim['H1_supported'] else 'not supported'}", va="center", fontsize=7.5, color=INK)
    ax.set_xticks(list(range(4)) + [4.2], ticks + ["All cells"], fontsize=8)
    ax.set_xlim(-0.5, 5.3)
else:
    ax.text(0.5, 0.5, "primary test not available", transform=ax.transAxes, ha="center", color=INK2)
ax.grid(axis="y", color=GRID, lw=0.6)
ax.set_ylabel("Guide error − no-guide error\n(points; below 0 favors the guide)")
handles = [plt.Line2D([], [], marker="o", ls="", color=c, label=l) for c, (_, l) in zip(COLORS, MODELS)]
fig.legend(handles=handles, loc="lower center", ncol=5, frameon=False, fontsize=8, bbox_to_anchor=(0.5, -0.005))
fig.text(0.01, 0.995, "Does the design guide generalize to other behaviors and models?",
         fontsize=10, color=INK, va="top", weight="bold")
fig.text(0.01, 0.965, "Top: measured effect of each change (95% CI over 20 paired tasks). Bottom: pre-registered test, "
         "one dot per cell.", fontsize=8, color=INK2, va="top")
fig.subplots_adjust(left=0.1, right=0.98, top=0.88, bottom=0.12)
fig.savefig(sys.argv[2], dpi=200)
print("wrote", sys.argv[2])
