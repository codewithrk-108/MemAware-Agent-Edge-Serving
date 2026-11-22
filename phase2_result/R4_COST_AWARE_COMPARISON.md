# Full R4 FIFO versus Cost-Aware Aging

The matched workload is the Phase 1 R4 configuration: 500 AgentTrace tasks, seed 2, N=4, Poisson arrivals with mean 2 seconds, and a 32K unified KV pool. Phase 1 FIFO values come from the published Phase 1 report; its latency distribution includes only the 472 successful tasks.

| Metric | FIFO R4 | Cost-aware R4 | Change |
|---|---:|---:|---:|
| Successful tasks | 472/500 | 500/500 | +28 tasks |
| Failures | 28 | 0 | -100% |
| Success rate | 94.4% | 100.0% | +5.6 points |
| Throughput | 4.26 tasks/min | 4.200 tasks/min | -1.4% |
| Completion p50 | 3,984 s | 3,499 s | -12.2% |
| Completion p95 | 5,420 s | 6,299 s | +16.2% |
| Peak KV utilization | 99.9% | 92.5% | -7.4 points |

Cost-aware scheduling eliminated all 28 FIFO KV-context failures while retaining 98.6% of FIFO throughput and improving median completion latency. Its tail latency remained worse: p95 increased by 879 seconds. FIFO's latency is survivor-biased because its failed tasks are excluded, but the cost-aware result still indicates that the zero-failure guarantee is being purchased with additional waiting at the tail.

The full run lasted 7,143 seconds (1 hour 59 minutes). It processed 1,068 requests with no request failures or runtime-estimator fallbacks. Of 336 within-grace returns, 327 received resume priority, nine were denied by the cost score, and 322 produced actual cache hits. It recomputed 684,897 prompt tokens across 233 requests and admitted no backfills because the 30-second aging threshold closed the candidate windows.

One of 733 slot erasures reported that aggregate physical KV release was not observed immediately, although the erase endpoint succeeded. The run continued safely, completed every task, reported no KV-context errors, and stayed below 92.5% peak physical KV occupancy. This is an instrumentation/control warning to investigate, not a task failure.

The detached supervisor exited with status zero, created a checksum-verified archive, and scheduled OS shutdown one minute later. The instance stopped successfully, validating this supervisor pattern for future unattended runs.
