# One-resume/one-FIFO adaptive experiments

## Outcome

The quota-only policy completed 50/50 tasks with zero KV failures under both burst and Poisson-5s arrivals. It improved fairness relative to unrestricted adaptive, especially for burst, but did not beat plain FIFO latency. The quota denied 7 of 40 burst returns and 8 of 40 Poisson returns; no resume-age cutoff was active.

## Burst versus existing FIFO

| Metric | FIFO | Quota adaptive | Change |
|---|---:|---:|---:|
| Completed | 46/50 | **50/50** | +4 tasks |
| KV failures | 4 | **0** | -4 |
| Throughput | **3.364** | 3.288 tasks/min | -2.25% |
| Completion p50 | **390.8 s** | 509.6 s | +30.39% |
| Completion p95 | **692.0 s** | 770.2 s | +11.30% |
| Ready-queue p95 | **552.6 s** | 665.2 s | +20.38% |
| Cache-hit ratio | **57.05%** | 56.06% | -1.74% |
| Prefill tokens | **131,377** | 170,138 | +29.50% |
| Peak KV use | 99.99% | **94.76%** | -5.24% |
| Grace hits / quota denials | — | 33 / 7 | — |
| Recomputed requests/tokens | — | 27 / 103,139 | — |

Against unrestricted adaptive burst, quota improved completion p50 by 18.52%, p95 by 6.77%, and ready-queue p95 by 13.47%, while reducing throughput by 2.10% and cache hit ratio by 21.71%. On the 46 task IDs that FIFO completed, quota p50/p95 were 502.2/708.2 seconds versus FIFO's 390.8/692.0; the common-task p95 gap is only 2.34%.

## Poisson-5s versus existing FIFO

| Metric | FIFO | Quota adaptive | Change |
|---|---:|---:|---:|
| Completed | 46/50 | **50/50** | +4 tasks |
| KV failures | 4 | **0** | -4 |
| Throughput | 3.255 | **3.311 tasks/min** | +1.72% |
| Completion p50 | **222.1 s** | 299.6 s | +34.88% |
| Completion p95 | **527.3 s** | 658.7 s | +24.92% |
| Ready-queue p95 | **453.7 s** | 525.9 s | +15.91% |
| Cache-hit ratio | 53.36% | **59.90%** | +12.24% |
| Prefill tokens | 163,224 | **155,333** | -4.83% |
| Peak KV use | 96.84% | **92.20%** | -4.79% |
| Grace hits / quota denials | — | 32 / 8 | — |
| Recomputed requests/tokens | — | 25 / 93,854 | — |

Against unrestricted adaptive Poisson, quota improved p50 by 4.16% and ready-queue p95 by 9.53%, but p95 worsened 2.20% and throughput fell 4.12%. On FIFO's same 46 successful task IDs, quota p50/p95 were 281.1/634.8 seconds versus FIFO's 222.1/527.3.

## Interpretation

One-resume/one-FIFO is effective as a burst fairness control: it moves latency substantially toward FIFO while retaining zero failures. It is less useful under Poisson-5s because arrivals already spread queue pressure; quota gives up cache reuse without improving p95.

Neither workload produced a Phase 2 backfill admission. The quota should not be selected as a universal policy from these runs. A workload-aware policy could enable quota only when burstiness or oldest-ready wait exceeds a trigger, while leaving Poisson-like traffic unrestricted.

## Validity

- Existing FIFO artifacts were reused; no FIFO run was repeated.
- Same AgentTrace task order, seed, N=4, 32K unified KV, model, runtime, host-cache setting, prompt reuse, tool delays, deadlines, and output budgets.
- Quota arms used 500 ms grace, maximum one consecutive priority resume, and no resume-age cutoff.
- Both quota runs completed all 113 outputs without truncation, timeouts, KV errors, or erase-verification errors.
