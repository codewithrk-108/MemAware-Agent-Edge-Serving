"""Histogram of the true (recorded) output length of the 113 requests in the 50-task smoke trace.

Output: experiments/workload_stats/output_length_hist.png. Bars are stacked by first step vs later steps of a task;
dashed lines mark the median and the fixed caps tried in Exp 4 (1,024 / 2,048 / 4,096 / 8,192).
"""
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
tasks = [json.loads(l) for l in (ROOT / "data/traces/agenttrace_50_seed2.jsonl").open()]
first = np.array([t["steps"][0]["n_gen"] for t in tasks])
later = np.array([s["n_gen"] for t in tasks for s in t["steps"][1:]])
allv = np.concatenate([first, later])
bins = np.arange(0, 8001, 250)

fig, ax = plt.subplots(figsize=(10, 5.2), dpi=150)
ax.hist([first, later], bins=bins, stacked=True, color=["#2a6fdb", "#9db8e8"], edgecolor="white", linewidth=0.6,
        label=[f"first step of a task (n={len(first)})", f"later steps (n={len(later)})"])
ymax = ax.get_ylim()[1]
med = np.median(allv)
ax.axvline(med, color="#c2410c", lw=1.6)
ax.text(med + 60, ymax * 0.93, f"median {med:,.0f}", color="#c2410c", fontsize=9)
for cap in (1024, 2048, 4096):
    over = int((allv > cap).sum())
    ax.axvline(cap, color="#555", lw=1, ls="--")
    ax.text(cap + 60, ymax * 0.78, f"cap {cap:,}\n{over} longer", color="#444", fontsize=8.5)
ax.set_xlim(0, 8000)
ax.set_xlabel("true output length (tokens), bins of 250")
ax.set_ylabel("requests")
ax.set_title(f"True output length of the {len(allv)} requests in the 50-task smoke trace\n"
             f"mean {allv.mean():,.0f} · p25 {np.percentile(allv, 25):,.0f} · p75 {np.percentile(allv, 75):,.0f} · "
             f"p90 {np.percentile(allv, 90):,.0f} · max {allv.max():,}", fontsize=11)
ax.spines[["top", "right"]].set_visible(False)
ax.legend(frameon=False, loc="upper right")
fig.tight_layout()
fig.savefig(Path(__file__).with_name("output_length_hist.png"))
print("first step median", np.median(first), "later median", np.median(later))
