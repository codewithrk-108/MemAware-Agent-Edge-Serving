"""Phase 1 performance curves, bottleneck breakdown and summary table. Missing runs are skipped."""

import json
import re
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
P1 = ROOT / "results/macos/phase1"
FIGS = P1 / "figures"


def select(platform_name: str):
    # plotting reads results only, so any platform's results can be plotted on any machine
    global P1, FIGS
    P1 = ROOT / "results" / platform_name / "phase1"
    FIGS = P1 / "figures"
TRACES = {"syn": "synthetic mix", "ai": "AgentInstruct"}
OUTCOMES = ["ok", "failed", "timeout_running", "timeout_queued"]


def summaries() -> pd.DataFrame:
    rows = [json.loads(m.read_text())["summary"] for m in sorted(P1.glob("*/meta.json"))]
    return pd.DataFrame(rows).set_index("name") if rows else pd.DataFrame()


def run_data(name: str) -> dict:
    d = P1 / name
    return {k: pd.read_csv(d / f"{k}.csv") for k in ("tasks", "requests", "timeline", "memory") if (d / f"{k}.csv").exists()}


def savefig(fig, name: str):
    FIGS.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(FIGS / f"{name}.png", dpi=150)
    plt.close(fig)
    print(f"wrote figures/{name}.png")


def breakdown(name: str) -> dict:
    """Mean seconds per task spent queued, in prefill (TTFT), decoding and in tools. Finished tasks only."""
    d = run_data(name)
    t, r = d["tasks"], d["requests"]
    ok = t[t.status == "ok"]
    r = r[r.task_id.isin(ok.task_id)]
    per = r.assign(decode=r.e2e_s - r.ttft_s).groupby("task_id").agg(prefill=("ttft_s", "sum"), decode=("decode", "sum"))
    ok = ok.set_index("task_id").join(per)
    return {"queue": ok.queue_s.mean(), "prefill + wait": ok.prefill.mean(), "decode": ok.decode.mean(), "tool": ok.tool_s.mean()}


def concurrency(s: pd.DataFrame, lines: list):
    for tr, label in TRACES.items():
        rows = s[[bool(re.fullmatch(rf"{tr}_N\d+", n)) for n in s.index]].sort_values("concurrency")
        if rows.empty:
            continue
        n = rows.concurrency
        fig, ax = plt.subplots(2, 3, figsize=(15, 8))
        ax[0, 0].plot(n, rows.throughput_tasks_min, "o-")
        ax[0, 0].set(title="Completed tasks per minute", ylabel="tasks/min")
        for p, m in (("50", "o-"), ("95", "s-"), ("99", "^-")):
            ax[0, 1].plot(n, rows[f"completion_p{p}_s"], m, label=f"p{p}")
        ax[0, 1].set(title="Task completion time (finished tasks)", ylabel="s", yscale="log")
        ax[0, 1].legend(fontsize=8)
        ax[0, 2].plot(n, rows.queue_p50_s, "o-", label="p50")
        ax[0, 2].plot(n, rows.queue_p95_s, "s-", label="p95")
        ax[0, 2].set(title="Queueing time", ylabel="s")
        ax[0, 2].legend(fontsize=8)
        bottom = np.zeros(len(rows))
        for o in OUTCOMES:
            v = rows[o if o != "ok" else "ok"].to_numpy() / rows.n_tasks.to_numpy()
            ax[1, 0].bar(range(len(rows)), v, bottom=bottom, label=o)
            bottom += v
        ax[1, 0].set_xticks(range(len(rows)), n)
        ax[1, 0].set(title="Task outcomes", ylabel="share of tasks", xlabel="concurrency N")
        ax[1, 0].legend(fontsize=7)
        ax[1, 1].plot(n, rows.peak_footprint_mib, "o-", label="peak")
        ax[1, 1].plot(n, rows.start_footprint_mib, "k--", label="after startup")
        ax[1, 1].set(title="Server memory footprint", ylabel="MiB")
        ax[1, 1].legend(fontsize=8)
        ax[1, 2].plot(n, rows.cache_hit_ratio, "o-", label="prompt tokens reused")
        ax[1, 2].plot(n, rows.ttft_p95_s / rows.ttft_p95_s.max(), "s--", label="TTFT p95 (normalized)")
        ax[1, 2].set(title="Prefix reuse and TTFT", ylim=(0, 1.05))
        ax[1, 2].legend(fontsize=8)
        for a in ax.flat:
            if a is not ax[1, 0]:
                a.set(xscale="log", xlabel="concurrency N")
                a.set_xticks(n, n)
                a.minorticks_off()
        fig.suptitle(f"FIFO baseline, {label}: {rows.n_tasks.iloc[0]} tasks at {rows.arrival_rate.iloc[0]} tasks/s, "
                     f"KV budget {rows.ctx.iloc[0]} tokens [{P1.parent.name}]")
        savefig(fig, f"concurrency_{tr}")

        # completion-time breakdown -> dominant bottleneck per N
        parts = pd.DataFrame({k: breakdown(k) for k in rows.index}).T
        fig, ax = plt.subplots(figsize=(7, 4))
        parts.plot.bar(stacked=True, ax=ax, width=0.8)
        ax.set_xticks(range(len(rows)), [f"N={c}" for c in n], rotation=0)
        ax.set(ylabel="mean seconds per finished task", title=f"Where task time goes, {label}")
        savefig(fig, f"breakdown_{tr}")
        best = rows.throughput_tasks_min.idxmax()
        for name, row in rows.iterrows():
            dom = parts.loc[name].idxmax()
            lines.append(f"| {label} | N={row.concurrency} | {row.ok}/{row.n_tasks} | {row.failed} | "
                         f"{row.timeout_queued + row.timeout_running} | {row.throughput_tasks_min:.1f} | "
                         f"{row.completion_p50_s:.1f} / {row.completion_p95_s:.1f} / {row.completion_p99_s:.1f} | "
                         f"{row.queue_p95_s:.1f} | {row.peak_footprint_mib:.0f} | {row.cache_hit_ratio:.2f} | "
                         f"{dom}{' (best throughput)' if name == best else ''} |")


