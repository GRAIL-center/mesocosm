"""IDR-LOOP-1 figures: loop loss by test (designer vs random) and held-out loss of the final designs.

Usage: python3 make_fig.py results_loop.json [analysis.json] out.png
"""

from __future__ import annotations

import json
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

BLUE, ORANGE, GRAY, INK = "#2a6fdb", "#e07b39", "#8a8a8a", "#222222"


def main() -> None:
    r = json.load(open(sys.argv[1]))
    analysis = json.load(open(sys.argv[2])) if len(sys.argv) > 3 else None
    out = sys.argv[-1]
    fig, axes = plt.subplots(1, 2 if analysis else 1, figsize=(11 if analysis else 6.5, 4.2), squeeze=False)
    ax = axes[0][0]
    for arm, color, label in (("D", ORANGE, "Designer (Claude Opus 5.5)"), ("R", BLUE, "Random search")):
        xs = [x["test"] for x in r[arm]]
        ys = [x["loss"] for x in r[arm]]
        ax.plot(xs, ys, "o-", color=color, lw=2, ms=6, label=label)
        best = [min(ys[: i + 1]) for i in range(len(ys))]
        ax.plot(xs, best, "--", color=color, lw=1.2, alpha=0.7)
    ax.axhline(r["original"]["loop"]["loss"], color=GRAY, ls=":", lw=1.5)
    ax.text(8.1, r["original"]["loop"]["loss"] + 1, "original env.", color=GRAY, fontsize=9, ha="right")
    ax.set_xlabel("Test")
    ax.set_ylabel("Loss to Qwen3-8B target (points, lower is better)")
    ax.set_title("Loop tasks (12): loss by test; dashed = best so far", fontsize=10)
    ax.set_xticks(range(1, 9))
    ax.set_ylim(0, 50)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False, fontsize=9, loc="upper right")
    if analysis:
        ax2 = axes[0][1]
        h = analysis["heldout"]
        names = [("orig_heldout", "Original env.", GRAY), ("R", "Random final", BLUE), ("D", "Designer final", ORANGE)]
        vals = [h[k]["loss"] for k, _, _ in names]
        bars = ax2.bar([n for _, n, _ in names], vals, color=[c for _, _, c in names], width=0.6)
        for b, v in zip(bars, vals, strict=True):
            ax2.text(b.get_x() + b.get_width() / 2, v + 0.8, f"{v:.1f}", ha="center", fontsize=10, color=INK)
        ax2.set_ylabel("Held-out loss (points)")
        ax2.set_title(f"Held-out tasks ({len(h['tasks'])}), one run per final design", fontsize=10)
        ax2.spines[["top", "right"]].set_visible(False)
        ax2.set_ylim(0, max(vals) * 1.25)
    fig.tight_layout()
    fig.savefig(out, dpi=200)
    print(out)


if __name__ == "__main__":
    main()
