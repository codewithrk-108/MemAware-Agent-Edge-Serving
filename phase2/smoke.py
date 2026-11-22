"""Run matched Phase 1 FIFO and Phase 2 smoke configurations."""

import argparse
import json
from pathlib import Path

from harness.server import ServerConfig
from phase1.runner import RunConfig, execute as execute_phase1
from phase2.estimator import RuntimeEstimator
from phase2.runner import Phase2Config, execute as execute_phase2


def run_config(name: str, trace: str, n_tasks: int, concurrency: int,
               ctx: int, seed: int, arrival_rate: float = 0.0) -> RunConfig:
    return RunConfig(
        name=name,
        trace=trace,
        n_tasks=n_tasks,
        arrival_rate=arrival_rate,
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


def quota_config(run: RunConfig, aging_threshold_s: float = 30) -> Phase2Config:
    return Phase2Config(
        run=run,
        aging_threshold_s=aging_threshold_s,
        backfill_guard_s=8,
        kv_safety_margin_cells=512,
        reserve_per_waiting_task=256,
        max_tool_reserve_cells=4096,
        reserve_horizon_s=5,
        estimated_tool_delay_s=0.2,
        timing_mode="estimated",
        eviction_policy="slot_aware",
        cache_grace_s=0.5,
        max_consecutive_resume_priorities=1,
        resume_priority_head_age_limit_s=0,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", choices=("phase1", "phase2", "phase2-slot",
                                             "phase2-adaptive", "phase2-fair-adaptive",
                                             "poisson5-adaptive", "poisson5-both",
                                             "quota-burst", "quota-poisson5", "quota-both",
                                             "quota-age360-poisson5",
                                             "cost-aware-poisson5",
                                             "cost-aware-r4",
                                             "cost-aware-r5-age30",
                                             "both"),
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

    if args.policy == "phase2-fair-adaptive":
        phase2_run = run_config("smoke_phase2_fair_adaptive_n4_burst_50", args.trace,
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
            max_consecutive_resume_priorities=1,
            resume_priority_head_age_limit_s=30,
        )
        summaries["phase2_fair_adaptive"] = execute_phase2(
            config, RuntimeEstimator.load(args.runtime_model), group=args.output_group)

    if args.policy == "poisson5-both":
        phase1 = run_config("smoke_phase1_fifo_n4_poisson5s_50", args.trace,
                            args.n_tasks, args.concurrency, args.ctx, args.seed,
                            arrival_rate=0.2)
        summaries["phase1_fifo_poisson5s"] = execute_phase1(
            phase1, group=args.output_group)

    if args.policy in {"poisson5-adaptive", "poisson5-both"}:
        phase2_run = run_config("smoke_phase2_adaptive_n4_poisson5s_50", args.trace,
                                args.n_tasks, args.concurrency, args.ctx, args.seed,
                                arrival_rate=0.2)
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
            max_consecutive_resume_priorities=0,
            resume_priority_head_age_limit_s=0,
        )
        summaries["phase2_adaptive_poisson5s"] = execute_phase2(
            config, RuntimeEstimator.load(args.runtime_model), group=args.output_group)

    if args.policy in {"quota-burst", "quota-both"}:
        run = run_config("smoke_phase2_quota1_n4_burst_50", args.trace,
                         args.n_tasks, args.concurrency, args.ctx, args.seed)
        summaries["phase2_quota1_burst"] = execute_phase2(
            quota_config(run), RuntimeEstimator.load(args.runtime_model),
            group=args.output_group)

    if args.policy in {"quota-poisson5", "quota-both"}:
        run = run_config("smoke_phase2_quota1_n4_poisson5s_50", args.trace,
                         args.n_tasks, args.concurrency, args.ctx, args.seed,
                         arrival_rate=0.2)
        summaries["phase2_quota1_poisson5s"] = execute_phase2(
            quota_config(run), RuntimeEstimator.load(args.runtime_model),
            group=args.output_group)

    if args.policy == "quota-age360-poisson5":
        run = run_config("smoke_phase2_quota1_age360_n4_poisson5s_50", args.trace,
                         args.n_tasks, args.concurrency, args.ctx, args.seed,
                         arrival_rate=0.2)
        summaries["phase2_quota1_age360_poisson5s"] = execute_phase2(
            quota_config(run, aging_threshold_s=360),
            RuntimeEstimator.load(args.runtime_model), group=args.output_group)

    if args.policy == "cost-aware-poisson5":
        run = run_config("smoke_phase2_cost_aware_age30_n4_poisson5s_50", args.trace,
                         args.n_tasks, args.concurrency, args.ctx, args.seed,
                         arrival_rate=0.2)
        config = quota_config(run)
        config.max_consecutive_resume_priorities = 0
        config.cost_aware_resume = True
        config.resume_queue_cost_weight = 0.25
        summaries["phase2_cost_aware_age30_poisson5s"] = execute_phase2(
            config, RuntimeEstimator.load(args.runtime_model), group=args.output_group)

    if args.policy == "cost-aware-r4":
        run = run_config("full_phase2_cost_aware_n4_poisson2s_500", args.trace,
                         args.n_tasks, args.concurrency, args.ctx, args.seed,
                         arrival_rate=0.5)
        config = quota_config(run)
        config.max_consecutive_resume_priorities = 0
        config.cost_aware_resume = True
        config.resume_queue_cost_weight = 0.25
        summaries["phase2_cost_aware_r4"] = execute_phase2(
            config, RuntimeEstimator.load(args.runtime_model), group=args.output_group)

    if args.policy == "cost-aware-r5-age30":
        run = run_config("full_phase2_cost_aware_n16_poisson2s_500_age30", args.trace,
                         args.n_tasks, args.concurrency, args.ctx, args.seed,
                         arrival_rate=0.5)
        config = quota_config(run, aging_threshold_s=30)
        config.max_consecutive_resume_priorities = 0
        config.cost_aware_resume = True
        config.resume_queue_cost_weight = 0.25
        summaries["phase2_cost_aware_r5_age30"] = execute_phase2(
            config, RuntimeEstimator.load(args.runtime_model), group=args.output_group)

    print(json.dumps(summaries, indent=2, default=str))


if __name__ == "__main__":
    main()

