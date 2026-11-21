"""Interpretable request-runtime estimation fitted from Phase 1 request records."""

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class RuntimeEstimator:
    intercept_s: float
    prefill_s_per_token: float
    decode_s_per_token: float
    fallback_s: float
    source_files: tuple[str, ...] = ()
    source_sha256: str = ""
    error_p50_s: float = 0.0
    error_p95_s: float = 0.0

    def predict(self, prompt_tokens: int, output_tokens: int,
                cached_prompt_tokens: int = 0) -> tuple[float, bool]:
        values = (prompt_tokens, output_tokens, cached_prompt_tokens)
        if any(not isinstance(value, (int, np.integer)) or value < 0 for value in values):
            return self.fallback_s, True
        uncached = max(0, prompt_tokens - cached_prompt_tokens)
        estimate = (self.intercept_s + self.prefill_s_per_token * uncached
                    + self.decode_s_per_token * output_tokens)
        if not np.isfinite(estimate) or estimate <= 0:
            return self.fallback_s, True
        return float(max(estimate, 1e-3)), False

    def save(self, path: Path) -> None:
        data = asdict(self)
        data["source_files"] = list(self.source_files)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n")

    @classmethod
    def load(cls, path: Path) -> "RuntimeEstimator":
        data = json.loads(path.read_text())
        data["source_files"] = tuple(data.get("source_files", ()))
        return cls(**data)


def _source_digest(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def fit_runtime_estimator(frame: pd.DataFrame, source_files: tuple[str, ...] = (),
                          source_sha256: str = "") -> RuntimeEstimator:
    required = {"n_prompt", "n_predict_req", "e2e_s"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"missing estimator columns: {sorted(missing)}")

    rows = frame.copy()
    if "error" in rows:
        rows = rows[rows.error.isna()]
    cache = rows.cache_n.fillna(0) if "cache_n" in rows else 0
    rows = rows.assign(uncached_prompt=(rows.n_prompt - cache).clip(lower=0))
    rows = rows.replace([np.inf, -np.inf], np.nan).dropna(
        subset=["uncached_prompt", "n_predict_req", "e2e_s"])
    rows = rows[(rows.uncached_prompt >= 0) & (rows.n_predict_req >= 0) & (rows.e2e_s > 0)]
    if len(rows) < 3:
        raise ValueError("at least three successful request records are required")

    design = np.column_stack([
        np.ones(len(rows)),
        rows.uncached_prompt.to_numpy(dtype=float),
        rows.n_predict_req.to_numpy(dtype=float),
    ])
    target = rows.e2e_s.to_numpy(dtype=float)
    coefficients, *_ = np.linalg.lstsq(design, target, rcond=None)
    coefficients = np.maximum(coefficients, 0.0)
    predicted = design @ coefficients
    errors = np.abs(target - predicted)
    fallback = float(np.percentile(target, 95))
    return RuntimeEstimator(
        intercept_s=float(coefficients[0]),
        prefill_s_per_token=float(coefficients[1]),
        decode_s_per_token=float(coefficients[2]),
        fallback_s=fallback,
        source_files=source_files,
        source_sha256=source_sha256,
        error_p50_s=float(np.percentile(errors, 50)),
        error_p95_s=float(np.percentile(errors, 95)),
    )


def fit_from_files(paths: list[Path], task_ids: Optional[set[str]] = None,
                   extra_sources: tuple[Path, ...] = ()) -> RuntimeEstimator:
    if not paths:
        raise ValueError("at least one requests.csv path is required")
    frames = [pd.read_csv(path) for path in paths]
    if task_ids is not None:
        frames = [frame[frame.task_id.isin(task_ids)] for frame in frames]
    sources = [*paths, *extra_sources]
    return fit_runtime_estimator(
        pd.concat(frames, ignore_index=True),
        source_files=tuple(path.as_posix() for path in paths),
        source_sha256=_source_digest(sources),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("requests", type=Path, nargs="+")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--split-manifest", type=Path)
    args = parser.parse_args()
    task_ids = None
    extra_sources = ()
    if args.split_manifest:
        manifest = json.loads(args.split_manifest.read_text())
        task_ids = set(manifest["development"]["task_ids"])
        extra_sources = (args.split_manifest,)
    estimator = fit_from_files(args.requests, task_ids, extra_sources)
    estimator.save(args.out)
    print(json.dumps(asdict(estimator), indent=2))


if __name__ == "__main__":
    main()

