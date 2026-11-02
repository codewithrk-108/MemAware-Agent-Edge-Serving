"""Reproduce every table, figure and inline number in the Phase 1 writeup from committed data.

    uv run python -m analysis.reproduce_phase1

Writes to results/reproduced/ (not committed):
    table1_s3_grid.csv      writeup Table 1 (S3, completed tasks and tasks/min)
    table3_full_runs.csv    writeup Table 3 (R1-R7)
    writeup_numbers.csv     every inline number: value printed in the writeup vs. value from the data
    fig_tradeoff.pdf        writeup figure (same code as report/fig_tradeoff.py)

No GPU, model or server is needed. Exit status is 0 if every number matches the writeup at the
precision it prints, 1 otherwise (mismatches are listed).
"""

import argparse
import importlib.util
import statistics
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from analysis import metrics as M

OUT = M.RESULTS / "reproduced"


def fmt_int(x) -> str:
    return f"{x:,.0f}"


def rng(values, spec) -> str:
    lo, hi = min(values), max(values)
    return f"{lo:{spec}}–{hi:{spec}}"


def table1() -> pd.DataFrame:
    rows = []
    for run in M.s3_runs():
        s = M.run_metrics(run)
        rows.append({"arrival": M.arrival_label(s["arrival_rate"]), "N": s["concurrency"],
                     "completed": s["ok"], "n_tasks": s["n_tasks"],
                     "tasks_per_min": round(s["throughput_tasks_min"], 2), "path": s["path"]})
    return pd.DataFrame(rows)


def table3() -> pd.DataFrame:
    rows = []
    for name, run in M.FULL_RUNS.items():
        s = M.run_metrics(run)
        rows.append({"run": name, "N": s["concurrency"], "arrival": M.arrival_label(s["arrival_rate"]),
                     "kv": f"{s['ctx'] // 1024}K", "done": s["ok"], "failed": s["failed"],
                     "success_pct": round(100 * s["ok"] / s["n_tasks"], 1),
                     "p50_s": round(s["completion_p50_s"]), "p95_s": round(s["completion_p95_s"]),
                     "tasks_per_min": round(s["throughput_tasks_min"], 2),
                     "avg_running": round(s["avg_running"], 2),
                     "peak_kv_pct": round(100 * s["kv_peak_usage_ratio"], 1),
                     "gpu_mib": s["gpu_peak_used_mib"], "path": s["path"]})
    return pd.DataFrame(rows)


# Tables as printed in report/phase1_writeup.tex.
WRITEUP_TABLE1 = {  # (arrival, N): "completed (tasks/min)"
    ("Burst", 2): "50 (2.95)", ("Burst", 4): "46 (3.38)", ("Burst", 8): "34 (4.00)", ("Burst", 16): "18 (4.80)",
    ("Poisson 5 s", 2): "50 (2.95)", ("Poisson 5 s", 4): "46 (3.26)", ("Poisson 5 s", 8): "34 (4.14)",
    ("Poisson 5 s", 16): "22 (4.11)",
    ("Poisson 10 s", 2): "50 (2.98)", ("Poisson 10 s", 4): "46 (3.41)", ("Poisson 10 s", 8): "34 (3.41)",
    ("Poisson 10 s", 16): "32 (3.55)",
}
WRITEUP_TABLE3 = {  # run: done, failed, success, p50, p95, tasks/min, avg running, peak KV, GPU MiB
    "R1": "500 | 0 | 100.0% | 4,029 | 7,351 | 2.70 | 1.00 | 49.0% | 4,383",
    "R2": "480 | 20 | 96.0% | 2,874 | 4,768 | 4.21 | 3.99 | 99.9% | 4,383",
    "R3": "177 | 323 | 35.4% | 24 | 58 | 4.36 | 6.40 | 100.0% | 4,383",
    "R4": "472 | 28 | 94.4% | 3,984 | 5,420 | 4.26 | 4.00 | 99.9% | 4,383",
    "R5": "100 | 400 | 20.0% | 127 | 270 | 5.55 | 15.72 | 99.9% | 4,377",
    "R6": "476 | 24 | 95.2% | 4,241 | 6,050 | 4.30 | 4.00 | 99.9% | 4,383",
    "R7": "340 | 160 | 68.0% | 1,959 | 3,631 | 4.48 | 15.05 | 100.0% | 7,999",
}


