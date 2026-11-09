"""Figures for the small-model reproduction and the held-out test.
Usage: python fig_signature.py <signature.json> <results_dir> [q27_score.json]
  figure6_signature.png  three dot panels (episode-1 rate, change per episode, overall rate) per Qwen3-4B harness,
                         colored by round, target (teacher on the same window) as a black tick
  figure7_curves.png     mutual-ACCEPT curve by scored episode: teacher, Qwen3-4B original, best round-1, best round-2
  figure8_heldout.png    held-out test (B: Gemma-4-12B): direction accuracy and MAE per guide arm vs the no-change reference
"""
import json, pathlib, sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sig = json.loads(pathlib.Path(sys.argv[1]).read_text())
res = pathlib.Path(sys.argv[2])
INK, INK2, MUTED, GRID, SURF = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#fcfcfb"
ROUND = {"ref": ("Qwen3-4B, original settings", "#898781"), "r1": ("Round 1 (no profile, no guide)", "#2a78d6"),
         "r2": ("Round 2 (profile + guide v2)", "#eb6834"), "r3": ("Round 3 (guide v3 + measured harnesses)", "#1baf7a")}
NAMES = {"main": "original settings", "mem0": "original, no memory"}
TARGET_CURVE = [0, 0, 12, 26, 46, 56, 66, 72, 72, 74]
plt.rcParams.update({"font.size": 9, "axes.edgecolor": MUTED, "axes.labelcolor": INK2, "xtick.color": INK2,
                     "ytick.color": INK2, "axes.spines.top": False, "axes.spines.right": False,
                     "figure.facecolor": SURF, "axes.facecolor": SURF, "savefig.facecolor": SURF})

order = [k for r in ROUND for k, s in sorted(sig.items(), key=lambda x: x[1]["distance"]) if s["round"] == r]
if order:
    fig, axes = plt.subplots(1, 3, figsize=(7.6, 0.9 + 0.32 * len(order)), sharey=True)
    ys = {k: len(order) - i for i, k in enumerate(order)}
    panels = [("ep1", "Both ACCEPT in episode 1 (%)", lambda s: 0.0),
              ("slope", "Change per episode (points)", lambda s: s["target_slope_same_window"]),
              ("rate", "Both ACCEPT overall (%)", lambda s: s["target_rate_same_window"])]
    for ax, (key, label, tgt) in zip(axes, panels):
        for k in order:
            s = sig[k]
            ax.plot([tgt(s)], [ys[k]], marker="|", ms=12, mew=2, color=INK, zorder=3)
            ax.scatter([s[key]], [ys[k]], s=38, color=ROUND[s["round"]][1], zorder=4)
        if key == "slope":
            ax.axvline(0, color=GRID, lw=1)
        ax.set_xlabel(label, fontsize=8)
        ax.grid(axis="x", color=GRID, lw=0.6)
    axes[0].set_yticks([ys[k] for k in order],
                       [f"{NAMES.get(k, k)} (n={sig[k]['n_traj']}, K={sig[k]['K']})" for k in order], fontsize=7.5)
    hs = [plt.Line2D([], [], marker="o", ls="", color=c, label=l) for r, (l, c) in ROUND.items()
          if any(sig[k]["round"] == r for k in order)]
    hs.append(plt.Line2D([], [], marker="|", ms=10, mew=2, ls="", color=INK, label="Teacher, same episodes"))
    fig.legend(handles=hs, loc="lower center", ncol=3, fontsize=7.5, frameon=False)
    fig.tight_layout(rect=(0, 0.075 if len(order) > 6 else 0.14, 1, 1))
    fig.savefig(res / "figure6_signature.png", dpi=200); plt.close(fig)

    fig, ax = plt.subplots(figsize=(6.4, 3.4))
    ax.plot(range(1, 11), TARGET_CURVE, "-D", color=INK, ms=4, lw=2, label="Teacher (Gemini 3.1 Flash-Lite)")
    def best(r):  # closest to the teacher among harnesses with >= 5 scored episodes (short windows flatter distance)
        pool = [k for k in order if sig[k]["round"] == r and sig[k]["K"] >= 5]
        return min(pool, key=lambda k: sig[k]["distance"]) if pool else None
    picks = [("main", "ref")] + [(best(r), r) for r in ("r1", "r2", "r3") if best(r)]
    for k, r in picks:
        if k not in sig:
            continue
        c = sig[k]["curve"]
        lab = ROUND[r][0] if r == "ref" else f"Best {ROUND[r][0].split(' (')[0].lower()}: {k}"
        ax.plot(range(1, len(c) + 1), c, "-o", color=ROUND[r][1], ms=4, lw=2, label=lab)
    ax.set_xlabel("Scored episode"); ax.set_ylabel("Both ACCEPT (%)")
    ax.set_xticks(range(1, 11)); ax.set_ylim(-3, 100); ax.grid(color=GRID, lw=0.6)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.18), ncol=2, fontsize=7.5, frameon=False)
    fig.tight_layout(); fig.savefig(res / "figure7_curves.png", dpi=200); plt.close(fig)

