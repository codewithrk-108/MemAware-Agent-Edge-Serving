"""Compare experiment 3 (MLFQ) with the plain FIFO N4 baseline and experiment 2 on the same 50-task workload.

Besides completion and throughput, every task's latency is compared with its solo latency
(results/solo_SLO/smoke50_v1/retain/per_task.csv, the same 50 tasks run alone):
  slowdown        task latency / solo latency, over completed tasks
  SLO attainment  share of ALL tasks that completed within alpha x solo latency (failed tasks count as misses)
Writes results/mlfq_fastserve/compare_smoke.csv and .md.
"""

from pathlib import Path

import json
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
RES = ROOT / "results"
RUNS = {
    "Baseline FIFO N4": RES / "kv_policy/smoke_baseline_fifo_n4",
    "Exp 2: KV policy, returning first": RES / "kv_policy/smoke_exp2_policy_front",
    "Exp 3: MLFQ (FastServe-style)": RES / "mlfq_fastserve/smoke_exp3_mlfq",
}
SOLO = RES / "solo_SLO/smoke50_v1/retain/per_task.csv"
ALPHAS = (2, 5, 10, 20)
CLASSES = {"1 step": (1, 1), "2 steps": (2, 2), "3+ steps": (3, 99)}


def row(label: str, path: Path, solo: pd.Series) -> dict:
    s = json.loads((path / "meta.json").read_text())["summary"]
    t = pd.read_csv(path / "tasks.csv")
    r = pd.read_csv(path / "requests.csv")
    first = r[r.error.isna() & ((r.chunk == 0) if "chunk" in r else True)]
    if "chunk" in r:   # MLFQ: time to first token of a step = first chunk of that step
        first = r[r.error.isna()].sort_values("t_rel").groupby(["task_index", "step"]).head(1)
    ok = t[t.status == "ok"].copy()
    ok["slowdown"] = ok.completion_s / ok.task_id.map(solo)
    t["solo"] = t.task_id.map(solo)
    met = lambda a: 100 * ((t.status == "ok") & (t.completion_s <= a * t.solo)).mean()
    out = {"run": label, "completed": f"{len(ok)}/{len(t)}",
           "kv_context_failures": int(s["kv_context_errors"]),
           "tasks_per_min": s["throughput_tasks_min"],
           "latency_mean_s": ok.completion_s.mean(),
           "latency_p50_s": ok.completion_s.median(), "latency_p95_s": ok.completion_s.quantile(.95),
           "slowdown_p50": ok.slowdown.median(), "slowdown_p95": ok.slowdown.quantile(.95)}
    for a in ALPHAS:
        out[f"SLO_{a}x_pct"] = met(a)
    for name, (lo, hi) in CLASSES.items():
        sub = ok[(ok.n_steps >= lo) & (ok.n_steps <= hi)]
        out[f"latency_p50_{name}_s"] = sub.completion_s.median()
    out.update({"ttft_step_p50_ms": 1e3 * first.ttft_s.median(), "ttft_step_p95_ms": 1e3 * first.ttft_s.quantile(.95),
                "tpot_p50_ms": r.tpot_ms.median(), "span_min": s["span_s"] / 60,
                "mean_kv_pct": s.get("kv_mean_usage_ratio", np.nan)})
    kv = pd.read_csv(path / "kv_memory.csv")
    out["mean_kv_pct"], out["peak_kv_pct"] = 100 * kv.usage_ratio.mean(), 100 * kv.usage_ratio.max()
    for k in ("chunk_requests", "preemptions", "demotions", "evictions_swap", "evictions_discard",
              "resume_recomputed_tokens"):
        if k in s:
            out[k] = s[k]
    return out


def main():
    solo = pd.read_csv(SOLO).set_index("task_id").mean_s
    df = pd.DataFrame([row(k, v, solo) for k, v in RUNS.items() if (v / "meta.json").exists()])
    out = RES / "mlfq_fastserve"
    df.to_csv(out / "compare_smoke.csv", index=False)
    table = df.set_index("run").T
    fmt = lambda v: f"{v:,.1f}" if isinstance(v, (float, np.floating)) else str(v)
    lines = ["| metric | " + " | ".join(table.columns) + " |", "|---|" + "---:|" * len(table.columns)]
    lines += [f"| {k} | " + " | ".join(fmt(v) for v in vals) + " |" for k, vals in table.iterrows()]
    (out / "compare_smoke.md").write_text("\n".join(lines) + "\n")
    with pd.option_context("display.width", 220, "display.max_columns", 10, "display.max_colwidth", 40):
        print(table.to_string())


if __name__ == "__main__":
    main()