def writeup_numbers() -> list:
    """[(section, claim, value printed in the writeup, value from the data)]."""
    checks = []

    def add(section, claim, writeup, data):
        checks.append((section, claim, writeup, data))

    for r in table1().itertuples():
        add("Table 1", f"S3 {r.arrival}, N={r.N}: completed (tasks/min)",
            WRITEUP_TABLE1[(r.arrival, r.N)], f"{r.completed} ({r.tasks_per_min:.2f})")
    for r in table3().itertuples():
        data = " | ".join([str(r.done), str(r.failed), f"{r.success_pct:.1f}%", fmt_int(r.p50_s), fmt_int(r.p95_s),
                           f"{r.tasks_per_min:.2f}", f"{r.avg_running:.2f}", f"{r.peak_kv_pct:.1f}%",
                           fmt_int(r.gpu_mib)])
        add("Table 3", f"{r.run} row (done|failed|success|p50|p95|tasks/min|avg run|peak KV|GPU MiB)",
            WRITEUP_TABLE3[r.run], data)

    # S1 feasibility
    s1 = {run: M.run_metrics(run) for run in M.S1_RUNS if (M.RESULTS / run / "meta.json").exists()}
    l4 = [s1[r] for r in M.S1_RUNS[:3]]
    add("S1", "L4 slice, 16K pool: tasks completed (N16, N8)", "2–10 of 50", f"{l4[0]['ok']}–{l4[1]['ok']} of 50")
    add("S1", "L4 slice, 32K pool, N8: tasks completed", "34/50", f"{l4[2]['ok']}/50")
    t4 = [M.run_metrics(r) for r in M.sweep_runs(M.SWEEP_T4)]
    l4s = [M.run_metrics(r) for r in M.sweep_runs(M.SWEEP_L4)]
    add("S1", "Poisson-40 s sweep, T4: task executions completed", "550/550",
        f"{sum(s['ok'] for s in t4)}/{sum(s['n_tasks'] for s in t4)}")
    add("S1", "Poisson-40 s sweep, L4: task executions completed", "468/550",
        f"{sum(s['ok'] for s in l4s)}/{sum(s['n_tasks'] for s in l4s)}")
    n2 = M.SWEEP_RUN.format("N2")
    t4_n2 = M.run_metrics(f"{M.SWEEP_T4}/{n2}")["completion_p50_s"]
    l4_n2 = M.run_metrics(f"{M.SWEEP_L4}/{n2}")["completion_p50_s"]
    add("S1", "Median task time at N=2, T4 vs L4 (s)", "23 vs 767", f"{t4_n2:.0f} vs {l4_n2:.0f}")
    t4_32k = t4 + [M.run_metrics(r) for r in M.s3_runs()] + \
        [s for s in s1.values() if s.get("gpu_total_mib") == 15360 and s["ctx"] == 32768]
    gpu = max(s["gpu_peak_used_mib"] for s in t4_32k)
    add("S1", "T4 device memory with the 32K pool (flat)", "4.4 GiB", f"{gpu / 1024:.1f} GiB")

    # S2 light load (T4 sweep)
    conc = {n: M.run_metrics(f"{M.SWEEP_T4}/{M.SWEEP_RUN.format(k)}") for n, k in M.S2_CONCURRENCY.items()}
    add("S2", "All N=1–16 completed", "50/50", "/".join(sorted({f"{s['ok']}/{s['n_tasks']}" for s in conc.values()})))
    add("S2", "Throughput range across N (tasks/min)", "1.52–1.56", rng([s["throughput_tasks_min"] for s in conc.values()], ".2f"))
    add("S2", "Most requests ever active together", "5", str(max(s["max_running"] for s in conc.values())))
    base = conc[4]
    noreuse = M.run_metrics(f"{M.SWEEP_T4}/{M.SWEEP_RUN.format('N4_noreuse')}")
    delay0 = M.run_metrics(f"{M.SWEEP_T4}/{M.SWEEP_RUN.format('N4_delay0')}")
    add("S2", "Prompt reuse off: prefill tokens ratio", "9.4×",
        f"{noreuse['prefill_tokens_computed'] / base['prefill_tokens_computed']:.1f}×")
    add("S2", "Prompt reuse off: change in median latency (s)", "1.5",
        f"{noreuse['completion_p50_s'] - base['completion_p50_s']:.1f}")
    add("S2", "Tool delays removed: change in median latency (s)", "1.0",
        f"{abs(delay0['completion_p50_s'] - base['completion_p50_s']):.1f}")

    # S3 instrumented grid
    s3 = [M.run_metrics(r) for r in M.s3_runs()]
    add("S3", "Pool occupancy for N>=4 (%)", "97–100", rng([100 * s["kv_peak_usage_ratio"] for s in s3 if s["concurrency"] >= 4], ".0f"))
    add("S3", "GPU memory for N>=4 (MiB)", "4,383", fmt_int(max(s["gpu_peak_used_mib"] for s in s3 if s["concurrency"] >= 4)))
    add("S3", "Failed tasks", "138", str(sum(s["failed"] for s in s3)))
    add("S3", "Failed tasks that were KV-context errors", "138", str(sum(s["kv_context_errors"] for s in s3)))
    burst16 = next(s for s in s3 if s["arrival_rate"] == 0 and s["concurrency"] == 16)
    add("S3", "Burst N=16: tasks failed", "32 of 50", f"{burst16['failed']} of {burst16['n_tasks']}")
    add("S3", "Burst N=16: throughput (tasks/min)", "4.80", f"{burst16['throughput_tasks_min']:.2f}")
    p16 = {s["arrival_rate"]: s["max_running"] for s in s3 if s["concurrency"] == 16 and s["arrival_rate"] > 0}
    add("S3", "Max active requests, N=16, Poisson 10 s / 5 s", "11/14", f"{p16[0.1]}/{p16[0.2]}")
    add("S3", "Size-only backfill opportunities", "32", str(sum(s["backfill_opportunities"] for s in s3)))
    add("S3", "Backfill opportunities at N=16", "0", str(sum(s["backfill_opportunities"] for s in s3 if s["concurrency"] == 16)))
    add("Calibration", "Predicted fits that failed", "34", str(sum(s["est_fit_then_error"] for s in s3)))
    add("Calibration", "Predicted overflows that succeeded", "127", str(sum(s["est_exceed_then_ok"] for s in s3)))
    tpot = [next(s for s in s3 if s["arrival_rate"] == 0 and s["concurrency"] == n)["tpot_p50_ms"] for n in (2, 4, 8, 16)]
    add("Calibration", "Burst median TPOT at N=2/4/8/16 (ms)", "9.9/16.0/24.4/31.2", "/".join(f"{t:.1f}" for t in tpot))

    # Gaps / full runs
    smoke64 = s1["phase1/t4_smoke_sweep_v1/t4_smoke_20_burst_ctx64k_n16_seed2"]
    add("Gaps", "64K pool only tried with 20 tasks: completed", "20/20", f"{smoke64['ok']}/{smoke64['n_tasks']}")
    full = {name: M.run_metrics(run) for name, run in M.FULL_RUNS.items()}
    add("Results", "Failures across R1–R7", "955", str(sum(s["failed"] for s in full.values())))
    add("Results", "Failures that were KV-context errors", "955", str(sum(s["kv_context_errors"] for s in full.values())))
    add("Results", "Timeouts across R1–R7", "0", str(sum(s["timeout_queued"] + s["timeout_running"] for s in full.values())))
    add("Results", "Max share of VRAM used by N>=4 runs (%)", "52",
        f"{max(100 * s['gpu_peak_used_mib'] / s['gpu_total_mib'] for s in full.values() if s['concurrency'] >= 4):.0f}")
    r1 = full["R1"]
    add("Results", "R1 completed / peak KV", "500/500, 49%", f"{r1['ok']}/{r1['n_tasks']}, {100 * r1['kv_peak_usage_ratio']:.0f}%")
    n4 = [full[r] for r in ("R2", "R4", "R6")]
    add("Results", "N4 completion range (%)", "94.4–96.0", rng([100 * s["ok"] / s["n_tasks"] for s in n4], ".1f"))
    add("Results", "N4 throughput range (tasks/min)", "4.21–4.30", rng([s["throughput_tasks_min"] for s in n4], ".2f"))
    add("Results", "N4 throughput relative to R1", "1.56–1.59×",
        rng([s["throughput_tasks_min"] / r1["throughput_tasks_min"] for s in n4], ".2f") + "×")
    add("Results", "N16 completion, R3 and R5 (%)", "35.4 and 20.0",
        f"{100 * full['R3']['ok'] / 500:.1f} and {100 * full['R5']['ok'] / 500:.1f}")
    add("Results", "R5 throughput / survivors / run length", "5.55, 100, 18 min",
        f"{full['R5']['throughput_tasks_min']:.2f}, {full['R5']['ok']}, {full['R5']['span_s'] / 60:.0f} min")
    add("Results", "R5 average running requests", "15.72", f"{full['R5']['avg_running']:.2f}")
    r7 = full["R7"]
    add("Results", "R7 completion / peak KV (%)", "68, 100", f"{100 * r7['ok'] / 500:.0f}, {100 * r7['kv_peak_usage_ratio']:.0f}")
    gain = r7["throughput_tasks_min"] / statistics.mean(s["throughput_tasks_min"] for s in n4) - 1
    add("Results", "R7 throughput gain over N4 (%)", "~5", f"~{100 * gain:.0f}")
    add("Results", "Offered load at Poisson 5 s (tasks/min)", "12", f"{full['R2']['arrival_rate'] * 60:.0f}")
    add("Results", "N4 ready-queue p95 range (s)", "4,769–6,161",
        "–".join(fmt_int(v) for v in (min(s["ready_queue_p95_s"] for s in n4), max(s["ready_queue_p95_s"] for s in n4))))
    add("Calibration", "Backfill opportunities in R2", "23", str(full["R2"]["backfill_opportunities"]))
    add("Calibration", "Backfill opportunities at N=16 (R3, R5)", "0",
        str(full["R3"]["backfill_opportunities"] + full["R5"]["backfill_opportunities"]))
    delays = M.trace_tool_delays()
    add("Calibration", "Tool return per call, median / p95 (s), 50-task trace", "0.12 / 3.7",
        f"{np.median(delays):.2f} / {np.percentile(delays, 95):.1f}")
    erase = [M.kv_validation(M.RESULTS / p)["after_erase"]["used_cells"] for p in M.KV_VALIDATION_T4]
    add("Calibration", "KV cells after slot erase (T4 validation, both hosts)", "0", str(max(erase)))
    return checks


