"""Phase 1 experiment grid. Every group varies one factor against the same baseline:

    baseline: FIFO, model and precision fixed, KV budget -c 16384, server defaults otherwise
              (host prompt cache on), weights loaded into RAM so the footprint counts everything

    concurrency_*  N = 1..32 admitted tasks                -> throughput vs latency, where completed work peaks
    budget_syn     KV budget 8k / 32k tokens at N = 16      -> memory pressure: failures vs budget
    idle_syn       tool delays x0 / x4 at N = 8             -> cost of idle periods between agent steps
    retention_*    host prompt cache off, prefix reuse off  -> do retained contexts / reusable prefixes matter
    scheduler       task FIFO vs request FIFO at N = 8       -> does releasing workers during tools help
    agenttrace_*    reliable and stress grids on AgentTrace  -> same questions on recorded agent trajectories

Runs already in results/phase1 are skipped, so an interrupted sweep resumes.
"""

import argparse
import copy
import json
from dataclasses import asdict

import pandas as pd

from harness.results import RESULTS, check_power
from harness.server import ServerConfig
from phase1.runner import RunConfig, execute

SYN = dict(trace="data/traces/synthetic.jsonl", n_tasks=100, arrival_rate=0.4)
AI = dict(trace="data/traces/agentinstruct.jsonl", n_tasks=150, arrival_rate=0.8)
AGENTTRACE = dict(trace="data/traces/agenttrace_50_seed2.jsonl", n_tasks=50,
                  arrival_rate=0.025, deadline_s=14400, request_timeout_s=900, seed=2)
AGENTTRACE_OPEN = AGENTTRACE
CONCURRENCY = [1, 2, 4, 8, 16, 32]


def base(name: str, wl: dict, **kw) -> RunConfig:
    default_cache_ram = 0 if wl.get("trace") == AGENTTRACE["trace"] else None
    server = ServerConfig(ctx=kw.pop("ctx", 16384), cache_ram=kw.pop("cache_ram", default_cache_ram), mmap=False)
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
        # AgentTrace reliability points: generous KV capacity and modest load target
        # >=90% task success before the separate capacity stress grid.
        "baseline_agenttrace": [
            base("agenttrace_baseline", AGENTTRACE, concurrency=4, ctx=32768,
                 arrival_rate=0.025),
        ],
        "concurrency_agenttrace": [
            base(f"agenttrace_N{n}", AGENTTRACE, concurrency=n, ctx=32768)
            for n in (1, 2, 4, 8)
        ],
        "idle_agenttrace": [
            base(f"agenttrace_N4_delay{d:g}", AGENTTRACE, concurrency=4, ctx=32768,
                 arrival_rate=0.025, delay_scale=d)
            for d in (0.0, 1.0)
        ],
        "retention_agenttrace": [
            base("agenttrace_N4_default", AGENTTRACE, concurrency=4, ctx=32768,
                 arrival_rate=0.025, cache_ram=8192),
            base("agenttrace_N4_noram", AGENTTRACE, concurrency=4, ctx=32768,
                 arrival_rate=0.025, cache_ram=0),
            base("agenttrace_N4_noreuse", AGENTTRACE, concurrency=4, ctx=32768,
                 arrival_rate=0.025, cache_ram=0, cache_prompt=False),
        ],
        "scheduler_agenttrace": [
            base(f"agenttrace_N4_{scheduler}", AGENTTRACE, concurrency=4, ctx=32768,
                 arrival_rate=0.025, scheduler=scheduler)
            for scheduler in ("task", "request")
        ],
        "arrival_agenttrace": [
            base(f"agenttrace_N8_rate{rate:g}", AGENTTRACE_OPEN, concurrency=8,
                 ctx=32768, arrival_rate=rate)
            for rate in (1 / 38, 1 / 40, 1 / 42)
        ],
        # Deliberately approach/exceed the KV budget to characterize the failure boundary.
        "budget_agenttrace": [
            base(f"agenttrace_N{n}_ctx{ctx}", AGENTTRACE, concurrency=n, ctx=ctx,
                 cache_ram=0)
            for ctx in (32768,)
            for n in (4, 8, 16)
        ],
        # Short T4 checks: all tasks arrive together and use the fixed 50-task trace.
        "smoke_t4": [
            base("t4_smoke_8_burst_ctx32k_n8_seed2", AGENTTRACE,
                 n_tasks=8, arrival_rate=0.0, concurrency=8, ctx=32768),
            base("t4_smoke_8_burst_ctx64k_n8_seed2", AGENTTRACE,
                 n_tasks=8, arrival_rate=0.0, concurrency=8, ctx=65536),
            base("t4_smoke_20_burst_ctx64k_n16_seed2", AGENTTRACE,
                 n_tasks=20, arrival_rate=0.0, concurrency=16, ctx=65536),
        ],
    }
    # New names prevent prior, differently configured results from being silently reused.
    for name, configs in g.items():
        if name.endswith("_agenttrace"):
            for cfg in configs:
                cfg.name += "_poisson50_32k_v5"
    # Balanced two-instance deployment of the same twelve load points.
    # Saved tool delays remain enabled in every run; only load/concurrency vary.
    for part, concurrencies in (("a", (2, 8)), ("b", (4, 16))):
        g[f"load_t4_part_{part}"] = [
            base(f"t4_load_v1_{pattern}_n{n}_ctx32k_seed2", AGENTTRACE,
                 concurrency=n, ctx=32768, arrival_rate=rate,
                 require_kv_metrics=True)
            for pattern, rate in (("poisson10s", 0.1), ("poisson5s", 0.2), ("burst", 0.0))
            for n in concurrencies
        ]
    for n in (8, 16):
        g[f"smoke64k_t4_n{n}"] = [
            base(f"t4_smoke_poisson5s_50_ctx64k_n{n}_seed2", AGENTTRACE,
                 concurrency=n, ctx=65536, arrival_rate=0.2,
                 require_kv_metrics=True)
        ]
    g["smoke64k_poisson2s_t4_n16"] = [
        base("t4_smoke_poisson2s_50_ctx64k_n16_seed2", AGENTTRACE,
             concurrency=16, ctx=65536, arrival_rate=0.5,
             require_kv_metrics=True)
    ]
    return g


