"""SLO benchmark on the 500-task set: FIFO N4 vs E11b, 3 runs each, against per-task solo latency.

Solo latency (the SLO base) = mean over the solo repetitions (retain mode, task alone on the GPU) from
results/solo_SLO/<SOLO>/retain/task_metadata.jsonl. slowdown(task, run) = completion under load / solo latency; a failed
task has slowdown = inf (always misses). SLO(alpha) is met if slowdown <= alpha.

Every metric is computed per run, then reported as mean +- std over the runs of a system. Per-task views average each
task over its runs (mean completion; SLO met share = fraction of runs that met it). Segregations: task class, step
count, solo-latency tercile (short / medium / long tasks), and arrival half.

Usage: python -m experiments.mem_admission.slo_benchmark SOLO_NAME OUT_DIR "LABEL=dir1,dir2,dir3" "LABEL=..."
Directories are relative to the repository root; the first system is the reference for head-to-head counts.
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
ALPHAS = (2, 5, 10, 20, 50, 100, 200, 500)
SEG_ALPHAS = (20, 50, 100, 200)   # segment tables; under 500-task overload slowdowns are ~100-400x
FIG_ALPHA = 100
# reference palette (dataviz skill, light mode): slot 1 blue, slot 2 orange; text inks; recessive grid
COLORS = ["#257de0", "#f47230", "#00b894", "#f2a600"]
INK, INK2, GRID = "#191919", "#666666", "#e6e6e6"
plt.rcParams.update({"font.size": 10, "axes.edgecolor": INK2, "axes.labelcolor": INK, "xtick.color": INK2,
                     "ytick.color": INK2, "axes.spines.top": False, "axes.spines.right": False,
                     "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8, "axes.axisbelow": True,
                     "legend.frameon": False, "figure.dpi": 130})


def load_solo(name: str) -> pd.DataFrame:
    rows = [json.loads(l) for l in (ROOT / "results/solo_SLO" / name / "retain/task_metadata.jsonl").open()]
    s = pd.DataFrame(rows)
    s = s[s.status == "ok"]
    g = s.groupby("task_id").latency_s
    return pd.DataFrame({"solo_s": g.mean(), "solo_std_s": g.std(), "solo_reps": g.size()})


def load_run(d: Path, solo: pd.DataFrame) -> pd.DataFrame:
    t = pd.read_csv(ROOT / d / "tasks.csv").set_index("task_id")
    t["arr_rel"] = t.arrival - t.arrival.min()
    t = t.join(solo, how="inner")
    t["ok"] = t.status == "ok"
    t["slowdown"] = np.where(t.ok, t.completion_s / t.solo_s, np.inf)
    return t


def run_metrics(t: pd.DataFrame, full: pd.DataFrame) -> dict:
    ok = full[full.status == "ok"]
    span = full.finish.max() - full.arrival.min()
    s = t.slowdown
    m = {"completed": len(ok), "tasks/min": len(ok) / (span / 60),
         "latency p50 s": ok.completion_s.median(), "latency p95 s": ok.completion_s.quantile(.95),
         "latency p99 s": ok.completion_s.quantile(.99), "latency mean s": ok.completion_s.mean(),
         "slowdown p50": s.quantile(.5, interpolation="nearest"), "slowdown p90": s.quantile(.9, interpolation="nearest"),
         "slowdown p95": s.quantile(.95, interpolation="nearest"), "slowdown p99": s.quantile(.99, interpolation="nearest")}
    for a in ALPHAS:
        m[f"SLO {a}x met %"] = 100 * (s <= a).mean()
    return m


def segment(t: pd.DataFrame, terciles) -> pd.DataFrame:
    return pd.DataFrame({
        "class": t.cls,
        "steps": pd.cut(t.n_steps, [0, 1, 2, 99], labels=["1 step", "2 steps", "3+ steps"]).astype(str),
        "solo length": pd.cut(t.solo_s, terciles, labels=["short", "medium", "long"], include_lowest=True).astype(str),
        "arrival": np.where(t.arr_rel <= t.arr_rel.median(), "first half", "second half")}, index=t.index)


def fmt(mean, std):
    if not np.isfinite(mean):
        return "inf"
    d = 1 if abs(mean) < 1000 else 0
    return f"{mean:,.{d}f} ± {std:,.{d}f}" if np.isfinite(std) else f"{mean:,.{d}f}"


def main():
    solo_name, out = sys.argv[1], ROOT / sys.argv[2]
    out.mkdir(parents=True, exist_ok=True)
    systems = {}
    for arg in sys.argv[3:]:
        label, dirs = arg.rsplit("=", 1)
        systems[label] = [Path(x) for x in dirs.split(",") if (ROOT / x / "tasks.csv").exists()]
    solo = load_solo(solo_name)
    runs = {k: [load_run(d, solo) for d in v] for k, v in systems.items()}
    fulls = {k: [pd.read_csv(ROOT / d / "tasks.csv") for d in v] for k, v in systems.items()}
    names = list(runs)
    lines = [f"# SLO benchmark: {' vs '.join(names)}", "",
             f"Solo base: `results/solo_SLO/{solo_name}` (retain mode), {len(solo)} tasks, "
             f"reps per task: {int(solo.solo_reps.min())}–{int(solo.solo_reps.max())}; solo latency p50 "
             f"{solo.solo_s.median():.1f} s, max {solo.solo_s.max():.1f} s; median rep-to-rep std "
             f"{100 * (solo.solo_std_s / solo.solo_s).median():.2f}% of the mean.", "",
             "Runs: " + "; ".join(f"{k}: {len(v)} ({', '.join(str(d) for d in systems[k])})" for k, v in runs.items()),
             "", f"Tasks scored (have a solo latency): {len(runs[names[0]][0])}.", ""]

    # 1. headline metrics, mean +- std over runs
    per_run = {k: pd.DataFrame([run_metrics(t, f) for t, f in zip(runs[k], fulls[k])]) for k in names}
    head = pd.DataFrame({k: {c: fmt(v[c].mean(), v[c].std()) for c in v.columns} for k, v in per_run.items()})
    lines += ["## 1. Headline metrics (mean ± std over runs)", "", head.to_markdown(), ""]
    pd.concat(per_run, names=["system", "run"]).to_csv(out / "per_run_metrics.csv")

    # 2. per-task averages and head-to-head
    pt = {}
    for k in names:
        stack = pd.concat([t[["completion_s", "ok", "slowdown"]].assign(run=i) for i, t in enumerate(runs[k])])
        g = stack.groupby(level=0)
        okmean = stack[stack.ok].groupby(level=0).completion_s.mean()
        pt[k] = pd.DataFrame({"runs_ok": g.ok.sum(), "runs": g.size(), "mean_completion_s": okmean})
        for a in ALPHAS:
            pt[k][f"met_{a}x_share"] = (stack.slowdown <= a).groupby(level=0).mean()
        pt[k]["mean_slowdown"] = np.where(pt[k].runs_ok == pt[k].runs, pt[k].mean_completion_s / solo.solo_s.reindex(pt[k].index), np.inf)
    base = runs[names[0]][0]
    tasks = pd.concat({k: v for k, v in pt.items()}, axis=1)
    tasks.columns = [f"{k} · {c}" for k, c in tasks.columns]
    tasks = tasks.join(base[["cls", "n_steps", "solo_s", "arr_rel"]])
    tasks.to_csv(out / "per_task.csv")
    ref = names[0]
    hh = []
    for k in names[1:]:
        a, b = pt[k].mean_slowdown, pt[ref].mean_slowdown
        hh.append({"system": k, f"tasks with lower mean slowdown than {ref}": int((a < b).sum()),
                   "higher": int((a > b).sum()), "equal (incl. both failed)": int((a == b).sum()),
                   "median ratio (system / ref), finite only": float(np.median((a / b)[np.isfinite(a / b)])),
                   f"tasks meeting {FIG_ALPHA}x in all runs": int((pt[k][f"met_{FIG_ALPHA}x_share"] == 1).sum()),
                   f"{ref} tasks meeting {FIG_ALPHA}x in all runs": int((pt[ref][f"met_{FIG_ALPHA}x_share"] == 1).sum())})
    lines += ["## 2. Per task, averaged over runs: head-to-head", "", pd.DataFrame(hh).set_index("system").T.to_markdown(), ""]

    # 3. segregations: SLO attainment and slowdown by segment (mean over runs)
    terc = list(np.quantile(solo.solo_s.reindex(base.index), [0, 1 / 3, 2 / 3, 1]))
    seg_rows = []
    for k in names:
        for i, t in enumerate(runs[k]):
            sg = segment(t, terc)
            for dim in sg.columns:
                for val, idx in sg.groupby(dim).groups.items():
                    s = t.slowdown.loc[idx]
                    r = {"system": k, "run": i, "dimension": dim, "segment": val, "tasks": len(s),
                         "slowdown p50": s.quantile(.5, interpolation="nearest"),
                         "slowdown p90": s.quantile(.9, interpolation="nearest")}
                    for a in SEG_ALPHAS:
                        r[f"SLO {a}x %"] = 100 * (s <= a).mean()
                    seg_rows.append(r)
    seg = pd.DataFrame(seg_rows)
    seg.to_csv(out / "segments_per_run.csv", index=False)
    agg = seg.groupby(["dimension", "segment", "system"]).agg(
        tasks=("tasks", "first"), **{c: (c, "mean") for c in ["slowdown p50", "slowdown p90"] + [f"SLO {a}x %" for a in SEG_ALPHAS]}).reset_index()
    lines += [f"## 3. Segregations (mean over runs; solo-length terciles at {terc[1]:.1f} s and {terc[2]:.1f} s)", ""]
    for dim in ["class", "steps", "solo length", "arrival"]:
        a = agg[agg.dimension == dim].drop(columns="dimension")
        piv = a.pivot_table(index=["segment", "tasks"], columns="system",
                            values=["slowdown p50"] + [f"SLO {a}x %" for a in SEG_ALPHAS])
        piv.columns = [f"{v} · {s}" for v, s in piv.columns]
        lines += [f"### By {dim}", "", piv.round(1).to_markdown(), ""]

    # 4. run-to-run spread
    spread = {k: {"tasks/min range": f"{v['tasks/min'].min():.2f}–{v['tasks/min'].max():.2f}",
                  "latency p95 range s": f"{v['latency p95 s'].min():,.0f}–{v['latency p95 s'].max():,.0f}",
                  f"SLO {FIG_ALPHA}x range %": f"{v[f'SLO {FIG_ALPHA}x met %'].min():.1f}–{v[f'SLO {FIG_ALPHA}x met %'].max():.1f}"}
              for k, v in per_run.items()}
    lines += ["## 4. Run-to-run spread", "", pd.DataFrame(spread).to_markdown(), ""]
    (out / "slo_benchmark.md").write_text("\n".join(lines))
    print("\n".join(lines))

    # ---------- figures ----------
    alphas = np.logspace(0, np.log10(1000), 100)
    fig, ax = plt.subplots(figsize=(7.2, 3.8))
    for c, k in zip(COLORS, names):
        curves = np.array([[100 * (t.slowdown <= a).mean() for a in alphas] for t in runs[k]])
        ax.plot(alphas, curves.mean(0), color=c, linewidth=2, label=f"{k} (mean of {len(runs[k])} runs)")
        if len(curves) > 1:
            ax.fill_between(alphas, curves.min(0), curves.max(0), color=c, alpha=0.15, linewidth=0)
    ax.set_xscale("log")
    ax.set_xlabel("SLO = α × solo latency (log scale)")
    ax.set_ylabel("tasks meeting the SLO (%)")
    ax.set_ylim(0, 100)
    ax.set_title("SLO attainment vs SLO tightness (band = min–max over runs)", loc="left", color=INK)
    ax.legend(fontsize=8, loc="lower right")
    fig.tight_layout()
    fig.savefig(out / "fig1_slo_attainment_curve.png")

    fig, ax = plt.subplots(figsize=(7.2, 3.6))
    dims = [("class", ["mbpp", "nl2bash"]), ("steps", ["1 step", "2 steps", "3+ steps"]),
            ("solo length", ["short", "medium", "long"]), ("arrival", ["first half", "second half"])]
    labels, x = [], 0
    w = 0.8 / len(names)
    for dim, vals in dims:
        for val in vals:
            for i, (c, k) in enumerate(zip(COLORS, names)):
                r = agg[(agg.dimension == dim) & (agg.segment == val) & (agg.system == k)]
                if len(r):
                    ax.bar(x + (i - (len(names) - 1) / 2) * w, r[f"SLO {FIG_ALPHA}x %"].iloc[0], w * 0.92, color=c,
                           label=k if (dim, val) == dims[0][0:1] + (dims[0][1][0],) else None)
            labels.append(f"{val}\n({dim})" if val in ("mbpp", "1 step", "short", "first half") else val)
            x += 1
        x += 0.4
    pos = []
    x = 0
    for dim, vals in dims:
        for _ in vals:
            pos.append(x)
            x += 1
        x += 0.4
    ax.set_xticks(pos, labels, fontsize=7)
    ax.set_ylabel(f"tasks meeting {FIG_ALPHA}× SLO (%)")
    ax.set_ylim(0, 100)
    ax.grid(axis="x", visible=False)
    ax.set_title(f"SLO {FIG_ALPHA}× attainment by segment (mean over runs)", loc="left", color=INK)
    handles = [plt.Rectangle((0, 0), 1, 1, color=c) for c in COLORS[:len(names)]]
    ax.legend(handles, names, fontsize=8, loc="upper right")
    fig.tight_layout()
    fig.savefig(out / f"fig2_slo{FIG_ALPHA}_by_segment.png")

    if len(names) >= 2:
        a, b = pt[names[1]].mean_slowdown, pt[ref].mean_slowdown
        m = np.isfinite(a) & np.isfinite(b)
        fig, ax = plt.subplots(figsize=(5.2, 5.0))
        ax.scatter(b[m], a[m], s=12, alpha=0.6, color=COLORS[0], edgecolor="none")
        lim = [min(a[m].min(), b[m].min()) * 0.8, max(a[m].max(), b[m].max()) * 1.2]
        ax.plot(lim, lim, color=INK2, linewidth=1, linestyle="--")
        ax.set_xscale("log"), ax.set_yscale("log"), ax.set_xlim(lim), ax.set_ylim(lim)
        ax.set_xlabel(f"{ref}: mean slowdown per task")
        ax.set_ylabel(f"{names[1]}: mean slowdown per task")
        below, above = int((a[m] < b[m]).sum()), int((a[m] > b[m]).sum())
        ax.text(0.03, 0.95, f"below diagonal ({names[1]} better): {below}\nabove: {above}\n"
                f"failed in some run (not shown): {int((~m).sum())}", transform=ax.transAxes, va="top",
                fontsize=8, color=INK2)
        ax.set_title("Per-task slowdown, averaged over runs", loc="left", color=INK)
        fig.tight_layout()
        fig.savefig(out / "fig3_per_task_slowdown_scatter.png")

    fig, ax = plt.subplots(figsize=(7.2, 3.4))
    for c, k in zip(COLORS, names):
        for i, t in enumerate(runs[k]):
            s = np.sort(t.slowdown[np.isfinite(t.slowdown)].values)
            ax.step(s, np.arange(1, len(s) + 1) / len(t) * 100, where="post", color=c, linewidth=1.6,
                    alpha=0.9 if i == 0 else 0.45, label=k if i == 0 else None)
    ax.set_xscale("log")
    ax.set_xlabel("slowdown (completion / solo latency, log scale)")
    ax.set_ylabel("tasks (%)")
    ax.set_title("Slowdown CDF, every run (failed tasks never reach 100%)", loc="left", color=INK)
    ax.legend(fontsize=8, loc="lower right")
    fig.tight_layout()
    fig.savefig(out / "fig4_slowdown_cdf.png")
    print("figures in", out)


if __name__ == "__main__":
    main()