def heldout(qpath, outname, title_models):
    q = json.loads(pathlib.Path(qpath).read_text())
    arms = [a for a in ("N", "V1", "V2") if a in q["arms"]]
    lab = {"N": "No guide", "V1": "Guide v1", "V2": "Guide v2"}
    col = {"N": "#898781", "V1": "#2a78d6", "V2": "#eb6834"}
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.6))
    mk = "abs_mae" if "abs_mae" in q["no_change"] else "rel_mae"
    for ax, key, xl in ((axes[0], "dir_acc", "Direction accuracy"), (axes[1], mk, "Mean absolute error (points)" if mk == "abs_mae" else "Relative MAE (points)")):
        for i, a in enumerate(arms):
            vals = [s[key] for s in q["sessions"] if s["arm"] == a]
            ax.scatter(vals, [len(arms) - i] * len(vals), s=12, color=col[a], alpha=0.4, linewidths=0)
            ax.scatter([q["arms"][a][key]], [len(arms) - i], s=50, color=col[a], zorder=3)
        ax.axvline(q["no_change"][key], color=INK2, ls="--", lw=1)
        ax.text(q["no_change"][key], len(arms) + 0.45, " answer “no change”", fontsize=7, color=INK2)
        ax.set_yticks(range(len(arms), 0, -1), [lab[a] for a in arms]); ax.set_ylim(0.4, len(arms) + 0.8)
        ax.set_xlabel(xl); ax.grid(axis="x", color=GRID, lw=0.6)
    axes[1].set_yticklabels([])
    fig.tight_layout(); fig.savefig(res / outname, dpi=200); plt.close(fig)


for i, (name, label) in enumerate((("figure8_heldout_qwen27b.png", "Qwen3.8-27B"), ("figure9_heldout_gemma.png", "Gemma-4-12B")), start=3):
    if len(sys.argv) > i and pathlib.Path(sys.argv[i]).exists():
        heldout(sys.argv[i], name, label)
ITEM = {"J1": "Remove verdict review", "J2": "Separate rewards", "J3": "No memory", "J4": "Hide the reward",
        "J5": "Pay every ACCEPT, no review", "J6": "Memory of last 3", "J7": "Keep only feedback + reflection"}


def items(qpath, outname):
    """Per-intervention predictions (arm means, session dots) against the measured rate (black diamond)."""
    q = json.loads(pathlib.Path(qpath).read_text())
    js = [j for j in ITEM if j in q["truth"]]
    arms = [("N", "No guide", "#898781"), ("V1", "Guide v1", "#2a78d6"), ("V2", "Guide v2", "#eb6834")]
    fig, ax = plt.subplots(figsize=(7.2, 0.7 + 0.55 * len(js)))
    ys = {j: len(js) - k for k, j in enumerate(js)}
    for (a, lab, col), off in zip(arms, (0.2, 0.0, -0.2)):
        for j in js:
            vals = [float(s["pred"][j]) for s in q["sessions"] if s["arm"] == a]
            if not vals: continue
            ax.scatter(vals, [ys[j] + off] * len(vals), s=10, color=col, alpha=0.35, linewidths=0)
            ax.scatter([sum(vals) / len(vals)], [ys[j] + off], s=40, color=col, zorder=3, label=lab if j == js[0] else None)
    for j in js:
        ax.scatter([q["truth"][j]["rate"]], [ys[j]], marker="D", s=40, color=INK, zorder=4, label="Measured" if j == js[0] else None)
    ax.axvline(q["baseline_rate"], color=INK2, ls="--", lw=1)
    ax.text(q["baseline_rate"] + 1, len(js) + 0.45, f"unchanged {q['baseline_rate']:.0f}%", fontsize=7, color=INK2)
    ax.set_yticks([ys[j] for j in js], [ITEM[j] for j in js], fontsize=8)
    ax.set_xlim(-3, 103); ax.set_ylim(0.5, len(js) + 0.8)
    ax.set_xlabel(f"Episodes with both ACCEPT, scored episodes 1-{q['K']} (%)"); ax.grid(axis="x", color=GRID, lw=0.6)
    ax.legend(loc="upper center", bbox_to_anchor=(0.45, -0.12 - 0.1 / max(1, len(js) / 6)), ncol=4, fontsize=7.5, frameon=False)
    fig.tight_layout(); fig.savefig(res / outname, dpi=200); plt.close(fig)


if len(sys.argv) > 3 and pathlib.Path(sys.argv[3]).exists():
    items(sys.argv[3], "figure10_heldout_qwen27b_items.png")
print("figures written")
