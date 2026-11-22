# Aging and Cost-Aware Poisson-5s Comparison

Both new Phase 2 arms completed all 50 AgentTrace tasks without a KV context error. The existing FIFO baseline completed 46 and failed four.

| Policy | OK | Tasks/min | Completion p50 | Completion p95 | Cache hit | Prefill tokens | Backfills |
|---|---:|---:|---:|---:|---:|---:|---:|
| FIFO | 46 | 3.255 | 222.1 s | 527.3 s | 53.36% | 163,224 | diagnostic only |
| Quota, aging 30 | 50 | 3.311 | 299.6 s | 658.7 s | 59.90% | 155,333 | 0 |
| Quota, aging 360 | 50 | 3.223 | 301.8 s | 654.9 s | 54.24% | 176,284 | 9 |
| Cost-aware, aging 30 | 50 | 3.394 | 279.5 s | 640.4 s | 62.15% | 146,606 | 0 |

Raising aging to 360 seconds converted the previously closed windows into nine actual backfill admissions, but did not materially improve end-to-end performance. Versus quota aging-30, p95 improved 0.6% while throughput fell 2.7%, p50 rose 0.8%, and prefill work rose 13.5%.

Cost-aware resume was the best successful policy in this comparison. Versus quota aging-30, it improved throughput 2.5%, p50 6.7%, and p95 2.8%, with 5.6% less prefill work. Versus aging-360, it improved throughput 5.3%, p50 7.4%, and p95 2.2%. It still trails FIFO latency by 25.8% at p50 and 21.4% at p95, although FIFO's latency distribution excludes four failed tasks.

The configured score was permissive on this trace: 39 returns received priority and only one was denied by cost. Therefore this smoke supports the direction but does not establish that the cost gate itself caused all gains over unrestricted adaptive; a sensitivity sweep of the frozen queue-cost weight is needed before claiming that mechanism.
