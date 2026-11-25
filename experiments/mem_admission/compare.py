"""Compare experiment 4 (caps 1,024 / 2,048 / 4,096 / 8,192) with the FIFO N4 baseline, experiment 2 and experiment 3.

Uses experiments/mlfq_fastserve/compare.row for the shared metrics (completion, throughput, latency, slowdown and
SLO attainment against solo latency, time per token, KV use) and adds, for every run:
  mean_running        time-weighted mean number of running requests (timeline.csv)
  active_kv_pct       estimated KV of running requests (prompt + output so far), mean over kv_memory samples
  overflow_events     distinct times at which requests failed with "Context size has been exceeded"
and for experiment 4: continuations, deadlock discards, admissions that overtook an older waiting request,
longest wait, peak running, peak reserved vs peak actual KV.
Writes results/mem_admission/compare_smoke.csv and .md.
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd

from experiments.mlfq_fastserve.compare import row, SOLO

ROOT = Path(__file__).resolve().parents[2]
RES = ROOT / "results"
RUNS = {
    "Baseline FIFO N4": RES / "kv_policy/smoke_baseline_fifo_n4",
    "Exp 2: KV policy, returning first": RES / "kv_policy/smoke_exp2_policy_front",
    "Exp 3: MLFQ": RES / "mlfq_fastserve/smoke_exp3_mlfq",
    "Exp 4 (oracle below cap), cap 1,024": RES / "mem_admission/smoke_exp4_cap1024",
    "Exp 4 (oracle below cap), cap 2,048": RES / "mem_admission/smoke_exp4_cap2048",
    "Exp 4 (oracle below cap), cap 4,096": RES / "mem_admission/smoke_exp4_cap4096",
    "Exp 4 (oracle), cap 8,192": RES / "mem_admission/smoke_exp4_cap8192",
    "Exp 4 fixed cap 1,024": RES / "mem_admission/smoke_exp4fc_cap1024",
    "Exp 4 fixed cap 2,048": RES / "mem_admission/smoke_exp4fc_cap2048",
    "Exp 4 fixed cap 4,096": RES / "mem_admission/smoke_exp4fc_cap4096",
    "Exp 4 fixed cap 8,192": RES / "mem_admission/smoke_exp4fc_cap8192",
    "E5a: cap 2,048 + starvation 60 s": RES / "mem_admission/smoke_e5_cap2048_starve60",
    "E5b: cap 2,048 + starvation 120 s": RES / "mem_admission/smoke_e5_cap2048_starve120",
    "E6: cap 2,048 + lookup order (tau 0.33)": RES / "mem_admission/smoke_e6_cap2048_lookup",
}


def extra(path: Path) -> dict:
    tl = pd.read_csv(path / "timeline.csv")
    kv = pd.read_csv(path / "kv_memory.csv")
    r = pd.read_csv(path / "requests.csv")
    dt = tl.t.diff().shift(-1).fillna(0)
    out = {"mean_running": float((tl.running * dt).sum() / dt.sum()) if dt.sum() else np.nan}
    t = kv.t.values
    act = np.zeros(len(t))
    for _, q in r.iterrows():
        if pd.isna(q.get("e2e_s")):
            continue
        s, e = q.t_rel, q.t_rel + q.e2e_s
        ttft = q.ttft_s if pd.notna(q.ttft_s) else q.e2e_s
        gen = q.predicted_n if pd.notna(q.predicted_n) else 0
        m = (t >= s) & (t < e)
        act[m] += q.n_prompt + gen * np.clip((t[m] - s - ttft) / max(e - s - ttft, 1e-6), 0, 1)
    out["active_kv_pct"] = 100 * act.mean() / 32768
    f = r[r.error.fillna("").str.contains("Context size", regex=False)]
    out["overflow_events"] = int((f.t_rel + f.e2e_s).round(0).nunique()) if len(f) else 0
    s = json.loads((path / "meta.json").read_text())["summary"]
    for k in ("continuations", "steps_needing_continuation", "deadlock_discards", "admissions_with_overtake",
              "max_wait_s", "peak_running", "peak_reserved", "starving_promotions", "starving_admissions",
              "new_wait_p50_s", "new_wait_p95_s", "starve_blocked_s"):
        if k in s:
            out[k] = s[k]
    if "peak_reserved" in s:
        out["peak_kv_used"] = int(kv.used_cells.max())
    return out


def main():
    solo = pd.read_csv(SOLO).set_index("task_id").mean_s
    rows = [{**row(k, v, solo), **extra(v)} for k, v in RUNS.items() if (v / "meta.json").exists()]
    df = pd.DataFrame(rows)
    out = RES / "mem_admission"
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "compare_smoke.csv", index=False)
    table = df.set_index("run").T
    fmt = lambda v: f"{v:,.1f}" if isinstance(v, (float, np.floating)) else str(v)
    lines = ["| metric | " + " | ".join(table.columns) + " |", "|---|" + "---:|" * len(table.columns)]
    lines += [f"| {k} | " + " | ".join(fmt(v) for v in vals) + " |" for k, vals in table.iterrows()]
    (out / "compare_smoke.md").write_text("\n".join(lines) + "\n")
    with pd.option_context("display.width", 250, "display.max_columns", 10, "display.max_colwidth", 30):
        print(table.to_string())


if __name__ == "__main__":
    main()
