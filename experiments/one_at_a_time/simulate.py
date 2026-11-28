"""Would serving requests one at a time (no batching) beat the concurrent schedulers on the 500-task set?

Replays the 500-task workload (same task order and Poisson arrival times, mean 5 s, seed 2, taken from the Phase 1
FIFO N4 run) on a simulated single-request server. Each request's service time is its measured end-to-end time in the
Phase 1 FIFO N1 run (R1, one request at a time on the same T4 / llama.cpp / model, so every request ran alone; R1 used
arrival rate 0.1, which does not change a request's own service time). Tool delays come from the trace.
Policies: fifo (by ready time, as Phase 1) and sjf_oracle (shortest measured service time first; uses true lengths,
so it is a bound, not a scheduler). Writes results/one_at_a_time/summary.csv.
Caveat: R1's service times include prefix-cache hits when a task's next step followed its previous step on the one
slot; interleaving tasks differently would reduce those hits, so the simulated times are optimistic for N1.
"""
import heapq
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
T = [json.loads(l) for l in (ROOT / "data/traces/agenttrace_full_500_seed2.jsonl").open()]
R1 = ROOT / "results/t4_full500_v1_part_a/t4_full500_v1_c1_n1_poisson10s_ctx32k_seed2"
R2 = ROOT / "results/t4_full500_v1_part_a/t4_full500_v1_c2_n4_poisson5s_ctx32k_seed2"


def simulate(policy, svc, arr, steps):
    ev = [(arr[tid], tid, 0) for tid in steps]
    heapq.heapify(ev)
    q, t, fin = [], 0.0, {}
    while ev or q:
        while ev and ev[0][0] <= t:
            q.append(heapq.heappop(ev))
        if not q:
            t = ev[0][0]
            continue
        key = (lambda e: e[0]) if policy == "fifo" else (lambda e: (svc[(e[1], e[2])], e[0]))
        e = min(q, key=key)
        q.remove(e)
        _, tid, k = e
        t += svc[(tid, k)]
        if k + 1 < len(steps[tid]):
            heapq.heappush(ev, (t + steps[tid][k]["tool_delay_s"], tid, k + 1))
        else:
            fin[tid] = t - arr[tid]
    return pd.Series(fin), t


def main():
    r = pd.read_csv(R1 / "requests.csv")
    svc = {(a, b): e for a, b, e in zip(r.task_id, r.step, r.e2e_s)}
    arr = pd.read_csv(R2 / "tasks.csv").set_index("task_id").arrival
    steps = {x["task_id"]: x["steps"] for x in T}
    rows = {}
    for p in ("fifo", "sjf_oracle"):
        c, end = simulate(p, svc, arr, steps)
        rows[f"N1 {p} (simulated)"] = {"completed": len(c), "tasks_per_min": 500 / end * 60, "latency_mean_s": c.mean(),
                                       "latency_p50_s": c.median(), "latency_p95_s": c.quantile(.95),
                                       "latency_p99_s": c.quantile(.99), "latency_max_s": c.max()}
    df = pd.DataFrame(rows).T
    print(f"total solo service time {sum(svc.values()):,.0f} s -> capacity {500 / sum(svc.values()) * 60:.2f} tasks/min")
    print(df.round(1).to_string())
    out = ROOT / "results/one_at_a_time"
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "summary.csv")


if __name__ == "__main__":
    main()
