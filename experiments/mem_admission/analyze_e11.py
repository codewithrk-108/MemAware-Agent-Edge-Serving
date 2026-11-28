"""Experiment 11 analysis: prompt reuse by slot choice, KV occupancy and task latency, E11 (slot reuse) vs E7b control.

Usage: python -m experiments.mem_admission.analyze_e11 [E11_RUN] [CONTROL_RUN] [OUT_DIR] [LABEL] [CONTROL_LABEL]
LABEL names the slot-reuse run in figures and tables (default "E11"; e.g. "E11b").
Defaults are the 50-task smoke runs. Writes PNG figures and reuse tables into OUT_DIR.
"""
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "results/mem_admission"
E11 = R / (sys.argv[1] if len(sys.argv) > 1 else "smoke_e11_cap2048_gbm_slotreuse")
CTL = R / (sys.argv[2] if len(sys.argv) > 2 else "smoke_e7b_cap2048_gbm_rebuilt")
OUT = R / (sys.argv[3] if len(sys.argv) > 3 else "e11_smoke_analysis")
LBL = sys.argv[4] if len(sys.argv) > 4 else "E11"
CTL_LBL = sys.argv[5] if len(sys.argv) > 5 else "E7b control (erase)"
OUT.mkdir(parents=True, exist_ok=True)

# reference palette (dataviz skill, light mode): slot 1 blue, slot 2 orange; text inks; recessive grid
BLUE, ORANGE, AQUA, YELLOW = "#257de0", "#f47230", "#00b894", "#f2a600"
INK, INK2, GRID = "#191919", "#666666", "#e6e6e6"
RUNS = {f"{LBL} slot reuse": (E11, BLUE), CTL_LBL: (CTL, ORANGE)}
plt.rcParams.update({"font.size": 10, "axes.edgecolor": INK2, "axes.labelcolor": INK, "xtick.color": INK2,
                     "ytick.color": INK2, "axes.spines.top": False, "axes.spines.right": False,
                     "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8, "axes.axisbelow": True,
                     "legend.frameon": False, "figure.dpi": 130})


def kind(df):
    if "slot_kind" in df:
        return df.slot_kind.fillna("empty")
    return np.where(df.continuation.astype(bool), "cont", "erased")


def load(d):
    q = pd.read_csv(d / "requests.csv")
    q = q[q.error.isna()].copy()
    q["kind"] = kind(q)
    q["type"] = np.where(q.continuation.astype(bool), "continuation", np.where(q.step == 0, "new step 0",
                                                                                 "new after tool"))
    q["prompt_total"] = q.prompt_n + q.cache_n
    t = pd.read_csv(d / "tasks.csv")
    kv = pd.read_csv(d / "kv_memory.csv")
    meta = json.loads((d / "meta.json").read_text())
    ev = pd.read_csv(d / "events.csv")
    purges = (d / "server.log").read_text(errors="ignore").count("purging slot")
    return q, t, kv, meta, ev, purges


data = {k: load(v[0]) for k, v in RUNS.items()}
lines = []

# ---------- tables ----------
rows = []
for name, (q, t, kv, meta, ev, purges) in data.items():
    s = meta.get("summary", meta)
    ok = t[t.status == "ok"]
    rows.append({"run": name, "completed": f"{len(ok)}/{len(t)}",
                 "prompt tokens": int(q.prompt_total.sum()), "computed": int(q.prompt_n.sum()),
                 "reused": int(q.cache_n.sum()), "reused %": round(100 * q.cache_n.sum() / q.prompt_total.sum(), 1),
                 "p50 s": round(ok.completion_s.median(), 1), "p95 s": round(ok.completion_s.quantile(.95), 1),
                 "p99 s": round(ok.completion_s.quantile(.99), 1),
                 "tasks/min": round(len(ok) / ((t.finish.max() - t.arrival.min()) / 60), 2),
                 "TPOT p50 ms": round(q.tpot_ms.median(), 1), "mean KV %": round(100 * kv.usage_ratio.mean(), 1),
                 "llama.cpp purges": purges,
                 "leftover erases": int(ev.event.astype(str).str.startswith("leftover_erase").sum()),
                 # finished/live split logged from E11c on; earlier runs logged every erase as "leftover_erase"
                 "of which finished task's": int((ev.event == "leftover_erase_finished").sum()),
                 "deadlock discards": int((ev.event == "deadlock_discard").sum())})
summary = pd.DataFrame(rows).set_index("run").T
lines += ["## Run comparison", "", summary.to_markdown(), ""]

