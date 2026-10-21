"""Build the synthetic trace from workloads/synthetic.yaml."""

import argparse
from pathlib import Path

import numpy as np
import yaml

from harness.client import random_tokens
from workloads.trace import Step, Task, describe, lognormal, save

HERE = Path(__file__).resolve().parent


def build(spec: dict) -> list:
    rng = np.random.default_rng(spec["seed"])
    classes = spec["classes"]
    names = list(classes)
    weights = np.array([classes[c]["weight"] for c in names], dtype=float)
    # one shared system prompt per class, so tasks of a class have a reusable prefix
    prefixes = {c: random_tokens(np.random.default_rng(spec["seed"] + i), classes[c]["prefix"]) for i, c in enumerate(names)}

    def uni(r):
        return int(rng.integers(r[0], r[1] + 1))

    tasks = []
    for i in range(spec["n_tasks"]):
        c = names[rng.choice(len(names), p=weights / weights.sum())]
        k = classes[c]
        n_steps = uni(k["steps"])
        steps = []
        for j in range(n_steps):
            inp = prefixes[c] + random_tokens(rng, uni(k["context"])) if j == 0 else random_tokens(rng, uni(k["tool_tokens"]))
            delay = lognormal(rng, k["tool_delay"]["median"], k["tool_delay"]["sigma"]) if j < n_steps - 1 else None
            steps.append(Step(input=inp, n_gen=uni(k["gen"]), tool_delay_s=delay))
        tasks.append(Task(task_id=f"{c}-{i:04d}", cls=c, source="synthetic", steps=steps))
    return tasks


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--spec", type=Path, default=HERE / "synthetic.yaml")
    p.add_argument("--out", type=Path, default=HERE.parent / "data/traces/synthetic.jsonl")
    a = p.parse_args()
    tasks = build(yaml.safe_load(a.spec.read_text()))
    save(tasks, a.out)
    print(f"wrote {a.out} ({len(tasks)} tasks)\n{describe(tasks)}")


if __name__ == "__main__":
    main()
