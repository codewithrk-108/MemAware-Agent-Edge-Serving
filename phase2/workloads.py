"""Create deterministic disjoint AgentTrace development and evaluation traces."""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from workloads.trace import load, save


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def split_agenttrace(source: Path, dev_out: Path, eval_out: Path, manifest_out: Path,
                     dev_size: int, seed: int = 2) -> dict:
    tasks = load(source)
    if not 0 < dev_size < len(tasks):
        raise ValueError("development size must leave at least one evaluation task")
    rng = np.random.default_rng(seed)
    order = rng.permutation(len(tasks))
    dev_indices = sorted(int(index) for index in order[:dev_size])
    eval_indices = sorted(int(index) for index in order[dev_size:])
    dev_tasks = [tasks[index] for index in dev_indices]
    eval_tasks = [tasks[index] for index in eval_indices]
    save(dev_tasks, dev_out)
    save(eval_tasks, eval_out)
    manifest = {
        "source": source.as_posix(),
        "source_sha256": sha256(source),
        "seed": seed,
        "development": {
            "path": dev_out.as_posix(),
            "tasks": len(dev_tasks),
            "sha256": sha256(dev_out),
            "task_ids": [task.task_id for task in dev_tasks],
        },
        "evaluation": {
            "path": eval_out.as_posix(),
            "tasks": len(eval_tasks),
            "sha256": sha256(eval_out),
            "task_ids": [task.task_id for task in eval_tasks],
        },
    }
    overlap = set(manifest["development"]["task_ids"]) & set(manifest["evaluation"]["task_ids"])
    if overlap:
        raise ValueError(f"task IDs overlap across split: {sorted(overlap)[:3]}")
    manifest_out.parent.mkdir(parents=True, exist_ok=True)
    manifest_out.write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path,
                        default=Path("data/traces/agenttrace_full_500_seed2.jsonl"))
    parser.add_argument("--dev-out", type=Path,
                        default=Path("data/traces/agenttrace_phase2_dev.jsonl"))
    parser.add_argument("--eval-out", type=Path,
                        default=Path("data/traces/agenttrace_phase2_eval.jsonl"))
    parser.add_argument("--manifest", type=Path,
                        default=Path("data/traces/agenttrace_phase2_split.manifest.json"))
    parser.add_argument("--dev-size", type=int, default=250)
    parser.add_argument("--seed", type=int, default=2)
    args = parser.parse_args()
    print(json.dumps(split_agenttrace(args.source, args.dev_out, args.eval_out,
                                      args.manifest, args.dev_size, args.seed), indent=2))


if __name__ == "__main__":
    main()

