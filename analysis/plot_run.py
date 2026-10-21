"""Anatomy of one Phase 1 run: what every task did over time, system state, and where time went.

    uv run python -m analysis.plot_run preview_syn_N8 [--platform macos]
"""

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
import numpy as np
import pandas as pd

from harness.platform import DEFAULT, PLATFORMS

ROOT = Path(__file__).resolve().parents[1]
COLORS = {"queued": "#bdbdbd", "prefill": "#ff7f0e", "decode": "#2ca02c", "tool": "#d62728"}


def load(run_dir: Path):
    tasks = pd.read_csv(run_dir / "tasks.csv")
    reqs = pd.read_csv(run_dir / "requests.csv")
    tl = pd.read_csv(run_dir / "timeline.csv")
    mem = pd.read_csv(run_dir / "memory.csv")
    meta = json.loads((run_dir / "meta.json").read_text())
    # request times are monotonic clock values, t_rel is t_send relative to the run start
    t0 = (reqs.t_send - reqs.t_rel).median()
    for c in ("t_send", "t_first", "t_end"):
        reqs[c] = reqs[c] - t0
    return tasks, reqs.sort_values(["task_id", "step"]), tl, mem, meta


def gantt(ax, tasks, reqs):
    order = tasks.sort_values("arrival").reset_index(drop=True)
    by_task = {k: g for k, g in reqs.groupby("task_id")}
    for y, t in order.iterrows():
        if pd.notna(t.admit):
            ax.barh(y, t.admit - t.arrival, left=t.arrival, color=COLORS["queued"], height=0.8)
        g = by_task.get(t.task_id)
        if g is not None:
            rows = list(g.itertuples())
            for i, r in enumerate(rows):
                first = r.t_first if pd.notna(r.t_first) else r.t_end
                ax.barh(y, first - r.t_send, left=r.t_send, color=COLORS["prefill"], height=0.8)
                ax.barh(y, r.t_end - first, left=first, color=COLORS["decode"], height=0.8)
                if i + 1 < len(rows):
                    ax.barh(y, rows[i + 1].t_send - r.t_end, left=r.t_end, color=COLORS["tool"], height=0.8)
        if t.status != "ok":
            ax.plot(t.finish if pd.notna(t.finish) else t.admit, y, "kx", ms=5)
    ax.set_yticks(range(len(order)), [f"{c[:5]}" for c in order.cls], fontsize=5)
    ax.invert_yaxis()
    ax.set(xlabel="seconds since start", ylabel="tasks in arrival order (class)",
           title="Every task over time: grey = waiting in the FIFO queue, orange = prefill (to first token), "
                 "green = generating, red = waiting for the tool (x = failed / timed out)")
    ax.legend(handles=[Patch(color=v, label=k) for k, v in COLORS.items()], loc="lower left", fontsize=8, ncol=4)


def state(ax, tl, mem, reqs, conc):
    ax.step(tl.t, tl.queued, where="post", color="#7f7f7f", label="tasks waiting in queue")
    ax.step(tl.t, tl.running, where="post", color="#1f77b4", label=f"tasks admitted (max N = {conc})")
    ax.set(ylabel="tasks", xlabel="seconds since start", title="System state: queue, admitted tasks and server memory")
    b = ax.twinx()
    b.plot(mem.t, mem.footprint_mib, color="#9467bd", lw=1, label="server memory (MiB)")
    b.set_ylabel("MiB", color="#9467bd")
    for t in reqs[reqs.error.notna()].t_send:
        ax.axvline(t, color="k", lw=0.5, alpha=0.5)
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = b.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, loc="upper left", fontsize=8)


def where_time_goes(ax, tasks, reqs):
    ok = tasks[tasks.status == "ok"].set_index("task_id")
    r = reqs[reqs.task_id.isin(ok.index)]
    per = r.assign(prefill=r.t_first - r.t_send, decode=r.t_end - r.t_first).groupby("task_id")[["prefill", "decode"]].sum()
    ok = ok.join(per)
    parts = ok.groupby("cls").agg(queued=("queue_s", "mean"), prefill=("prefill", "mean"),
                                  decode=("decode", "mean"), tool=("tool_s", "mean"))
    parts.loc["all"] = [ok.queue_s.mean(), ok.prefill.mean(), ok.decode.mean(), ok.tool_s.mean()]
    parts.plot.barh(stacked=True, ax=ax, color=[COLORS[c] for c in parts.columns], width=0.7)
    for i, total in enumerate(parts.sum(axis=1)):
        ax.text(total, i, f" {total:.1f} s", va="center", fontsize=8)
    ax.set(xlabel="mean seconds per finished task", ylabel="", title="Where task time goes, by task class")
    ax.legend(fontsize=8)


def reuse(ax, reqs):
    ok = reqs[reqs.error.isna()]
    first, later = ok[ok.step == 0], ok[ok.step > 0]
    ax.scatter(first.prompt_n, first.ttft_s * 1e3, s=10, alpha=0.6, label="first step (nothing cached)")
    ax.scatter(later.prompt_n.clip(lower=1), later.ttft_s * 1e3, s=10, alpha=0.6,
               label=f"later steps (reused {later.cache_n.sum() / later.n_prompt.sum():.0%} of their prompt)")
    ax.set(xscale="log", yscale="log", xlabel="prompt tokens the server had to compute",
           ylabel="time to first token (ms)", title="Each model request: cost grows with tokens not found in cache")
    ax.legend(fontsize=8)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run")
    ap.add_argument("--platform", choices=PLATFORMS, default=DEFAULT)
    a = ap.parse_args()
    run_dir = ROOT / "results" / a.platform / "phase1" / a.run
    tasks, reqs, tl, mem, meta = load(run_dir)
    s, cfg = meta["summary"], meta["run"]

    fig = plt.figure(figsize=(16, 14))
    gs = fig.add_gridspec(3, 2, height_ratios=[2.2, 1, 1.1])
    gantt(fig.add_subplot(gs[0, :]), tasks, reqs)
    state(fig.add_subplot(gs[1, :]), tl, mem, reqs, cfg["concurrency"])
    where_time_goes(fig.add_subplot(gs[2, 0]), tasks, reqs)
    reuse(fig.add_subplot(gs[2, 1]), reqs)
    fig.suptitle(f"{a.run}: {s['n_tasks']} tasks ({Path(cfg['trace']).stem}) arriving at {cfg['arrival_rate']}/s, "
                 f"FIFO with N = {cfg['concurrency']}, KV budget {cfg['server']['ctx']} tokens, power: {meta['env'].get('power')}\n"
                 f"ok {s['ok']}, failed {s['failed']}, timeouts {s['timeout_queued'] + s['timeout_running']} | "
                 f"completion p50 {s['completion_p50_s']:.1f} s, p95 {s['completion_p95_s']:.1f} s, p99 {s['completion_p99_s']:.1f} s | "
                 f"queue p95 {s['queue_p95_s']:.1f} s | {s['throughput_tasks_min']:.1f} tasks/min | peak memory {s['peak_footprint_mib']:.0f} MiB",
                 fontsize=11)
    fig.tight_layout()
    out = run_dir / "anatomy.png"
    fig.savefig(out, dpi=130)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
