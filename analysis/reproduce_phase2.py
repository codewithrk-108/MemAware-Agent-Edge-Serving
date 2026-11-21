"""Validate and summarize committed Phase 2 run outputs."""

import argparse
import json
from pathlib import Path

import pandas as pd

from phase2.freeze import verify_frozen_policy

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"


def validate_run(path: Path) -> dict:
    required = ["meta.json", "tasks.csv", "requests.csv", "scheduler_decisions.csv"]
    missing = [name for name in required if not (path / name).exists()]
    if missing:
        raise ValueError(f"{path}: missing files {missing}")
    meta = json.loads((path / "meta.json").read_text())
    tasks = pd.read_csv(path / "tasks.csv")
    requests = pd.read_csv(path / "requests.csv")
    decisions = pd.read_csv(path / "scheduler_decisions.csv")
    requested = requests.n_predict_req.fillna(0)
    generated = requests.predicted_n.fillna(0)
    successful = requests.error.isna()
    short_successes = int((successful & (generated != requested)).sum())
    terminal = {"ok", "failed", "timeout_queued", "timeout_running", "infeasible"}
    nonterminal = sorted(set(tasks.status) - terminal)
    if nonterminal:
        raise ValueError(f"{path}: nonterminal task states {nonterminal}")
    if short_successes:
        raise ValueError(f"{path}: {short_successes} successful requests violated output budgets")
    selected = decisions.selected_task_index.notna() if "selected_task_index" in decisions else pd.Series(dtype=bool)
    return {
        "name": meta["summary"]["name"],
        "tasks": len(tasks),
        "ok": int((tasks.status == "ok").sum()),
        "failed": int((tasks.status == "failed").sum()),
        "timeouts": int(tasks.status.isin(["timeout_queued", "timeout_running"]).sum()),
        "infeasible": int((tasks.status == "infeasible").sum()),
        "throughput_tasks_min": meta["summary"]["throughput_tasks_min"],
        "completion_p95_s": meta["summary"]["completion_p95_s"],
        "max_ready_queue_s": meta["summary"]["max_ready_queue_s"],
        "backfills": int((decisions.reason == "backfill").sum()),
        "admissions": int(selected.sum()) if len(selected) else 0,
        "generated_tokens": int(generated.sum()),
    }


def compare_manifests(paths: list[Path]) -> None:
    signatures = []
    for path in paths:
        meta = json.loads((path / "meta.json").read_text())
        run = meta["phase2"]["run"] if "phase2" in meta else meta["run"]
        signatures.append((run["trace"], run["n_tasks"], run["arrival_rate"], run["seed"]))
    if len(set(signatures)) != 1:
        raise ValueError(f"runs are not workload-comparable: {signatures}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", type=Path, nargs="*")
    parser.add_argument("--frozen-policy", type=Path)
    parser.add_argument("--compare", action="store_true")
    args = parser.parse_args()
    if args.frozen_policy:
        verify_frozen_policy(args.frozen_policy)
    if args.compare and args.runs:
        compare_manifests(args.runs)
    rows = [validate_run(path) for path in args.runs]
    if rows:
        print(pd.DataFrame(rows).to_string(index=False))
    else:
        print("No Phase 2 runs supplied; frozen inputs validated." if args.frozen_policy
              else "No Phase 2 runs supplied.")


if __name__ == "__main__":
    main()
