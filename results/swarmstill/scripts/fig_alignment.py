"""figure11_alignment.png: behavior alignment of Qwen3-4B harnesses to the teacher, and its decomposition.
Usage: python fig_alignment.py <alignment.json> <out.png>
Top row: alignment per harness (small dots) and group mean with 95% CI, for the first 5 and first 8 scored episodes.
Bottom row: the three error components (start, level, rise) per group, mean with 95% CI (lower is closer).
"""
import json
import pathlib
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

rep = json.loads(pathlib.Path(sys.argv[1]).read_text())
INK, INK2, MUTED, GRID, SURF = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#fcfcfb"
GROUPS = {"original": ("Qwen3-4B, original settings", "#898781"), "round 1": ("Round 1 (no profile, no guide)", "#2a78d6"),
          "round 2": ("Round 2 (profile + guide v2)", "#eb6834"), "round 3": ("Round 3 (guide v3)", "#1baf7a")}
COMP = [("start", "Start\n(episode 1)"), ("level", "Level\n(overall rate)"), ("rise", "Rise\n(late − early)")]
plt.rcParams.update({"font.size": 9, "axes.edgecolor": MUTED, "axes.labelcolor": INK2, "xtick.color": INK2,
                     "ytick.color": INK2, "axes.spines.top": False, "axes.spines.right": False,
                     "figure.facecolor": SURF, "axes.facecolor": SURF, "savefig.facecolor": SURF})
fig, axes = plt.subplots(2, 2, figsize=(7.6, 6.0), gridspec_kw={"height_ratios": [1, 1]})
for col, K in enumerate(["5", "8"]):
    w = rep["windows"][K]
    ax = axes[0][col]
    ax.axhline(0, color=MUTED, lw=1, ls=(0, (3, 3)))
    ax.axhline(100, color=INK, lw=1)
    ax.text(3.45, 100, "teacher", va="center", ha="left", fontsize=8, color=INK2)
    ax.text(3.45, 0, "never\ncolludes", va="center", ha="left", fontsize=8, color=INK2)
    for x, (g, (label, color)) in enumerate(GROUPS.items()):
        if g not in w["groups"]:
            continue
        r = w["groups"][g]
        vals = [w["harness"][h]["alignment"] for h in r["harnesses"]]
        ax.scatter([x + 0.18] * len(vals), vals, s=14, color=color, alpha=0.45, lw=0, zorder=3)
        m, (lo, hi) = r["alignment"]["mean"], r["alignment"]["ci95"]
        ax.plot([x - 0.08, x - 0.08], [lo, hi], color=color, lw=2, solid_capstyle="round", zorder=4)
        ax.scatter([x - 0.08], [m], s=46, color=color, edgecolor=SURF, lw=1.5, zorder=5)
        ax.text(x - 0.2, m, f"{m:.0f}", ha="right", va="center", fontsize=8, color=INK)
    ax.set_xticks(range(4), ["Original", "Round 1", "Round 2", "Round 3"])
    ax.set_xlim(-0.6, 3.4)
    ax.set_ylim(-130, 110)
    ax.grid(axis="y", color=GRID, lw=0.6)
    ax.set_ylabel("Alignment to teacher" if col == 0 else "")
    ax.set_title(f"First {K} scored episodes (teacher: {w['teacher_window'][0]}→{w['teacher_window'][-1]}%)",
                 fontsize=9, color=INK, loc="left")
    ax = axes[1][col]
    for ci_, (key, label) in enumerate(COMP):
        for j, (g, (glabel, color)) in enumerate(GROUPS.items()):
            if g not in w["groups"]:
                continue
            r = w["groups"][g][key]
            x = ci_ + (j - 1.5) * 0.17
            ax.plot([x, x], r["ci95"], color=color, lw=2, solid_capstyle="round", zorder=3)
            ax.scatter([x], [r["mean"]], s=40, color=color, edgecolor=SURF, lw=1.5, zorder=4,
                       label=glabel if (ci_ == 0 and col == 0) else None)
    ax.set_xticks(range(3), [c[1] for c in COMP])
    ax.set_ylim(0, 105)
    ax.grid(axis="y", color=GRID, lw=0.6)
    ax.set_ylabel("Distance from teacher (points)" if col == 0 else "")
fig.legend(loc="lower center", ncol=2, frameon=False, fontsize=8, bbox_to_anchor=(0.5, -0.005))
fig.text(0.01, 0.995, "Behavior alignment of Qwen3-4B teams to the teacher's learned collusion", fontsize=10,
         color=INK, va="top", weight="bold")
fig.text(0.01, 0.965, "Dots: harnesses (each averaged over 3-trajectory subsets). Bars: 95% CI over harnesses "
         "(original: over trajectories).", fontsize=8, color=INK2, va="top")
fig.tight_layout(rect=(0, 0.07, 1, 0.95))
fig.savefig(sys.argv[2], dpi=200)
print("wrote", sys.argv[2])
