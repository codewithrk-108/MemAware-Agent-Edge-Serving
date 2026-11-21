# Unrestricted adaptive Poisson-5s smoke

## Outcome

The previous unrestricted 500 ms adaptive policy completed 50/50 tasks with zero KV failures, versus 46/50 and four KV failures for matched FIFO. Adaptive completed 3.454 tasks/min, 6.10% above FIFO, while reducing computed prefill tokens by 30.73% and increasing cache hit ratio from 53.36% to 70.69%.

The policy did not meet the predeclared latency target. Completion p95 increased 22.23% and ready-queue p95 increased 28.11%; the target allowed at most a 10% p95 increase. The result is therefore a reliability/throughput/cache improvement, not an across-the-board latency improvement.

## Matched results

| Metric | FIFO Poisson-5s | Adaptive Poisson-5s | Change |
|---|---:|---:|---:|
| Completed tasks | 46/50 | **50/50** | +4 tasks |
| KV-context failures | 4 | **0** | -4 |
| Completed tasks/min | 3.255 | **3.454** | +6.10% |
| Completion p50 | **222.1 s** | 312.6 s | +40.74% |
| Completion p95 | **527.3 s** | 644.6 s | +22.23% |
| Ready-queue p95 | **453.7 s** | 581.3 s | +28.11% |
| Run span | **847.8 s** | 868.6 s | +2.45% |
| Mean ready queue | **13.11** | 15.14 | +15.47% |
| Mean active requests | **3.605** | 3.588 | -0.49% |
| Mean physical KV use | 53.03% | 53.85% | +1.56% |
| Peak physical KV use | 96.84% | **95.14%** | -1.75% |
| Cache-hit ratio | 53.36% | **70.69%** | +32.47% |
| Prefill tokens computed | 163,224 | **113,063** | -30.73% |
| Grace returns / actual hits | — | 40 / 38 | — |
| Cache evictions | — | 68 | — |
| Recomputed requests/tokens | — | 18 / 47,472 | — |

FIFO latency is survivor-biased because four tasks failed. On the same 46 task IDs that succeeded under FIFO, adaptive p50/p95 were 289.4/650.4 seconds versus FIFO's 222.1/527.3 seconds; the latency increase remains after controlling for survivors.

## Backfill result

FIFO reported seven diagnostic backfill opportunities. Adaptive made 113 FIFO-head admissions and zero backfill admissions. Its 290 scheduler observations all had `candidate_count=0`:

- 8 `head_blocked` decisions;
- 15 `aged_head_blocked` decisions;
- blocked-head median age 389.9 seconds;
- blocked-head median backfill allowance 0 seconds, maximum 22 seconds.

Poisson arrivals created memory-blocked heads but not candidates satisfying fit, deadline, and runtime-allowance constraints. A larger dataset alone may increase opportunities, but the current 30-second aging threshold and 8-second guard close most candidate windows once the queue is old.

## Validity

- Same T4 instance, model, pinned llama.cpp runtime, N=4, and 32K unified KV pool.
- Same 50 task IDs in the same sampled order.
- Same seed and exponential arrival generation; relative arrival offsets matched within 29 ms maximum and 1.5 ms at p95.
- Same prompt reuse, host-cache setting, tool delays, deadlines, and output budgets.
- Adaptive fairness controls were both zero: unrestricted resume priority.
- Adaptive completed all 113 requested outputs with no truncation.
- FIFO's four shortened/error requests correspond to its four KV-context failures.

## Implementation correction during the experiment

An initial adaptive attempt revealed that zero fairness limit still blocked simultaneous pending returns; it was rejected as mislabeled. After correcting zero to mean unrestricted, a second attempt exposed an unsafe worker choice when a lower-numbered free slot retained another task's cache. The active-slot safety check aborted before corruption. The final implementation restricts every queued request to its own retained slot or an unowned slot and allows backfill only when that candidate has a compatible worker. Only the valid final run is stored here.

## Decision

The experiment passes completion, reliability, and throughput targets but fails the p95-latency target. Keep unrestricted adaptive as the throughput-oriented policy, but do not freeze it as the balanced final policy. Backfill tuning should first enlarge the candidate window on the development workload, then rerun Poisson-5s.