by_type = []
for name, (q, *_r) in data.items():
    for ty, g in q.groupby("type"):
        by_type.append({"run": name, "request type": ty, "requests": len(g), "prompt tokens": int(g.prompt_total.sum()),
                        "computed": int(g.prompt_n.sum()), "reused": int(g.cache_n.sum()),
                        "reused %": round(100 * g.cache_n.sum() / max(g.prompt_total.sum(), 1), 1)})
by_type = pd.DataFrame(by_type)
lines += ["## Reuse by request type", "", by_type.to_markdown(index=False), ""]

q11 = data[f"{LBL} slot reuse"][0]
by_kind = q11.groupby("kind").agg(requests=("cache_n", "size"), reused=("cache_n", "sum"),
                                  prompt=("prompt_total", "sum"), reused_median=("cache_n", "median"),
                                  predicted_median=("lcp_pred", "median")).reset_index()
by_kind["reused %"] = (100 * by_kind.reused / by_kind.prompt).round(1)
lines += [f"## {LBL} reuse by slot choice (own = task's own previous slot, finished = a finished task's leftover, "
          "other = a live task's leftover, empty, cont = continuation in its own slot)", "", by_kind.to_markdown(index=False), ""]
pred_ok = q11[q11.kind != "empty"]
err = (pred_ok.cache_n - pred_ok.lcp_pred)
lines += [f"Predicted vs actual reused tokens (non-empty slots): median difference {err.median():.0f}, "
          f"|diff| <= 1 token for {100 * (err.abs() <= 1).mean():.1f}% of {len(pred_ok)} requests; "
          f"actual < predicted by > 16 tokens for {int((err < -16).sum())} requests "
          f"(cache lost before use).", ""]
(OUT / "reuse_tables.md").write_text("\n".join(lines))
print("\n".join(lines))

# ---------- figure 1: prompt tokens computed vs reused, by request type ----------
types = ["new step 0", "new after tool", "continuation"]
fig, ax = plt.subplots(figsize=(7.2, 3.8))
w = 0.38
for i, (name, (q, *_r)) in enumerate(data.items()):
    color = RUNS[name][1]
    comp = [q[q.type == ty].prompt_n.sum() / 1e3 for ty in types]
    reu = [q[q.type == ty].cache_n.sum() / 1e3 for ty in types]
    x = np.arange(len(types)) + (i - 0.5) * w
    ax.bar(x, comp, w - 0.03, color=color, label=f"{name}: computed", edgecolor="white", linewidth=1)
    ax.bar(x, reu, w - 0.03, bottom=comp, color=color, alpha=0.35, hatch="///", edgecolor="white", linewidth=1,
           label=f"{name}: reused from slot")
    for xi, c, r in zip(x, comp, reu):
        if c + r > 0:
            ax.text(xi, c + r + 4, f"{100 * r / (c + r):.0f}% reused", ha="center", fontsize=8, color=INK2)
ax.set_xticks(np.arange(len(types)), types)
ax.set_ylim(0, ax.get_ylim()[1] * 1.35)
ax.set_ylabel("prompt tokens (thousands)")
ax.set_title(f"Prompt tokens per request type: computed vs reused ({len(data[next(iter(data))][1])} tasks)", loc="left", color=INK)
ax.legend(fontsize=8, ncol=2, loc="upper left")
fig.tight_layout()
fig.savefig(OUT / "fig1_prompt_reuse_by_type.png")

# ---------- figure 2: E11 reuse per request by slot choice ----------
fig, ax = plt.subplots(figsize=(7.2, 3.8))
order = [k for k in ["own", "finished", "other", "empty", "cont"] if k in set(q11.kind)]
colors = {"own": BLUE, "finished": "#8a5cd1", "other": AQUA, "empty": YELLOW, "cont": ORANGE}
labels = {"own": "own previous slot", "finished": "finished task's leftover", "other": "live task's leftover", "empty": "empty slot",
          "cont": "continuation (same slot)"}
for k in order:
    g = q11[q11.kind == k]
    ax.scatter(g.prompt_total / 1e3, 100 * g.cache_n / g.prompt_total, s=28, color=colors[k], alpha=0.8,
               edgecolor="white", linewidth=0.6, label=f"{labels[k]} (n={len(g)})")
ax.set_xlabel("prompt length (thousand tokens)")
ax.set_ylabel("share of prompt reused (%)")
ax.set_ylim(-3, 103)
ax.set_title(f"{LBL}: how much of each prompt came from the slot's cache", loc="left", color=INK)
ax.legend(fontsize=8, loc="center right")
fig.tight_layout()
fig.savefig(OUT / "fig2_e11_reuse_by_slot_choice.png")

