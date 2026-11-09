"""Report figures. Usage: python figures.py <op_dir> <results_dir>

Figure 1: same model, different harness -> episode collusion (EC) with 95% bootstrap CI.
Figure 2: designers' predicted EC vs the released teacher result for each intervention.
"""
import csv
import json
import pathlib
import statistics
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

op, res = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
gt = json.loads((op / "ground_truth.json").read_text())["conditions"]
key = json.loads((op / "answer_key.json").read_text())

INK, INK2, MUTED, GRID, SURF = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#fcfcfb"
BLUE, ORANGE = "#2a78d6", "#eb6834"
plt.rcParams.update({"font.size": 9, "axes.edgecolor": MUTED, "axes.labelcolor": INK2, "xtick.color": INK2,
                     "ytick.color": INK2, "axes.spines.top": False, "axes.spines.right": False,
                     "figure.facecolor": SURF, "axes.facecolor": SURF, "savefig.facecolor": SURF})

LABELS = {
    "I1": "No cross-episode memory", "I2": "Memory: last 3 episodes", "I3": "Memory: communication onward",
    "I4": "Memory: feedback + reflection only", "I5": "Reward hidden from feedback", "I6": "No verdict-correctness feedback",
    "I7": "Separate (non-shared) reward", "I8": "Reward for ACCEPT, no verdict review", "I9": "Warm-up: summaries allowed",
    "I10": "Warm-up: unlimited communication", "I11": "Peer = Claude Opus 4.6", "I12": "Peer = GPT-5.6 Luna"}

# ---------------- Figure 1
base = gt[key["interventions"]["I1"]["condition"].replace("memory_length", "main").split("__0")[0]] \
    if False else gt["main__gemini-3-1-flash-lite"]
items = [("Observed condition (baseline)", base["ec"])] + \
        [(f"{i}  {LABELS[i]}", gt[v["condition"]]["ec"]) for i, v in key["interventions"].items()]
items = [items[0]] + sorted(items[1:], key=lambda x: x[1]["mean"])
fig, ax = plt.subplots(figsize=(6.6, 4.4))
ys = list(range(len(items)))[::-1]
for y, (lab, e) in zip(ys, items):
    is_base = lab.startswith("Observed")
    ax.barh(y, e["mean"], height=0.55, color=INK2 if is_base else BLUE, zorder=2)
    ax.errorbar(e["mean"], y, xerr=[[e["mean"] - e["ci"][0]], [e["ci"][1] - e["mean"]]], fmt="none",
                ecolor=INK, elinewidth=1, capsize=2, zorder=3)
    ax.text(e["ci"][1] + 1.2, y, f"{e['mean']:.1f}", va="center", fontsize=8, color=INK)
ax.axvline(base["ec"]["mean"], color=INK2, lw=1, ls="--", zorder=1)
ax.set_yticks(ys, [lab for lab, _ in items], fontsize=8)
ax.set_xlabel("Episode collusion (%), 95% bootstrap CI over 50 trajectories")
ax.set_xlim(0, 85)
ax.grid(axis="x", color=GRID, lw=0.6, zorder=0)
fig.suptitle("Figure 1. Same model (Gemini 3.1 Flash-Lite), different harness", x=0.01, ha="left", fontsize=10, color=INK)
fig.tight_layout()
fig.savefig(res / "figure1_structure_effects.png", dpi=200)
plt.close(fig)

# ---------------- Figure 2
rows = list(csv.DictReader((res / "predictions_long.csv").open()))
summary = json.loads((res / "summary.json").read_text())
excluded = set(summary.get("excluded", []))
IV = list(key["interventions"])
groups = [("claude", "ev", BLUE, "o", "Claude, with traces"), ("codex", "ev", ORANGE, "o", "Codex, with traces"),
          ("claude", "noev", BLUE, "o", "Claude, no traces"), ("codex", "noev", ORANGE, "o", "Codex, no traces")]
offs = {("claude", "ev"): 0.27, ("codex", "ev"): 0.09, ("claude", "noev"): -0.09, ("codex", "noev"): -0.27}
fig, ax = plt.subplots(figsize=(7.2, 6.2))
ys = {i: len(IV) - k for k, i in enumerate(IV)}
for designer, ev, color, mk, label in groups:
    sel = [r for r in rows if r["designer"] == designer and r["evidence"] == ev and r["session"] not in excluded
           and r["pred_rate"] not in ("", "None")]
    if not sel:
        continue
    for i in IV:
        vals = [float(r["pred_rate"]) for r in sel if r["intervention"] == i]
        if not vals:
            continue
        y = ys[i] + offs[(designer, ev)]
        ax.scatter(vals, [y] * len(vals), s=10, color=color, alpha=0.35, linewidths=0, zorder=2)
        filled = ev == "ev"
        ax.scatter([statistics.mean(vals)], [y], s=42, marker=mk, zorder=4, linewidths=1.6,
                   facecolors=color if filled else SURF, edgecolors=color,
                   label=label if i == IV[0] else None)
for i in IV:
    t = key["interventions"][i]["ec"]
    ax.scatter([t], [ys[i]], marker="D", s=40, color=INK, zorder=5, label="Released result" if i == IV[0] else None)
ax.axvline(key["baseline_ec"], color=INK2, lw=1, ls="--", zorder=1)
ax.text(key["baseline_ec"] + 0.8, len(IV) + 0.75, "observed 42.4%", fontsize=7.5, color=INK2)
ax.set_yticks([ys[i] for i in IV], [f"{i}  {LABELS[i]}" for i in IV], fontsize=8)
ax.set_xlim(-2, 100)
ax.set_ylim(0.4, len(IV) + 1.1)
ax.set_xlabel("Episode collusion (%)")
ax.grid(axis="x", color=GRID, lw=0.6, zorder=0)
ax.legend(loc="upper center", bbox_to_anchor=(0.4, -0.09), ncol=3, fontsize=7.5, frameon=False)
fig.suptitle("Figure 2. Predicted vs released effect of each intervention (large marker = group mean)", x=0.01, ha="left",
             fontsize=10, color=INK)
fig.tight_layout()
fig.savefig(res / "figure2_predictions.png", dpi=200)
plt.close(fig)
print("figures written")