def load_figure_module():
    spec = importlib.util.spec_from_file_location("fig_tradeoff", M.ROOT / "report/fig_tradeoff.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", type=Path, default=OUT)
    a = p.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    pd.set_option("display.width", 200)

    t1 = table1()
    t1.to_csv(a.out / "table1_s3_grid.csv", index=False)
    pivot = t1.assign(cell=t1.completed.astype(str) + " (" + t1.tasks_per_min.map("{:.2f}".format) + ")") \
              .pivot(index="arrival", columns="N", values="cell").reindex(["Burst", "Poisson 5 s", "Poisson 10 s"])
    print("Table 1 — S3: tasks completed out of 50 (tasks/min), 32K pool\n")
    print(pivot.to_string(), "\n")

    t3 = table3()
    t3.to_csv(a.out / "table3_full_runs.csv", index=False)
    print("Table 3 — full runs, 500 tasks\n")
    print(t3.drop(columns="path").to_string(index=False), "\n")

    checks = writeup_numbers()
    rows = [{"section": s, "claim": c, "writeup": w, "data": d, "match": w == d} for s, c, w, d in checks]
    pd.DataFrame(rows).to_csv(a.out / "writeup_numbers.csv", index=False)
    print("Inline numbers — writeup vs. data\n")
    for r in rows:
        flag = "ok " if r["match"] else "DIFF"
        print(f"  [{flag}] {r['section']:<11} {r['claim']:<58} writeup {r['writeup']:<20} data {r['data']}")

    load_figure_module().plot(a.out / "fig_tradeoff.pdf")
    print(f"\nWrote tables, numbers and fig_tradeoff.pdf to {a.out.relative_to(M.ROOT) if a.out.is_relative_to(M.ROOT) else a.out}")
    mismatches = [r for r in rows if not r["match"]]
    print(f"{len(rows) - len(mismatches)}/{len(rows)} inline numbers match the writeup.")
    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main())