# ---------- figure 3: physical KV occupancy over time ----------
fig, ax = plt.subplots(figsize=(7.2, 3.4))
for name, (q, t, kv, *_r) in data.items():
    ax.plot(kv.t / 60, kv.used_cells / 1e3, color=RUNS[name][1], linewidth=1.6, label=name)
ax.axhline(32.768, color=INK2, linewidth=1, linestyle="--")
ax.text(0.2, 31.6, "pool 32,768 cells", fontsize=8, color=INK2)
ax.set_xlabel("time (min)")
ax.set_ylabel("occupied KV cells (thousands)")
ax.set_title(f"Physical KV occupancy (leftovers stay resident in {LBL})", loc="left", color=INK)
ax.legend(fontsize=8, loc="lower right")
fig.tight_layout()
fig.savefig(OUT / "fig3_kv_occupancy.png")

# ---------- figure 4: task completion latency CDF ----------
fig, ax = plt.subplots(figsize=(7.2, 3.4))
for name, (q, t, *_r) in data.items():
    c = np.sort(t[t.status == "ok"].completion_s.values)
    ax.step(c, np.arange(1, len(c) + 1) / len(c) * 100, where="post", color=RUNS[name][1], linewidth=2, label=name)
ax.set_xlabel("task completion time (s)")
ax.set_ylabel("tasks completed (%)")
ax.set_title("Task completion time, cumulative", loc="left", color=INK)
ax.legend(fontsize=8, loc="lower right")
fig.tight_layout()
fig.savefig(OUT / "fig4_latency_cdf.png")

# ---------- figure 5: time per output token vs occupied KV ----------
fig, ax = plt.subplots(figsize=(7.2, 3.4))
for name, (q, t, kv, *_r) in data.items():
    kvt = kv.set_index("t").used_cells
    occ = np.interp(q.t_rel, kvt.index, kvt.values) / 1e3
    ax.scatter(occ, q.tpot_ms, s=14, alpha=0.55, color=RUNS[name][1], edgecolor="none", label=name)
ax.set_ylim(0, 60)
n_out = sum(int((d[0].tpot_ms > 60).sum()) for d in data.values())
if n_out:
    ax.text(0.99, 0.95, f"{n_out} point(s) above 60 ms not shown", transform=ax.transAxes, ha="right",
            fontsize=8, color=INK2)
ax.set_xlabel("occupied KV cells when the request was sent (thousands)")
ax.set_ylabel("time per output token (ms)")
ax.set_title("Decode speed vs KV occupancy", loc="left", color=INK)
ax.legend(fontsize=8, loc="upper left")
fig.tight_layout()
fig.savefig(OUT / "fig5_tpot_vs_kv.png")
# ---------- figure 6: what happened to a returning step's previous slot (E11) ----------
# A slot whose first later user picked it as "empty" had been erased by the memory guard first (the leftover was gone).
q = q11.sort_values("t_rel")
fate = {"reused its own slot": 0, "slot taken by another task": 0, "slot erased by memory guard": 0}
for _, r in q[(~q.continuation.astype(bool)) & (q.step > 0)].iterrows():
    prev = q[(q.task_id == r.task_id) & (q.t_rel < r.t_rel)].iloc[-1]
    took = q[(q.slot_used == prev.slot_used) & (q.t_rel > prev.t_rel) & (q.t_rel < r.t_rel) & (q.task_id != r.task_id)]
    if r.slot_kind == "own":
        fate["reused its own slot"] += 1
    elif len(took) and took.iloc[0].slot_kind != "empty":   # first taker picked it while the leftover was still there
        fate["slot taken by another task"] += 1
    else:
        fate["slot erased by memory guard"] += 1
fig, ax = plt.subplots(figsize=(7.2, 2.6))
names, vals = list(fate), list(fate.values())
ax.barh(names[::-1], vals[::-1], color=[BLUE, AQUA, YELLOW][::-1], height=0.55)
for i, v in enumerate(vals[::-1]):
    ax.text(v + 0.6, i, str(v), va="center", fontsize=9, color=INK)
ax.set_xlabel("requests returning from a tool call")
ax.grid(axis="y", visible=False)
ax.set_title(f"{LBL}: fate of a returning step's previous slot (n={sum(vals)})", loc="left", color=INK)
fig.tight_layout()
fig.savefig(OUT / "fig6_returning_step_slot_fate.png")
with open(OUT / "reuse_tables.md", "a") as f:
    f.write(f"\n## {LBL}: fate of a returning step's previous slot\n\n" +
            pd.Series(fate, name="requests").to_frame().to_markdown() + "\n")
print("returning-step slot fate:", fate)
print("figures in", OUT)
