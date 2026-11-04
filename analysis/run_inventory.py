"""Build results/run_inventory.csv: one row per committed run and where the writeup uses it.

    uv run python -m analysis.run_inventory
"""

import re
from pathlib import Path

import pandas as pd

from analysis import metrics as M

OUT = M.RESULTS / "run_inventory.csv"
COLUMNS = ["path", "stage", "gpu", "n_tasks", "concurrency", "kv_ctx", "arrival", "completed", "failed",
           "tasks_per_min", "used_in_writeup", "note"]


def gpu_for(run: str, meta_total) -> str:
    if run.startswith("phase1/aws_smoke_fifo_6_ctx12k"):
        return "L4 slice (3 GiB)"
    if meta_total == 15360:
        return "T4 (15 GiB)"
    if meta_total == 6144 or run.startswith("phase1/aws_g6f2") or run.startswith(M.SWEEP_L4 + "/"):
        return "L4 slice (6 GiB)"
    return "Apple M4 Pro (Metal)"


def row_from_meta(run: str, stage: str, used: str) -> dict:
    s = M.run_metrics(run)
    return {"path": s["path"], "stage": stage, "gpu": gpu_for(run, s.get("gpu_total_mib")),
            "n_tasks": s["n_tasks"], "concurrency": s["concurrency"], "kv_ctx": s["ctx"],
            "arrival": M.arrival_label(s["arrival_rate"]), "completed": s["ok"], "failed": s["failed"],
            "tasks_per_min": round(s["throughput_tasks_min"], 3), "used_in_writeup": used, "note": ""}


def row_from_tasks(run: str, stage: str, used: str) -> dict:
    """For a run whose meta.json was never written: counts come from tasks.csv, config from the name."""
    tasks = pd.read_csv(M.RESULTS / run / "tasks.csv")
    n = int(re.search(r"_n(\d+)_", run).group(1))
    ctx = int(re.search(r"_ctx(\d+)k_", run).group(1)) * 1024
    return {"path": f"results/{run}", "stage": stage, "gpu": gpu_for(run, None), "n_tasks": len(tasks),
            "concurrency": n, "kv_ctx": ctx, "arrival": "Burst", "completed": int((tasks.status == "ok").sum()),
            "failed": int((tasks.status != "ok").sum()), "tasks_per_min": None, "used_in_writeup": used,
            "note": "no meta.json (run stopped before writing it); counts from tasks.csv"}


def build() -> pd.DataFrame:
    rows = []
    for run in M.S1_RUNS:
        used = "S1 feasibility (10 burst runs)"
        if run.endswith("t4_smoke_20_burst_ctx64k_n16_seed2"):
            used += "; gaps (64K tried with 20 tasks)"
        has_meta = (M.RESULTS / run / "meta.json").exists()
        rows.append((row_from_meta if has_meta else row_from_tasks)(run, "S1", used))
    for run in M.sweep_runs(M.SWEEP_L4):
        rows.append(row_from_meta(run, "S1 sweep", "S1 matched Poisson-40 s sweep (L4: 468/550)"))
    for run in M.sweep_runs(M.SWEEP_T4):
        rows.append(row_from_meta(run, "S1/S2 sweep", "S1 matched sweep (T4: 550/550); S2 light load"))
    for run in M.s3_runs():
        rows.append(row_from_meta(run, "S3", "S3 grid, Table 1; calibration (TPOT, estimator)"))
    for name, run in M.FULL_RUNS.items():
        rows.append(row_from_meta(run, f"Full {name}", "Table 3, Figure 1; results; calibration"))
    rows.append(row_from_meta(M.LOCAL_SMOKE, "Local smoke", "Appendix A timeline (local smoke test)"))
    rows.append(row_from_meta(M.KV_VALIDATION_LOCAL, "Validation",
                              "KV occupancy exporter check (local); T4 checks are in t4_load_v1_part_*/runtime_validation.log"))
    return pd.DataFrame(rows, columns=COLUMNS)


def main():
    table = build()
    table.to_csv(OUT, index=False)
    print(f"wrote {OUT.relative_to(M.ROOT)} ({len(table)} runs)")


if __name__ == "__main__":
    main()