def factor(s: pd.DataFrame, names: dict, title: str, fname: str, xlabel: str):
    rows = s.loc[[n for n in names if n in s.index]]
    if len(rows) < 2:
        return
    x = [names[n] for n in rows.index]
    fig, ax = plt.subplots(1, 4, figsize=(16, 3.8))
    ax[0].bar(x, rows.ok / rows.n_tasks, label="ok")
    ax[0].bar(x, rows.failed / rows.n_tasks, bottom=rows.ok / rows.n_tasks, label="failed")
    ax[0].bar(x, (rows.timeout_queued + rows.timeout_running) / rows.n_tasks,
              bottom=(rows.ok + rows.failed) / rows.n_tasks, label="timeout")
    ax[0].set(title="Task outcomes", ylabel="share")
    ax[0].legend(fontsize=7)
    ax[1].bar(x, rows.completion_p95_s, label="p95")
    ax[1].bar(x, rows.completion_p50_s, label="p50")
    ax[1].set(title="Completion time (s)")
    ax[1].legend(fontsize=7)
    ax[2].bar(x, rows.prefill_tokens_computed / 1e3)
    ax[2].set(title="Prompt tokens computed (k)")
    ax[3].bar(x, rows.peak_footprint_mib)
    ax[3].set(title="Peak footprint (MiB)")
    for a in ax:
        a.set_xlabel(xlabel)
        a.tick_params(axis="x", rotation=15)
    fig.suptitle(title)
    savefig(fig, fname)


def timelines(s: pd.DataFrame):
    names = [n for n in ("syn_N4", "syn_N16", "syn_N32", "ai_N16") if n in s.index]
    if not names:
        return
    fig, ax = plt.subplots(len(names), 1, figsize=(10, 2.6 * len(names)), sharex=False)
    ax = np.atleast_1d(ax)
    for a, n in zip(ax, names):
        d = run_data(n)
        a.plot(d["memory"].t, d["memory"].footprint_mib, "C3", lw=1, label="footprint (MiB)")
        a.set_ylabel("MiB", color="C3")
        b = a.twinx()
        b.step(d["timeline"].t, d["timeline"].queued, "C0", where="post", lw=1, label="queued tasks")
        b.step(d["timeline"].t, d["timeline"].running, "C2", where="post", lw=1, label="running tasks")
        b.set_ylabel("tasks")
        fails = d["requests"][d["requests"].error.notna()]
        for t in fails.t_rel:
            a.axvline(t, color="k", lw=0.4, alpha=0.4)
        a.set_title(f"{n}: memory, queue and running tasks over time (vertical lines: failed requests)", fontsize=9)
        b.legend(loc="upper left", fontsize=7)
    ax[-1].set_xlabel("seconds since start")
    savefig(fig, "timelines")


def per_class(s: pd.DataFrame):
    names = [n for n in ("syn_N8", "ai_N8") if n in s.index]
    if not names:
        return
    fig, ax = plt.subplots(1, len(names), figsize=(6 * len(names), 4))
    ax = np.atleast_1d(ax)
    for a, n in zip(ax, names):
        t = run_data(n)["tasks"]
        for cls, g in t[t.status == "ok"].groupby("cls"):
            v = np.sort(g.completion_s)
            a.plot(v, np.arange(1, len(v) + 1) / len(v), label=f"{cls} ({len(v)})")
        a.set(xscale="log", xlabel="completion time (s)", ylabel="CDF", title=f"{n}: completion by task class")
        a.legend(fontsize=7)
    savefig(fig, "per_class")


def main():
    import argparse
    from harness.platform import PLATFORMS, DEFAULT
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--platform", choices=PLATFORMS, default=DEFAULT, help="whose results to plot")
    select(ap.parse_args().platform)
    s = summaries()
    if s.empty:
        print("no phase1 results yet")
        return
    lines = ["| workload | N | ok | failed | timeouts | tasks/min | completion p50/p95/p99 (s) | queue p95 (s) | "
             "peak MiB | prefix reuse | largest share of task time |", "|" + "---|" * 11]
    concurrency(s, lines)
    factor(s, {"syn_N16_ctx8192": "8k", "syn_N16": "16k", "syn_N16_ctx32768": "32k"},
           "KV memory budget at N=16 (synthetic)", "budget_syn", "KV budget (tokens)")
    factor(s, {"syn_N8_delay0": "x0", "syn_N8": "x1", "syn_N8_delay4": "x4"},
           "Tool delay scale at N=8 (synthetic)", "idle_syn", "tool delay")
    for tr, label in TRACES.items():
        factor(s, {f"{tr}_N8": "default", f"{tr}_N8_noram": "no host cache", f"{tr}_N8_noreuse": "no prefix reuse"},
               f"Retained contexts and prefix reuse at N=8 ({label})", f"retention_{tr}", "configuration")
    timelines(s)
    per_class(s)
    s.to_csv(P1 / "summary.csv")
    (P1 / "summary.md").write_text("# Phase 1 summary\n\n" + "\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
