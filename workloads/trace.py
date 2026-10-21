"""Trace format shared by the synthetic and dataset workloads.

A trace is a JSONL file, one agent task per line:

    {"task_id": "react-0007", "cls": "react_agent", "source": "synthetic",
     "steps": [{"input": [token ids appended to the history before this step],
                "n_gen": tokens the model generates in this step,
                "tool_delay_s": seconds the tool takes after this step, null for the last step}, ...]}

The runner replays a task step by step: history += input, generate n_gen tokens (the model's own tokens are
appended to the history, as a real agent would), then wait tool_delay_s before the next step. Arrival times are
not part of the trace, the runner draws them, so the same tasks can be replayed at different load.
"""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np


@dataclass
class Step:
    input: list
    n_gen: int
    tool_delay_s: Optional[float]


@dataclass
class Task:
    task_id: str
    cls: str
    source: str
    steps: list = field(default_factory=list)

    @property
    def n_steps(self) -> int:
        return len(self.steps)

    @property
    def total_tokens(self) -> int:
        """Context length at the end of the task, the KV memory one task needs at its peak."""
        return sum(len(s.input) + s.n_gen for s in self.steps)

    @property
    def total_tool_s(self) -> float:
        return sum(s.tool_delay_s or 0.0 for s in self.steps)


def save(tasks: list, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for t in tasks:
            f.write(json.dumps(asdict(t)) + "\n")


def load(path: Path) -> list:
    tasks = []
    with open(path) as f:
        for line in f:
            d = json.loads(line)
            d["steps"] = [Step(**s) for s in d["steps"]]
            tasks.append(Task(**d))
    return tasks


def lognormal(rng: np.random.Generator, median: float, sigma: float) -> float:
    return float(median * np.exp(sigma * rng.standard_normal()))


def describe(tasks: list) -> str:
    rows = {}
    for t in tasks:
        rows.setdefault(t.cls, []).append(t)
    lines = [f"{'class':>14} {'tasks':>6} {'steps p50':>9} {'ctx p50':>8} {'ctx max':>8} {'gen/step p50':>12} {'tool s/task p50':>15}"]
    for cls, ts in sorted(rows.items()):
        gens = [s.n_gen for t in ts for s in t.steps]
        lines.append(f"{cls:>14} {len(ts):6d} {np.median([t.n_steps for t in ts]):9.0f} "
                     f"{np.median([t.total_tokens for t in ts]):8.0f} {max(t.total_tokens for t in ts):8d} "
                     f"{np.median(gens):12.0f} {np.median([t.total_tool_s for t in ts]):15.2f}")
    return "\n".join(lines)
