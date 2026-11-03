"""Writeup figure: completion vs. throughput for the seven 500-task runs (R1-R7).

Reads the committed run summaries, so the plotted points always match the data:
    results/t4_full500_v1_part_a/summary.csv  (R1-R3)
    results/t4_full500_v1_part_b/summary.csv  (R4-R7)

Usage: python report/fig_tradeoff.py [OUTPUT.pdf]   (default: report/fig_tradeoff.pdf)
"""

import csv
import re
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager as fm
from matplotlib.lines import Line2D

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SUMMARIES = [ROOT / "results/t4_full500_v1_part_a/summary.csv",
             ROOT / "results/t4_full500_v1_part_b/summary.csv"]
COLORS = {(1, 32768): "#2a6f97", (4, 32768): "#e07a1f", (16, 32768): "#9d4edd", (16, 65536): "#6a994e"}
LEGEND = [((1, 32768), "N1, 32K"), ((4, 32768), "N4, 32K"), ((16, 32768), "N16, 32K"), ((16, 65536), "N16, 64K")]


def load_points():
    """Return [(label, tasks_per_min, completed_pct, color)] for R1-R7 in run order."""
    points = []
    for path in SUMMARIES:
        with path.open() as f:
            for row in csv.DictReader(f):
                run = int(re.search(r"_c(\d+)_", row["name"]).group(1))
                key = (int(row["concurrency"]), int(row["ctx"]))
                pct = 100 * int(row["ok"]) / int(row["n_tasks"])
                points.append((f"R{run}", float(row["throughput_tasks_min"]), pct, COLORS[key]))
    return sorted(points, key=lambda p: int(p[0][1:]))


def plot(out: Path) -> None:
    for f in fm.findSystemFonts():
        if "texgyretermes" in f:
            fm.fontManager.addfont(f)
    plt.rcParams.update({"font.size": 7.5, "font.family": "TeX Gyre Termes", "mathtext.fontset": "stix",
                         "axes.spines.top": False, "axes.spines.right": False, "axes.linewidth": 0.6})
    pts = load_points()
    by_name = {n: (x, y) for n, x, y, _ in pts}
    fig, ax = plt.subplots(figsize=(3.3, 1.7))
    ax.grid(axis="y", lw=0.4, color="#dddddd", zorder=0)
    for _, x, y, c in pts:
        ax.scatter(x, y, s=22, color=c, zorder=3, edgecolor="white", lw=0.5)
    labels = {"R1": (6, -3, "left"), "R3": (6, -3, "left"), "R5": (-6, -3, "right"), "R7": (6, -3, "left")}
    for n, (dx, dy, ha) in labels.items():
        ax.annotate(n, by_name[n], xytext=(dx, dy), textcoords="offset points", fontsize=7, ha=ha)
    n4 = [by_name[n] for n in ("R2", "R4", "R6")]
    anchor = max(n4)  # rightmost N4 point
    ax.annotate("R2, R4, R6", (anchor[0], anchor[1] + 0.3), xytext=(4.75, 88), fontsize=7, ha="left",
                va="center", arrowprops=dict(arrowstyle="-", lw=0.5, color="#666666"))
    ax.set_xlabel("Completed tasks / min")
    ax.set_ylabel("Tasks completed (%)")
    ax.set_xlim(2.4, 5.9)
    ax.set_ylim(0, 108)
    ax.set_yticks([0, 25, 50, 75, 100])
    handles = [Line2D([], [], marker="o", ls="", color=COLORS[k], label=l, ms=4) for k, l in LEGEND]
    ax.legend(handles=handles, frameon=False, fontsize=6.5, loc="lower left", handletextpad=0.2)
    fig.tight_layout(pad=0.3)
    fig.savefig(out)
    plt.close(fig)


if __name__ == "__main__":
    plot(Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "fig_tradeoff.pdf")
