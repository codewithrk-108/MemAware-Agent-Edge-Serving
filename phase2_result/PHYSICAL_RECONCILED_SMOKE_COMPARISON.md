# Physical-Reconciled Admission Smoke

The experiment changes only admission accounting relative to the existing cost-aware Poisson-5s smoke. Both use 50 AgentTrace tasks, seed 2, N=4, 32K unified KV, aging 30 seconds, slot-aware eviction, 500 ms cache grace, and queue-cost weight 0.25.

| Metric | Logical cost-aware | Physical-reconciled | Change |
|---|---:|---:|---:|
| Successful tasks | 50/50 | 50/50 | unchanged |
| KV-context failures | 0 | 0 | unchanged |
| Throughput | 3.394/min | 3.069/min | -9.6% |
| Completion p50 | 279.5s | 379.8s | +35.9% |
| Completion p95 | 640.4s | 738.7s | +15.4% |
| Ready-queue p95 | 528.5s | 616.5s | +16.7% |
| Mean queue length | 15.74 | 17.55 | +11.5% |
| Mean running | 3.18 | 2.90 | -9.0% |
| Mean physical KV | 54.8% | 51.6% | -3.2 points |
| Peak physical KV | 97.6% | 97.7% | +0.2 points |
| Blocked-head decisions | 21 | 96 | +75 |
| Backfill admissions | 0 | 1 | +1 |
| Cache-hit ratio | 62.1% | 59.9% | -2.2 points |
| Recomputed prompt tokens | 72,797 | 85,648 | +17.7% |

Physical accounting was active for 209 scheduler decisions with zero sample errors and zero logical fallbacks. The run completed safely, but it did not improve utilization or performance.

The conservative formula adds each active request's full worst-case incremental growth to a physical sample that already contains some of that growth. Without per-request live progress, this double-counts partially materialized prompt/output growth. It produced 96 blocked-head decisions versus 21 under logical accounting, reduced active concurrency, increased queueing, and lowered mean KV utilization despite a similar peak.

The result rejects this formula as an authoritative replacement. A future physical policy needs per-slot progress or a calibrated bound on *remaining* growth, not original incremental growth. Until that signal exists, logical accounting remains the better policy.