def deduplicate_configs(configs):
    """Return unique runs and map every requested name to its equivalent run."""
    by_config = {}
    unique = []
    aliases = {}
    for cfg in configs:
        data = asdict(cfg)
        data.pop("name")
        key = json.dumps(data, sort_keys=True)
        if key not in by_config:
            by_config[key] = cfg.name
            unique.append(cfg)
        aliases[cfg.name] = by_config[key]
    return unique, aliases


def main():
    groups = grid()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--groups", nargs="*", default=["concurrency_agenttrace"], choices=list(groups))
    p.add_argument("--redo", action="store_true", help="rerun configs that already have results")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--deduplicate", action="store_true", help="reuse identical runs across selected groups")
    p.add_argument("--require-kv-metrics", action="store_true",
                   help="require physical KV occupancy metrics before starting each run")
    p.add_argument("--output-group", default="phase1", help="results subdirectory for this sweep")
    a = p.parse_args()
    if not a.dry_run:
        check_power()
    requested = [(gname, cfg) for gname in a.groups for cfg in groups[gname]]
    if a.require_kv_metrics:
        for _, cfg in requested:
            cfg.require_kv_metrics = True
    _, aliases = deduplicate_configs([cfg for _, cfg in requested]) if a.deduplicate else ([], {})
    if a.deduplicate and not a.dry_run:
        out = RESULTS / a.output_group
        out.mkdir(parents=True, exist_ok=True)
        (out / "sweep_aliases.json").write_text(json.dumps(aliases, indent=2) + "\n")
    for gname, cfg in requested:
        target = aliases.get(cfg.name, cfg.name)
        if target != cfg.name:
            print(f"{gname:>16} {cfg.name} reuse {target}", flush=True)
            continue
        done = (RESULTS / a.output_group / cfg.name / "meta.json").exists()
        print(f"{gname:>16} {cfg.name:<18} {'skip (done)' if done and not a.redo else 'run'}", flush=True)
        if a.dry_run or (done and not a.redo):
            continue
        execute(copy.deepcopy(cfg), group=a.output_group)
    if a.dry_run:
        return
    rows = []
    for meta in sorted((RESULTS / a.output_group).glob("*/meta.json")):
        rows.append(json.loads(meta.read_text())["summary"])
    if rows:
        pd.DataFrame(rows).to_csv(RESULTS / a.output_group / "summary.csv", index=False)
        print(f"wrote {RESULTS / a.output_group / 'summary.csv'} ({len(rows)} runs)")


if __name__ == "__main__":
    main()
