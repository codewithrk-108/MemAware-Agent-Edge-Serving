"""Per-run metrics and the map from writeup sections to result folders.

Everything here reads only committed files: each run's meta.json plus timeline.csv,
kv_memory.csv and admission_checks.csv. No GPU or server is needed.
"""

import json
import math
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"

# ---- Writeup section -> result folders -------------------------------------------------

# S1 feasibility: the ten early burst-arrival runs.
S1_RUNS = [
    "phase1/aws_g6f2_fifo_50_ctx16k_n16_seed2_retry",   # L4 6 GiB slice, 16K, N16
    "phase1/aws_g6f2_fifo_50_ctx16k_n8_seed2",          # L4 6 GiB slice, 16K, N8
    "phase1/aws_g6f2_fifo_50_ctx32k_n8_seed2",          # L4 6 GiB slice, 32K, N8
    "phase1/aws_g6f2_smoke_fifo_6_ctx16k_n4_seed2",     # L4 6 GiB slice, 6-task smoke
    "phase1/aws_g6f2_smoke_fifo_6_ctx16k_n8_seed2",     # L4 6 GiB slice, 6-task smoke
    "phase1/aws_smoke_fifo_6_ctx12k_n4_seed2",          # L4 3 GiB slice, 6-task smoke (no meta.json)
    "phase1/t4_smoke_8_burst_ctx32k_n4_seed2",          # T4
    "phase1/t4_smoke_sweep_v1/t4_smoke_8_burst_ctx32k_n8_seed2",
    "phase1/t4_smoke_sweep_v1/t4_smoke_8_burst_ctx64k_n8_seed2",
    "phase1/t4_smoke_sweep_v1/t4_smoke_20_burst_ctx64k_n16_seed2",
]
# S1/S2: matched Poisson-40 s sweeps on the 50-task trace, 11 configurations per GPU.
SWEEP_L4 = "phase1/agenttrace_sweep_v5"
SWEEP_T4 = "phase1/t4_agenttrace_sweep_v5"
SWEEP_RUN = "agenttrace_{}_poisson50_32k_v5"
# S2 concurrency comparison inside the T4 sweep (N=4 is the "baseline" request-FIFO run).
S2_CONCURRENCY = {1: "N1", 2: "N2", 4: "baseline", 8: "N8", 16: "N16_ctx32768"}
# S3: instrumented 12-run grid.
S3_GLOB = "t4_load_v1_part_*/t4_load_v1_*"
# Full runs R1-R7.
FULL_RUNS = {
    "R1": "t4_full500_v1_part_a/t4_full500_v1_c1_n1_poisson10s_ctx32k_seed2",
    "R2": "t4_full500_v1_part_a/t4_full500_v1_c2_n4_poisson5s_ctx32k_seed2",
    "R3": "t4_full500_v1_part_a/t4_full500_v1_c3_n16_poisson5s_ctx32k_seed2",
    "R4": "t4_full500_v1_part_b/t4_full500_v1_c4_n4_poisson2s_ctx32k_seed2",
    "R5": "t4_full500_v1_part_b/t4_full500_v1_c5_n16_poisson2s_ctx32k_seed2",
    "R6": "t4_full500_v1_part_b/t4_full500_v1_c6_n4_burst_ctx32k_seed2",
    "R7": "t4_full500_v1_part_b/t4_full500_v1_c7_n16_poisson2s_ctx64k_seed2",
}
# Timeline and calibration evidence.
LOCAL_SMOKE = "phase1/trial_fifo_6_ctx16k_n4_seed2"
KV_VALIDATION_LOCAL = "validation/kv_occupancy_local"
KV_VALIDATION_T4 = ["t4_load_v1_part_a/runtime_validation.log", "t4_load_v1_part_b/runtime_validation.log"]
SMOKE_TRACE = ROOT / "data/traces/agenttrace_50_seed2.jsonl"


def arrival_label(rate: float) -> str:
    if rate <= 0:
        return "Burst"
    gap = 1 / rate
    return f"Poisson {gap:.0f} s"


# ---- Metrics -----------------------------------------------------------------------------

def integrate(frame: pd.DataFrame, column: str, duration: float) -> float:
    """Time-weighted mean of a left-held sampled column over [0, duration]."""
    times = frame.t.to_numpy()
    values = frame[column].to_numpy()
    area = 0.0
    for i, (time, value) in enumerate(zip(times, values)):
        left = max(0.0, time)
        right = min(duration, times[i + 1]) if i + 1 < len(times) else duration
        area += max(0.0, right - left) * value
    # Timeline counters are zero before their first event; KV samples start before
    # workload time zero, so they cover the full clipped interval.
    return area / duration if duration > 0 else math.nan


def load_meta(run: str) -> dict:
    return json.loads((RESULTS / run / "meta.json").read_text())


def run_metrics(run: str) -> dict:
    """Summary fields from meta.json plus metrics derived from the run's CSVs."""
    d = RESULTS / run
    meta = load_meta(run)
    s = dict(meta["summary"])
    s["path"] = f"results/{run}"
    s["arrival_rate"] = meta["run"]["arrival_rate"]
    timeline = pd.read_csv(d / "timeline.csv")
    s["avg_running"] = integrate(timeline, "running", s["span_s"])
    s["max_running"] = int(timeline.running.max())
    if "runnable" in timeline:
        s["avg_ready_queue"] = integrate(timeline, "runnable", s["span_s"])
        s["max_ready_queue"] = int(timeline.runnable.max())
    if (d / "kv_memory.csv").exists():
        kv = pd.read_csv(d / "kv_memory.csv")
        s["mean_kv_pct"] = 100 * integrate(kv, "used_cells", s["span_s"]) / s["kv_capacity_cells"]
    if (d / "admission_checks.csv").exists():
        checks = pd.read_csv(d / "admission_checks.csv")
        fits = checks.head_fits.astype(str).str.lower() == "true"
        s["est_fit_then_error"] = int((fits & (checks.request_outcome == "error")).sum())
        s["est_exceed_then_ok"] = int((~fits & (checks.request_outcome == "ok")).sum())
    return s


def s3_runs() -> list:
    runs = [p.parent.relative_to(RESULTS).as_posix() for p in RESULTS.glob(f"{S3_GLOB}/meta.json")]
    return sorted(runs, key=lambda r: (load_meta(r)["run"]["arrival_rate"], load_meta(r)["summary"]["concurrency"]))


def sweep_runs(sweep: str) -> list:
    return sorted(p.parent.relative_to(RESULTS).as_posix() for p in (RESULTS / sweep).glob("*/meta.json"))


def trace_tool_delays(path: Path = SMOKE_TRACE) -> list:
    delays = []
    for line in path.read_text().splitlines():
        delays += [s["tool_delay_s"] for s in json.loads(line)["steps"] if s["tool_delay_s"] is not None]
    return delays


def kv_validation(log: Path) -> dict:
    """Parse the JSON block that runtime/validate_kv_metrics.py prints into runtime_validation.log."""
    text = log.read_text()
    start = text.index('{\n  "initial"')
    data, _ = json.JSONDecoder().raw_decode(text[start:])
    return data
