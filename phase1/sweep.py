"""Phase 1 experiment grid. Every group varies one factor against the same baseline:

    baseline: FIFO, model and precision fixed, KV budget -c 16384 (512 MiB), server defaults otherwise
              (host prompt cache on), weights loaded into RAM so the footprint counts everything

    concurrency_*  N = 1..32 admitted tasks                -> throughput vs latency, where completed work peaks
    budget_syn     KV budget 8k / 32k tokens at N = 16      -> memory pressure: failures vs budget
    idle_syn       tool delays x0 / x4 at N = 8             -> cost of idle periods between agent steps
    retention_*    host prompt cache off, prefix reuse off  -> do retained contexts / reusable prefixes matter
    scheduler       task FIFO vs request FIFO at N = 8       -> does releasing workers during tools help

Runs already in results/phase1 are skipped, so an interrupted sweep resumes.
"""

import argparse
import copy
import json

import pandas as pd

from harness.results import RESULTS, check_power
from harness.server import ServerConfig
from phase1.runner import RunConfig, execute

SYN = dict(trace="data/traces/synthetic.jsonl", n_tasks=100, arrival_rate=0.4)
AI = dict(trace="data/traces/agentinstruct.jsonl", n_tasks=150, arrival_rate=0.8)
CONCURRENCY = [1, 2, 4, 8, 16, 32]


def base(name: str, wl: dict, **kw) -> RunConfig:
    server = ServerConfig(ctx=kw.pop("ctx", 16384), cache_ram=kw.pop("cache_ram", None), mmap=False)
    return RunConfig(name=name, server=server, **{**wl, **kw})


def grid() -> dict:
    g = {
        "concurrency_syn": [base(f"syn_N{n}", SYN, concurrency=n) for n in CONCURRENCY],
        "budget_syn": [base(f"syn_N16_ctx{c}", SYN, concurrency=16, ctx=c) for c in (8192, 32768)],
        "idle_syn": [base(f"syn_N8_delay{d:g}", SYN, concurrency=8, delay_scale=d) for d in (0.0, 4.0)],
        "retention_syn": [base("syn_N8_noram", SYN, concurrency=8, cache_ram=0),
                          base("syn_N8_noreuse", SYN, concurrency=8, cache_prompt=False)],
        "concurrency_ai": [base(f"ai_N{n}", AI, concurrency=n) for n in CONCURRENCY],
        "retention_ai": [base("ai_N8_noram", AI, concurrency=8, cache_ram=0),
                         base("ai_N8_noreuse", AI, concurrency=8, cache_prompt=False)],
        "scheduler": [base("syn_N8_task", SYN, concurrency=8, scheduler="task"),
                      base("syn_N8_request", SYN, concurrency=8, scheduler="request"),
                      base("ai_N8_task", AI, concurrency=8, scheduler="task"),
                      base("ai_N8_request", AI, concurrency=8, scheduler="request")],
    }
    return g


def main():
    groups = grid()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--groups", nargs="*", default=list(groups), choices=list(groups))
    p.add_argument("--redo", action="store_true", help="rerun configs that already have results")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    if not a.dry_run:
        check_power()
    for gname in a.groups:
        for cfg in groups[gname]:
            done = (RESULTS / "phase1" / cfg.name / "meta.json").exists()
            print(f"{gname:>16} {cfg.name:<18} {'skip (done)' if done and not a.redo else 'run'}")
            if a.dry_run or (done and not a.redo):
                continue
            execute(copy.deepcopy(cfg))
    rows = []
    for meta in sorted((RESULTS / "phase1").glob("*/meta.json")):
        rows.append(json.loads(meta.read_text())["summary"])
    if rows:
        pd.DataFrame(rows).to_csv(RESULTS / "phase1/summary.csv", index=False)
        print(f"wrote {RESULTS / 'phase1/summary.csv'} ({len(rows)} runs)")


if __name__ == "__main__":
    main()
