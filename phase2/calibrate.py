"""Evaluate the frozen-input runtime estimator on AgentTrace development requests."""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from phase2.estimator import RuntimeEstimator


def _metrics(frame: pd.DataFrame) -> dict:
    error = frame.actual_runtime_s - frame.predicted_runtime_s
    absolute = error.abs()
    return {
        "requests": len(frame),
        "absolute_error_p50_s": float(np.percentile(absolute, 50)),
        "absolute_error_p95_s": float(np.percentile(absolute, 95)),
        "signed_error_p50_s": float(np.percentile(error, 50)),
        "underprediction_p95_s": float(np.percentile(error.clip(lower=0), 95)),
    }


def runtime_error_report(estimator: RuntimeEstimator, paths: list[Path],
                         development_ids: set[str]) -> dict:
    frames = []
    for path in paths:
        frame = pd.read_csv(path)
        frame = frame[frame.task_id.isin(development_ids) & frame.error.isna()].copy()
        frame["source_file"] = path.as_posix()
        frames.append(frame)
    rows = pd.concat(frames, ignore_index=True)
    rows = rows.dropna(subset=["n_prompt", "n_predict_req", "e2e_s"])
    cache = rows.cache_n.fillna(0) if "cache_n" in rows else 0
    rows["predicted_runtime_s"] = (
        estimator.intercept_s
        + estimator.prefill_s_per_token * (rows.n_prompt - cache).clip(lower=0)
        + estimator.decode_s_per_token * rows.n_predict_req
    )
    rows["actual_runtime_s"] = rows.e2e_s
    rows["context_bucket"] = pd.cut(
        rows.n_prompt, [-1, 1024, 2048, 4096, float("inf")],
        labels=["<=1K", "1K-2K", "2K-4K", ">4K"],
    )
    rows["output_bucket"] = pd.cut(
        rows.n_predict_req, [-1, 128, 256, 512, float("inf")],
        labels=["<=128", "129-256", "257-512", ">512"],
    )

    def grouped(column: str) -> dict:
        return {
            str(name): _metrics(group)
            for name, group in rows.groupby(column, observed=True)
            if len(group)
        }

    return {
        "overall": _metrics(rows),
        "by_context": grouped("context_bucket"),
        "by_output_budget": grouped("output_bucket"),
        "by_step": grouped("step"),
        "by_source_run": grouped("source_file"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("requests", type=Path, nargs="+")
    parser.add_argument("--estimator", type=Path, required=True)
    parser.add_argument("--split-manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.split_manifest.read_text())
    report = runtime_error_report(
        RuntimeEstimator.load(args.estimator), args.requests,
        set(manifest["development"]["task_ids"]),
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
