"""Flatten solo-run metadata into CSVs and a per-task latency table.

Writes, per mode, next to task_metadata.jsonl:
  tasks.csv       one row per task-repetition
  requests.csv    one row per model request
  tool_waits.csv  one row per tool wait (planned and measured)
  per_task.csv    latency over repetitions per task: mean, std, cv, min, median, max
and one comparison file for the run:
  compare.csv     per task, retain vs evict mean latency
"""

import argparse
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]


def load(path: Path) -> list:
    rows = []
    for line in path.read_text().splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return rows


def flatten(mode_dir: Path) -> pd.DataFrame:
    recs = load(mode_dir / "task_metadata.jsonl")
    key = ("task_id", "cls", "mode", "rep")
    tasks = pd.DataFrame([{k: v for k, v in r.items() if k not in ("requests", "tool_waits")} for r in recs])
    reqs = pd.DataFrame([{**{k: r[k] for k in key}, **q} for r in recs for q in r["requests"]])
    tools = pd.DataFrame([{**{k: r[k] for k in key}, **t} for r in recs for t in r["tool_waits"]])
    tasks.to_csv(mode_dir / "tasks.csv", index=False)
    reqs.to_csv(mode_dir / "requests.csv", index=False)
    tools.to_csv(mode_dir / "tool_waits.csv", index=False)

    ok = tasks[tasks.status == "ok"]
    g = ok.groupby(["task_id", "cls", "n_steps"])
    per = g.latency_s.agg(reps="count", mean_s="mean", std_s="std", min_s="min", median_s="median", max_s="max")
    per["cv"] = per.std_s / per.mean_s
    per["model_mean_s"] = g.model_s.mean()
    per["tool_mean_s"] = g.tool_s.mean()
    per["prefill_computed_mean"] = g.prefill_tokens_computed.mean()
    per["peak_kv_cells_max"] = g.peak_kv_used_cells.max()
    per = per.reset_index()
    per.to_csv(mode_dir / "per_task.csv", index=False)

    n_fail = int((tasks.status != "ok").sum())
    print(f"== {mode_dir.name}: {len(tasks)} task-reps, {n_fail} failed, {len(reqs)} requests, {len(tools)} tool waits")
    print(f"   task latency (mean over reps) p50 {per.mean_s.median():.1f}s p95 {per.mean_s.quantile(.95):.1f}s "
          f"max {per.mean_s.max():.1f}s | per-task CV median {per.cv.median():.4f} max {per.cv.max():.4f}")
    if len(reqs):
        rok = reqs[reqs.error.isna()]
        print(f"   request e2e p50 {rok.e2e_s.median():.2f}s | TTFT p50 {rok.ttft_s.median()*1e3:.0f}ms "
              f"p95 {rok.ttft_s.quantile(.95)*1e3:.0f}ms | TPOT p50 {rok.tpot_ms.median():.2f}ms | "
              f"prefill computed {int(rok.prompt_n.sum())} reused {int(rok.cache_n.sum())}")
    if len(tools):
        print(f"   tool wait planned {tools.planned_s.sum():.1f}s actual {tools.actual_s.sum():.1f}s "
              f"(max overshoot {(tools.actual_s - tools.planned_s).max()*1e3:.1f}ms)")
    return per


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--name", default="smoke50_v1")
    a = p.parse_args()
    out = ROOT / "results" / "solo_SLO" / a.name
    per = {d.name: flatten(d) for d in sorted(out.iterdir()) if (d / "task_metadata.jsonl").exists()}
    if {"retain", "evict"} <= per.keys():
        c = per["retain"][["task_id", "cls", "n_steps", "mean_s"]].merge(
            per["evict"][["task_id", "mean_s"]], on="task_id", suffixes=("_retain", "_evict"))
        c["evict_minus_retain_s"] = c.mean_s_evict - c.mean_s_retain
        c["evict_over_retain"] = c.mean_s_evict / c.mean_s_retain
        c.to_csv(out / "compare.csv", index=False)
        multi = c[c.n_steps > 1]
        print(f"== evict vs retain ({len(multi)} multi-step tasks): extra latency median "
              f"{multi.evict_minus_retain_s.median():.3f}s max {multi.evict_minus_retain_s.max():.3f}s, "
              f"ratio median {multi.evict_over_retain.median():.4f} max {multi.evict_over_retain.max():.4f}")


if __name__ == "__main__":
    main()
