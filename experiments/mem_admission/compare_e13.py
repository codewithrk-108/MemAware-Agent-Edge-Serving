"""Experiment 13 comparison: task completion, latency percentiles, throughput and decode metrics of any runs.

Usage: python -m experiments.mem_admission.compare_e13 OUT.md LABEL=RUN_DIR [LABEL=RUN_DIR ...]
RUN_DIR is relative to the repository root. The first run is the reference (FIFO N4); "beats" counts the metrics on which
each run is better than it: completed, p50, p95, p99, mean, tasks/min.
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]


def metrics(d: Path) -> dict:
    t = pd.read_csv(d / "tasks.csv")
    ok = t[t.status == "ok"]
    q = pd.read_csv(d / "requests.csv")
    if "error" in q:
        q = q[q.error.isna()]
    kv = pd.read_csv(d / "kv_memory.csv")
    span = t.finish.max() - t.arrival.min()
    return {"completed": len(ok), "of": len(t), "p50 s": ok.completion_s.median(),
            "p95 s": ok.completion_s.quantile(.95), "p99 s": ok.completion_s.quantile(.99),
            "mean s": ok.completion_s.mean(), "max s": ok.completion_s.max(),
            "tasks/min": len(ok) / (span / 60), "run min": span / 60,
            "generated tok/s": q.predicted_n.sum() / span, "TPOT p50 ms": q.tpot_ms.median(),
            "prompt computed": int(q.prompt_n.sum()), "mean KV cells": kv.used_cells.mean(),
            "mean running": kv.requests_processing.mean()}


def beats(m: dict, ref: dict) -> int:
    return int(m["completed"] > ref["completed"]) + sum(int(m[k] < ref[k]) for k in ("p50 s", "p95 s", "p99 s", "mean s")) \
        + int(m["tasks/min"] > ref["tasks/min"])


def main():
    out = ROOT / sys.argv[1]
    runs = dict(a.rsplit("=", 1) for a in sys.argv[2:])   # labels may contain "=", paths do not
    rows = {k: metrics(ROOT / v) for k, v in runs.items() if (ROOT / v / "tasks.csv").exists()}
    ref = next(iter(rows.values()))
    for k, m in rows.items():
        m["beats ref (of 6)"] = beats(m, ref)
    df = pd.DataFrame(rows)
    fmt = df.T.copy()
    fmt["completed"] = [f"{int(m['completed'])}/{int(m['of'])}" for m in rows.values()]
    fmt = fmt.drop(columns="of")
    for c in fmt.columns:
        if c != "completed":
            fmt[c] = [f"{v:,.2f}" if isinstance(v, float) and abs(v) < 10 else f"{v:,.1f}" if isinstance(v, float) and abs(v) < 1000 else f"{v:,.0f}" for v in fmt[c]]
    text = f"Reference (first column): {next(iter(rows))}\n\n" + fmt.T.to_markdown() + "\n"
    out.write_text(text)
    print(text)


if __name__ == "__main__":
    main()
