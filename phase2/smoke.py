"""Run matched Phase 1 FIFO and Phase 2 smoke configurations."""

import argparse
import json
from pathlib import Path

from harness.server import ServerConfig
from phase1.runner import RunConfig, execute as execute_phase1
from phase2.estimator import RuntimeEstimator
from phase2.runner import Phase2Config, execute as execute_phase2


def run_config(name: str, trace: str, n_tasks: int, concurrency: int,
               ctx: int, seed: int) -> RunConfig:
    return RunConfig(
        name=name,
        trace=trace,
        n_tasks=n_tasks,
        arrival_rate=0.0,
        concurrency=concurrency,
        delay_scale=1.0,
        cache_prompt=True,
        deadline_s=14400,
        request_timeout_s=900,
        scheduler="request",
        require_kv_metrics=True,
        seed=seed,
        server=ServerConfig(ctx=ctx, cache_ram=0, mmap=False),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", choices=("phase1", "phase2", "phase2-slot",
                                             "phase2-adaptive", "both"),
                        default="both")
    parser.add_argument("--trace", default="data/traces/agenttrace_50_seed2.jsonl")
    parser.add_argument("--runtime-model", type=Path,
                        default=Path("phase2/runtime_model_agenttrace_dev.json"))
    parser.add_argument("--output-group", default="phase2_result")
    parser.add_argument("--n-tasks", type=int, default=50)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--ctx", type=int, default=32768)
    parser.add_argument("--seed", type=int, default=2)
    args = parser.parse_args()

    summaries = {}
    if args.policy in {"phase1", "both"}:
        phase1 = run_config("smoke_phase1_fifo_n4_burst_50", args.trace, args.n_tasks,
                            args.concurrency, args.ctx, args.seed)
        summaries["phase1_fifo"] = execute_phase1(phase1, group=args.output_group)

    if args.policy in {"phase2", "both"}:
        phase2_run = run_config("smoke_phase2_two_queue_n4_burst_50", args.trace, args.n_tasks,
                                args.concurrency, args.ctx, args.seed)
        config = Phase2Config(
            run=phase2_run,
            aging_threshold_s=30,
            backfill_guard_s=8,
            kv_safety_margin_cells=512,
            reserve_per_waiting_task=256,
            max_tool_reserve_cells=4096,
            reserve_horizon_s=5,
            estimated_tool_delay_s=0.2,
            timing_mode="estimated",
        )
        summaries["phase2_two_queue"] = execute_phase2(
            config, RuntimeEstimator.load(args.runtime_model), group=args.output_group)

    if args.policy == "phase2-slot":
        phase2_run = run_config("smoke_phase2_slot_aware_n4_burst_50", args.trace,
                                args.n_tasks, args.concurrency, args.ctx, args.seed)
        config = Phase2Config(
            run=phase2_run,
            aging_threshold_s=30,
            backfill_guard_s=8,
            kv_safety_margin_cells=512,
            reserve_per_waiting_task=256,
            max_tool_reserve_cells=4096,
            reserve_horizon_s=5,
            estimated_tool_delay_s=0.2,
            timing_mode="estimated",
            eviction_policy="slot_aware",
        )
        summaries["phase2_slot_aware"] = execute_phase2(
            config, RuntimeEstimator.load(args.runtime_model), group=args.output_group)

    if args.policy == "phase2-adaptive":
        phase2_run = run_config("smoke_phase2_adaptive_n4_burst_50", args.trace,
                                args.n_tasks, args.concurrency, args.ctx, args.seed)
        config = Phase2Config(
            run=phase2_run,
            aging_threshold_s=30,
            backfill_guard_s=8,
            kv_safety_margin_cells=512,
            reserve_per_waiting_task=256,
            max_tool_reserve_cells=4096,
            reserve_horizon_s=5,
            estimated_tool_delay_s=0.2,
            timing_mode="estimated",
            eviction_policy="slot_aware",
            cache_grace_s=0.5,
        )
        summaries["phase2_adaptive"] = execute_phase2(
            config, RuntimeEstimator.load(args.runtime_model), group=args.output_group)

    print(json.dumps(summaries, indent=2, default=str))


if __name__ == "__main__":
    main()

