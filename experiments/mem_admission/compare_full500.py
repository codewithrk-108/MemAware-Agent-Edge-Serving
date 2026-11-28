"""Compare E6 (Exp 4 cap 2,048 + class x step lookup order) on the 500-task set with the Phase 1 FIFO N4 baseline.

Both runs: data/traces/agenttrace_full_500_seed2.jsonl, 500 tasks, Poisson mean 5 s, seed 2 (identical task order and
arrival times), Qwen3-0.6B Q8_0, unified 32K pool, --cache-ram 0, 21,600 s deadline. No solo latencies exist for these
500 tasks, so slowdown / SLO are not computed. Failed tasks are excluded from latency statistics (as in Phase 1) and
counted separately. Writes results/mem_admission/compare_full500.{csv,md}.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
TRACE = [json.loads(l) for l in (ROOT / "data/traces/agenttrace_full_500_seed2.jsonl").open()]
RUNS = {
    "Phase 1 FIFO N4 (baseline)": ROOT / "results/t4_full500_v1_part_a/t4_full500_v1_c2_n4_poisson5s_ctx32k_seed2",
    "Phase 1 FIFO N16": ROOT / "results/t4_full500_v1_part_a/t4_full500_v1_c3_n16_poisson5s_ctx32k_seed2",
    "E6: cap 2,048 + lookup order": ROOT / "results/mem_admission/full500_e6_cap2048_lookup",
    "E7a: cap 2,048 + online median order": ROOT / "results/mem_admission/full500_e7a_cap2048_online_median",
    "E7b: cap 2,048 + online model order": ROOT / "results/mem_admission/full500_e7b_cap2048_online_gbm",
    "E8 T=120: E7b + starvation guard 120 s": ROOT / "results/mem_admission/full500_e8a_cap2048_online_gbm_starve120",
    "E8 T=240: E7b + starvation guard 240 s": ROOT / "results/mem_admission/full500_e8_cap2048_online_gbm_starve240",
    "E8 T=600: E7b + starvation guard 600 s": ROOT / "results/mem_admission/full500_e8_cap2048_online_gbm_starve600",
    "E10b: split pool a=0.5, P=E7b, F=mem-aware FCFS": ROOT / "results/mem_admission/full500_e10b_cap2048_gbm_split_a050_fcfs_mem",
    "E10c: E10b + per-partition deadlock breaker (rebuilt binary)": ROOT / "results/mem_admission/full500_e10c_cap2048_gbm_split_a050_fcfs_mem_pbreak",
    "E11b: E7b + slot reuse, own-first slot order (rebuilt binary)": ROOT / "results/mem_admission/full500_e11b_cap2048_gbm_slotreuse_ownfirst",
    "E12: E11b + at most 4 running (rebuilt binary)": ROOT / "results/mem_admission/full500_e12_cap2048_gbm_slotreuse_ownfirst_run4",
    "E12b: E12 with server -np 4 (rebuilt binary)": ROOT / "results/mem_admission/full500_e12b_cap2048_gbm_slotreuse_ownfirst_np4",
}


def metrics(path: Path) -> dict:
    t = pd.read_csv(path / "tasks.csv")
    r = pd.read_csv(path / "requests.csv")
    s = json.loads((path / "meta.json").read_text())["summary"]
    ok = t[t.status == "ok"]
    kv = pd.read_csv(path / "kv_memory.csv")
    out = {"completed": f"{len(ok)}/{len(t)}", "failed": int((t.status != "ok").sum()),
           "kv_context_failures": int(r.error.fillna("").str.contains("Context size", regex=False).sum()),
           "tasks_per_min": s["throughput_tasks_min"], "run_length_min": s["span_s"] / 60,
           "latency_mean_s": ok.completion_s.mean(), "latency_p50_s": ok.completion_s.median(),
           "latency_p90_s": ok.completion_s.quantile(.9), "latency_p95_s": ok.completion_s.quantile(.95),
           "latency_p99_s": ok.completion_s.quantile(.99), "latency_max_s": ok.completion_s.max()}
    for n, g in ok.groupby(ok.n_steps.clip(upper=3)):
        out[f"latency_p50_{n}{'+' if n == 3 else ''}_step_s"] = g.completion_s.median()
    for c, g in ok.groupby("cls"):
        out[f"latency_p50_{c}_s"] = g.completion_s.median()
    rok = r[r.error.isna()]
    out.update({"ttft_step_p50_s": rok.ttft_s.median(), "tpot_p50_ms": rok.tpot_ms.median(),
                "prompt_tokens_computed": rok.prompt_n.sum(), "mean_kv_pct": 100 * kv.used_cells.mean() / 32768,
                "peak_kv_pct": 100 * kv.used_cells.max() / 32768})
    tl = pd.read_csv(path / "timeline.csv")
    dt = tl.t.diff().shift(-1).fillna(0)
    out["mean_running"] = float((tl.running * dt).sum() / dt.sum())
    if "pred" in r.columns and r.pred.notna().any():
        from scipy.stats import kendalltau
        n = r[(~r.continuation.astype(bool)) & r.pred.notna()].drop_duplicates(["task_id", "step"])
        tr = {(t_["task_id"], k): s_["n_gen"] for t_ in TRACE for k, s_ in enumerate(t_["steps"])}
        y = [tr[(a, b)] for a, b in zip(n.task_id, n.step)]
        out["online_tau_b"] = kendalltau(n.pred, y).statistic
        out["requests_with_prediction"] = len(n)
    for k in ("continuations", "deadlock_discards", "max_wait_s", "peak_running", "online_refits",
              "starving_promotions", "starving_admissions", "starve_blocked_s", "admitted_by_P", "admitted_by_F",
              "admitted_oversized", "oversized_fails"):
        if k in s:
            out[k] = s[k]
    return out


def main():
    rows = {k: metrics(v) for k, v in RUNS.items() if (v / "meta.json").exists()}
    df = pd.DataFrame(rows)
    out = ROOT / "results/mem_admission"
    df.to_csv(out / "compare_full500.csv")
    fmt = lambda v: f"{v:,.1f}" if isinstance(v, (float, np.floating)) else (f"{v:,}" if isinstance(v, (int, np.integer)) else str(v))
    lines = ["| metric | " + " | ".join(df.columns) + " |", "|---|" + "---:|" * len(df.columns)]
    lines += [f"| {k} | " + " | ".join(fmt(v) for v in vals) + " |" for k, vals in df.iterrows()]
    (out / "compare_full500.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
