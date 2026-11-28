"""Per-task SLO comparison against standalone (solo) task latency.

slowdown(task) = completion time under load / solo latency of the same task (Study A, retain mode, mean of 3 reps,
results/solo_SLO/smoke50_v1/compare.csv). A task meets SLO(alpha) if slowdown <= alpha; a failed task always misses.
Only tasks with a solo latency are scored (the 50-task smoke set).

Usage: python -m experiments.mem_admission.slo_compare OUT.md LABEL=RUN_DIR [LABEL=RUN_DIR ...]
The first run is the reference for the per-task head-to-head.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SOLO = ROOT / "results/solo_SLO/smoke50_v1/compare.csv"
ALPHAS = (2, 5, 10, 20, 50)


def per_task(d: Path, solo: pd.Series) -> pd.DataFrame:
    t = pd.read_csv(d / "tasks.csv").set_index("task_id")
    t = t[t.index.isin(solo.index)].copy()
    t["solo_s"] = solo.reindex(t.index)
    t["slowdown"] = np.where(t.status == "ok", t.completion_s / t.solo_s, np.inf)
    return t


def main():
    out = ROOT / sys.argv[1]
    runs = dict(a.rsplit("=", 1) for a in sys.argv[2:])
    solo = pd.read_csv(SOLO).set_index("task_id").mean_s_retain
    tabs = {k: per_task(ROOT / v, solo) for k, v in runs.items()}
    rows = {}
    for k, t in tabs.items():
        s = t.slowdown
        r = {"tasks scored": len(t), "failed": int((t.status != "ok").sum()),
             "slowdown p50": s.quantile(.5, interpolation="nearest"), "slowdown p90": s.quantile(.9, interpolation="nearest"),
             "slowdown p95": s.quantile(.95, interpolation="nearest"),
             "slowdown mean (ok only)": s[np.isfinite(s)].mean()}
        for a in ALPHAS:
            r[f"SLO {a}x met %"] = 100 * (s <= a).mean()
        rows[k] = r
    ref_name = next(iter(tabs)); ref = tabs[ref_name].slowdown
    for k, t in tabs.items():
        s = t.slowdown.reindex(ref.index)
        rows[k][f"tasks faster than {ref_name}"] = int((s < ref).sum())
        rows[k][f"tasks slower than {ref_name}"] = int((s > ref).sum())
    df = pd.DataFrame(rows)
    txt = (f"Slowdown = completion under load / solo latency (retain mode, Study A). Failed tasks count as misses "
           f"(slowdown = inf). Reference for head-to-head: {ref_name}.\n\n" + df.to_markdown(floatfmt=".1f") + "\n")
    # by class and step count
    parts = []
    for k, t in tabs.items():
        g = t.assign(steps=pd.cut(t.n_steps, [0, 1, 2, 99], labels=["1 step", "2 steps", "3+ steps"]))
        for (c, st), gg in g.groupby(["cls", "steps"], observed=True):
            parts.append({"run": k, "class": c, "steps": st, "tasks": len(gg),
                          "slowdown p50": gg.slowdown.median(), "SLO 10x met %": 100 * (gg.slowdown <= 10).mean()})
    txt += "\n## By class and step count\n\n" + pd.DataFrame(parts).pivot_table(
        index=["class", "steps"], columns="run", values=["slowdown p50", "SLO 10x met %"]).to_markdown(floatfmt=".1f") + "\n"
    out.write_text(txt)
    pd.concat({k: t[["cls", "n_steps", "status", "completion_s", "solo_s", "slowdown"]] for k, t in tabs.items()},
              names=["run"]).to_csv(out.with_suffix(".csv"))
    print(txt)


if __name__ == "__main__":
    main()
