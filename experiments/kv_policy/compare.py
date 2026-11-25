"""Compare plain FIFO N4 with experiments 1 and 2 on the same workload.

Rows: the Phase 1 recorded N4 Poisson-5s run (another T4 instance; reference only), the baseline rerun on this
machine, experiment 1 (policy, requeue at back) and experiment 2 (policy, returning requests first).
Writes results/kv_policy/compare_<size>.csv and .md.
"""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
RES = ROOT / "results"
PHASE1 = {"smoke": "t4_load_v1_part_b/t4_load_v1_poisson5s_n4_ctx32k_seed2",
          "full": "t4_full500_v1_part_a/t4_full500_v1_c2_n4_poisson5s_ctx32k_seed2"}


def row(label: str, path: Path) -> dict:
    if not (path / "meta.json").exists():
        return {"run": label, "status": "missing"}
    s = json.loads((path / "meta.json").read_text())["summary"]
    t = pd.read_csv(path / "tasks.csv")
    r = pd.read_csv(path / "requests.csv")
    rok = r[r.error.isna()]
    later = rok[rok.step > 0]
    kv = pd.read_csv(path / "kv_memory.csv") if (path / "kv_memory.csv").exists() else pd.DataFrame()
    out = {
        "run": label,
        "completed": int(s["ok"]), "tasks": int(s["n_tasks"]),
        "completion_pct": 100 * s["ok"] / s["n_tasks"],
        "failed_kv_context": int(s["kv_context_errors"]),
        "timeouts": int(s["timeout_queued"] + s["timeout_running"]),
        "tasks_per_min": s["throughput_tasks_min"],
        "latency_p50_s": s["completion_p50_s"], "latency_p95_s": s["completion_p95_s"],
        "ready_queue_p50_s": s["ready_queue_p50_s"], "ready_queue_p95_s": s["ready_queue_p95_s"],
        "span_min": s["span_s"] / 60,
        "ttft_p50_ms": 1e3 * s["ttft_p50_s"], "ttft_p95_ms": 1e3 * s["ttft_p95_s"], "tpot_p50_ms": s["tpot_p50_ms"],
        "prefill_tokens": int(rok.prompt_n.sum()),
        "later_step_reuse_pct": 100 * later.cache_n.sum() / later.n_prompt.sum() if len(later) else np.nan,
        "kv_mean_pct": 100 * kv.usage_ratio.mean() if len(kv) else np.nan,
        "kv_peak_pct": 100 * kv.usage_ratio.max() if len(kv) else np.nan,
    }
    for k in ("decisions_stay", "decisions_swap", "decisions_discard", "reclaims", "swap_failures",
              "restore_failures", "swap_out_ms_total", "swap_in_ms_total"):
        out[k] = s.get(k, np.nan)
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--size", choices=("smoke", "full"), default="full")
    a = p.parse_args()
    g = RES / "kv_policy"
    rows = [row("Phase 1 recorded FIFO N4", RES / PHASE1[a.size]),
            row("Baseline FIFO N4 (rerun)", g / f"{a.size}_baseline_fifo_n4"),
            row("Exp 1: policy, requeue back", g / f"{a.size}_exp1_policy_back"),
            row("Exp 2: policy, returning first", g / f"{a.size}_exp2_policy_front")]
    df = pd.DataFrame(rows)
    df.to_csv(g / f"compare_{a.size}.csv", index=False)
    table = df.set_index("run").T
    fmt = lambda v: f"{v:,.1f}" if isinstance(v, (float, np.floating)) else str(v)
    lines = ["| metric | " + " | ".join(table.columns) + " |", "|---|" + "---:|" * len(table.columns)]
    lines += [f"| {k} | " + " | ".join(fmt(v) for v in vals) + " |" for k, vals in table.iterrows()]
    (g / f"compare_{a.size}.md").write_text("\n".join(lines) + "\n")
    with pd.option_context("display.width", 200, "display.max_columns", 10):
        print(table.to_string(float_format=lambda x: f"{x:,.1f}"))


if __name__ == "__main__":
    main()
