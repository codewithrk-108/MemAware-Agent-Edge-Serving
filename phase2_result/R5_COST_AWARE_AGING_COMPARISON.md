# Full R5 Cost-Aware Aging Comparison

Both experiments use the Phase 1 R5 workload: 500 AgentTrace tasks, seed 2, N=16, Poisson arrivals with mean 2 seconds, and a 32K unified KV pool. They differ only in the scheduler aging threshold.

| Metric | FIFO R5 | Cost-aware age 30 | Cost-aware age 360 |
|---|---:|---:|---:|
| Successful tasks | 100/500 | 500/500 | 500/500 |
| KV-context failures | 400 | 0 | 0 |
| Throughput | 5.55 tasks/min | 4.166 tasks/min | 4.186 tasks/min |
| Completion p50 | 127 s | 3,218 s | 3,074 s |
| Completion p95 | 270 s | 6,275 s | 6,164 s |
| Average queue length | — | 222.0 | 207.0 |
| Average running | 15.72 | 5.39 | 5.71 |
| Average KV utilization | 77.8% | 66.5% | 66.8% |
| Peak KV utilization | 99.9% | 96.3% | 94.9% |
| Backfill admissions | — | 38 | 135 |
| Cache-hit ratio | — | 45.8% | 44.1% |
| Recomputed prompt tokens | — | 935,215 | 979,891 |

Increasing aging from 30 to 360 seconds created 97 additional backfill admissions. Relative to age 30, age 360 improved throughput by 0.5%, p50 by 4.5%, p95 by 1.8%, and average queue length by 6.7%. Average running concurrency increased 5.9%. These gains came with a 1.7-point lower cache-hit ratio and 4.8% more recomputed prompt tokens.

Both Phase 2 policies eliminated all 400 FIFO KV-context failures. FIFO's 127/270-second p50/p95 are not directly comparable because they include only the 100 surviving tasks; the other 400 tasks failed rather than waiting to completion. FIFO's higher reported throughput is likewise completed-task throughput over a much shorter failure-dominated run.

The age-360 result demonstrates that a larger aging threshold enables substantial backfill at N=16, but the end-to-end gain is modest. Backfill improves queue utilization while causing more cache eviction and recomputation, limiting its throughput and latency benefit.

## Common FIFO-success task subset

Restricting all policies to the exact 100 task IDs that succeeded under FIFO controls for task identity but not system load. FIFO p50/p95 are 126.7/269.6 seconds, age 30 is 3,159.0/5,395.6 seconds, and age 360 is 2,858.6/5,355.1 seconds. Median accumulated ready-queue time is 97.4 seconds for FIFO, 3,135.6 seconds for age 30, and 2,822.5 seconds for age 360. FIFO remains artificially lightly loaded because the other 400 tasks fail and leave the system, whereas both Phase 2 runs retain all 500 tasks until completion.

The active-concurrency timeline is stored in `r5_aging_running_concurrency_over_time.png`. Its one-second time-weighted means are 5.54 running requests for age 30 and 5.60 for age 360, despite the configured maximum N=16. KV-safe admission, rather than the slot maximum, controls effective concurrency.

The event-aware adaptive-concurrency plot is stored in `r5_adaptive_concurrency_effect_over_time.png` and includes FIFO on the same N=16 scale and 120-minute axis. FIFO's time-weighted N is 15.70, but its run ends after roughly 18 minutes with only 100/500 completions. Age 30 recorded 1,168 grace-hold decisions: 1,138 protected one slot and 30 protected two. Age 360 recorded 1,213: 1,164 protected one slot, 47 protected two, and two protected three. Thus the 500 ms cache grace usually reduces the temporary slot ceiling from 16 to 15, while actual N remains around 5--6. Memory-aware admission and KV/slot compatibility, not grace protection, explain most of the gap between configured and actual concurrency.
