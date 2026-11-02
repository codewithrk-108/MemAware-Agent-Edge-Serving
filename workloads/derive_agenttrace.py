"""Derive the Phase 1 AgentTrace workloads from the converted trace.

Two deterministic steps follow `workloads.build_agenttrace --split all --max-context 16384`:

    scale   tool_delay_s -> min(tool_delay_s * 150, 10 s); null delays stay null
            data/traces/agenttrace_all.jsonl -> data/traces/agenttrace_all_tool_delay_150x_max10s.jsonl
    sample  numpy.random.default_rng(2).choice(n, 50, replace=False), in selection order
            ...150x_max10s.jsonl -> data/traces/agenttrace_50_seed2.jsonl  (smoke tests S1-S3)

The 500-task file used by the full runs is built by `workloads.build_full_agenttrace`.
Each output gets an adjacent `.manifest.json` with input/output SHA-256 hashes.
"""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
TRACES = ROOT / "data/traces"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def read_rows(path: Path) -> list:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_rows(path: Path, rows: list) -> None:
    path.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows))


def scale_delays(rows: list, scale: float, cap_s: float) -> tuple[list, dict]:
    calls = capped = requests = 0
    out = []
    for row in rows:
        steps = []
        for step in row["steps"]:
            requests += 1
            delay = step["tool_delay_s"]
            if delay is not None:
                calls += 1
                scaled = delay * scale
                capped += scaled > cap_s
                delay = min(scaled, cap_s)
            steps.append({**step, "tool_delay_s": delay})
        out.append({**row, "steps": steps})
    counts = {"tasks": len(out), "model_requests": requests, "tool_calls": calls, "tool_calls_capped": capped}
    return out, counts


def sample(rows: list, n: int, seed: int) -> list:
    idx = np.random.default_rng(seed).choice(len(rows), size=n, replace=False)
    return [rows[i] for i in idx]


def cmd_scale(a):
    rows, counts = scale_delays(read_rows(a.source), a.scale, a.cap)
    write_rows(a.out, rows)
    manifest = {
        "source_trace": rel(a.source), "source_trace_sha256": sha256(a.source),
        "output_trace": rel(a.out), "output_trace_sha256": sha256(a.out),
        "tool_delay_transform": {
            "field": "tool_delay_s", "scale": a.scale, "cap_s": a.cap,
            "formula": "min(source_tool_delay_s * scale, cap_s)", "nulls_unchanged": True,
            "interpretation": "controlled experimental delays; not measured execution times at this scale"},
        "counts": counts,
    }
    Path(f"{a.out}.manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


def cmd_sample(a):
    rows = read_rows(a.source)
    chosen = sample(rows, a.n, a.seed)
    write_rows(a.out, chosen)
    manifest = {
        "source": rel(a.source), "source_sha256": sha256(a.source), "dataset_sha256": sha256(a.out),
        "selection": f"numpy.random.default_rng({a.seed}).choice, without replacement, preserving selection order",
        "numpy_version": np.__version__, "seed": a.seed,
        "source_task_count": len(rows), "selected_task_count": len(chosen),
        "task_ids": [r["task_id"] for r in chosen],
    }
    Path(f"{a.out}.manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"wrote {rel(a.out)}: {len(chosen)} of {len(rows)} tasks, sha256 {manifest['dataset_sha256']}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scale", help="scale and cap tool delays")
    s.add_argument("--source", type=Path, default=TRACES / "agenttrace_all.jsonl")
    s.add_argument("--out", type=Path, default=TRACES / "agenttrace_all_tool_delay_150x_max10s.jsonl")
    s.add_argument("--scale", type=float, default=150)
    s.add_argument("--cap", type=float, default=10.0)
    s.set_defaults(func=cmd_scale)
    m = sub.add_parser("sample", help="seeded random task sample")
    m.add_argument("--source", type=Path, default=TRACES / "agenttrace_all_tool_delay_150x_max10s.jsonl")
    m.add_argument("--out", type=Path, default=TRACES / "agenttrace_50_seed2.jsonl")
    m.add_argument("--n", type=int, default=50)
    m.add_argument("--seed", type=int, default=2)
    m.set_defaults(func=cmd_sample)
    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
