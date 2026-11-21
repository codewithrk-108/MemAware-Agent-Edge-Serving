# Phase 2 matched T4 smoke results

## Outcome

The 500 ms adaptive-concurrency policy completed 50/50 tasks with no KV-context failures. Relative to zero-grace slot-aware Phase 2, it reduced recomputed prompt tokens by 83.8%, increased cache hit rate from 26.5% to 71.6%, and recovered throughput from 3.089 to 3.359 tasks/min. Throughput is within 0.15% of Phase 1 FIFO while completing four additional tasks.

The result does not improve every latency metric. Resume priority preserved 40 short-wait caches but allowed returning multi-step tasks to bypass older first-step work. Completion p95 improved 1.48% relative to zero-grace, while completion p50 increased 42.7% and ready-queue p95 increased 15.0%. Adaptive grace solves most recomputation overhead but introduces substantial inter-task unfairness.

## Matched results

| Metric | Phase 1 FIFO | Phase 2 logical | Slot-aware, 0 ms | Adaptive, 500 ms | Adaptive vs 0 ms |
|---|---:|---:|---:|---:|---:|
| Completed tasks | 46/50 | 46/50 | 50/50 | **50/50** | same |
| KV-context failures | 4 | 4 | 0 | **0** | same |
| Completed tasks/min | 3.364 | 3.286 | 3.089 | **3.359** | +8.73% |
| Completion p50 | 390.8 s | 389.4 s | 438.4 s | 625.5 s | +42.67% |
| Completion p95 | 692.0 s | 727.2 s | 838.5 s | **826.1 s** | -1.48% |
| Ready-queue p95 | 552.6 s | 589.0 s | 668.6 s | 768.7 s | +14.96% |
| Run span | 820.5 s | 839.9 s | 971.1 s | **893.1 s** | -8.03% |
| Mean ready queue | 21.67 | — | 19.96 | 26.06 | +30.55% |
| Mean active requests | 3.536 | — | 3.360 | **3.749** | +11.57% |
| Mean physical KV use | 57.04% | — | 50.49% | 54.53% | +7.99% |
| Peak physical KV use | 99.99% | 99.98% | **88.98%** | 93.48% | +5.05% |
| Cache-hit ratio | 57.05% | 60.83% | 26.50% | **71.60%** | +170.23% |
| Prefill tokens computed | 131,377 | 135,209 | 284,603 | **109,848** | -61.40% |
| Cache evictions | — | — | 102 | **66** | -35.29% |
| Recomputed requests | — | — | 52 | **16** | -69.23% |
| Recomputed prompt tokens | — | — | 226,908 | **36,719** | -83.82% |

Phase 1 latency is survivor-biased because four tasks failed. On the same 46 tasks that succeeded under Phase 1, adaptive completion p50/p95 were 609.3/803.5 s versus Phase 1's 390.8/692.0 s. The latency gap is therefore real, not solely caused by the four additional completions.

## Adaptive behavior

- Grace window: 500 ms, selected because 40/63 trace tool waits finish within 500 ms and none additional finish between 500 ms and 1 s.
- Grace hits: 40 resumed requests reused their retained slots.
- Grace hold decisions: 112 dispatcher observations; this is a decision count, not elapsed hold time.
- Evictions: 16 expired-grace slot reassignments plus 50 terminal cleanups. There were no memory-pressure evictions.
- Reliability: 50 successes, no failures, no timeouts, and no erase-verification errors.
- Although slots were temporarily protected, less recomputation shortened service enough to raise average active concurrency from 3.36 to 3.75 requests.

## Interpretation

The grace period addresses the dominant zero-grace bottleneck. Prefill work fell below even Phase 1 because short tool returns reused their retained KV state. This recovered throughput and shortened total run time without reintroducing KV failures.

The remaining problem is fairness. A cache hit currently gives the returning request immediate priority on its retained slot. That is required to avoid instantly evicting the just-preserved cache, but repeated returns let multi-step tasks advance while older tasks remain queued. Median task completion and ready-queue tails therefore worsened even though per-task median service time fell from 303.1 s to 91.3 s.

The next policy should bound resume priority rather than remove grace. Candidate controls are a maximum consecutive resume count, disabling resume priority when the FIFO head exceeds an age threshold, or comparing saved recomputation time against added head-of-line delay. The 500 ms policy should be repeated before tuning so run-to-run variance is known.

## Validity

All policies used the same T4 host, Qwen3-0.6B-Q8_0 model, pinned llama.cpp b11780 runtime, four slots, 32K unified KV pool, 50 AgentTrace tasks, burst arrival, seed 2, tool delays, output budgets, frozen runtime estimator, and host-cache setting. The adaptive run changed only the grace/resume behavior. All 113 requests generated their requested output lengths.

## Artifacts

- `runs/smoke_phase1_fifo_n4_burst_50/`: matched FIFO baseline.
- `runs/smoke_phase2_two_queue_n4_burst_50/`: logical-only Phase 2.
- `runs/smoke_phase2_slot_aware_n4_burst_50/`: zero-grace slot-aware Phase 2.
- `runs/smoke_phase2_adaptive_n4_burst_50/`: adaptive raw results.
- `adaptive_smoke.log`: remote launcher output.
- `adaptive_provenance.txt`: launch configuration and checksums.
- `summary.csv`: compact four-policy comparison.
