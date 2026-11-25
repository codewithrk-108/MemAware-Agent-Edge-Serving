"""Histograms of the input length of the 113 requests in the 50-task smoke trace.

Two meanings of "input length", one panel each:
  (a) new input tokens of the step: system prompt + task for a first step, the tool result for a later step
      (`input` in the trace);
  (b) full prompt at request time: everything in the history when the request is sent, i.e. all earlier inputs plus
      all earlier outputs (recorded n_gen) of the task. This is what the server prefills or finds cached, and what
      occupies KV before the request generates.
Output: experiments/workload_stats/input_length_hist.png. Stacked by first step vs later steps.
"""
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
tasks = [json.loads(l) for l in (ROOT / "data/traces/agenttrace_50_seed2.jsonl").open()]
new_first, new_later, full_first, full_later = [], [], [], []
for t in tasks:
    hist = 0
    for k, s in enumerate(t["steps"]):
        hist += len(s["input"])
        (new_first if k == 0 else new_later).append(len(s["input"]))
        (full_first if k == 0 else full_later).append(hist)
        hist += s["n_gen"]
C = ["#2a6fdb", "#9db8e8"]


def panel(ax, first, later, bins, title, xlabel, log=False):
    first, later = np.array(first), np.array(later)
    allv = np.concatenate([first, later])
    ax.hist([first, later], bins=bins, stacked=True, color=C, edgecolor="white", linewidth=0.6,
            label=[f"first step (n={len(first)})", f"later steps (n={len(later)})"])
    if log:
        ax.set_xscale("log")
    med = np.median(allv)
    ax.axvline(med, color="#c2410c", lw=1.6)
    ax.text(med * (1.06 if log else 1) + (0 if log else 150), ax.get_ylim()[1] * 0.92, f"median {med:,.0f}",
            color="#c2410c", fontsize=9)
    ax.set_title(f"{title}\nmean {allv.mean():,.0f} · p25 {np.percentile(allv, 25):,.0f} · "
                 f"p75 {np.percentile(allv, 75):,.0f} · p90 {np.percentile(allv, 90):,.0f} · max {allv.max():,}",
                 fontsize=10.5)
    ax.set_xlabel(xlabel)
    ax.set_ylabel("requests")
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False, loc="upper left" if log else "upper right")
    return allv


fig, (a, b) = plt.subplots(2, 1, figsize=(10, 9), dpi=150)
newv = panel(a, new_first, new_later, np.logspace(0, np.log10(4000), 36),
             "(a) New input tokens of each step (first step: system prompt + task; later: tool result)",
             "new input tokens (log scale)", log=True)
fullv = panel(b, full_first, full_later, np.arange(0, 32001, 1000),
              "(b) Full prompt when the request is sent (all earlier inputs + earlier outputs)",
              "prompt tokens in the history, bins of 1,000")
b.set_xlim(0, 32000)
fig.tight_layout()
fig.savefig(Path(__file__).with_name("input_length_hist.png"))
for name, f, l in (("new", new_first, new_later), ("full", full_first, full_later)):
    print(name, "first median", np.median(f), "min/max", min(f), max(f), "| later median", np.median(l), "min/max", min(l), max(l))
print("full > 8K", int((fullv > 8192).sum()), "> 16K", int((fullv > 16384).sum()))
