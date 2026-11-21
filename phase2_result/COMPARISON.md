# Phase 2 matched T4 smoke results

## Outcome

All slot-aware policies completed 50/50 tasks without KV-context failures. Unrestricted 500 ms adaptive grace delivered the best throughput and cache reuse, but harmed FIFO fairness. The fair-adaptive follow-up restored median and queue latency by applying one-resume/one-FIFO weighting plus a 30-second old-head cutoff, but the cutoff denied 32 of 40 short tool returns and almost completely removed the cache benefit.

The fair run demonstrates that the latency regression is controllable, but this exact configuration is not the best policy: it performs almost identically to zero-grace slot-aware Phase 2. The next ablation should retain the one-for-one quota while disabling or substantially increasing the head-age cutoff.

## Matched results

| Metric | Phase 1 FIFO | Slot-aware, 0 ms | Adaptive, 500 ms | Fair adaptive | Fair vs adaptive |
|---|---:|---:|---:|---:|---:|
| Completed tasks | 46/50 | 50/50 | 50/50 | **50/50** | same |
| KV-context failures | 4 | 0 | 0 | **0** | same |
| Completed tasks/min | 3.364 | 3.089 | **3.359** | 3.095 | -7.87% |
| Completion p50 | 390.8 s | 438.4 s | 625.5 s | **448.5 s** | -28.29% |
| Completion p95 | 692.0 s | 838.5 s | **826.1 s** | 837.0 s | +1.32% |
| Ready-queue p95 | 552.6 s | 668.6 s | 768.7 s | **668.7 s** | -13.01% |
| Run span | 820.5 s | 971.1 s | **893.1 s** | 969.4 s | +8.54% |
| Mean ready queue | 21.67 | 19.96 | 26.06 | **19.65** | -24.57% |
| Mean active requests | 3.536 | 3.360 | **3.749** | 3.372 | -10.04% |
| Mean physical KV use | 57.04% | 50.49% | **54.53%** | 51.20% | -6.11% |
| Peak physical KV use | 99.99% | **88.98%** | 93.48% | 89.27% | -4.50% |
| Cache-hit ratio | 57.05% | 26.50% | **71.60%** | 27.07% | -62.19% |
| Prefill tokens computed | 131,377 | 284,603 | **109,848** | 282,472 | +157.15% |
| Cache evictions | — | 102 | **66** | 101 | +53.03% |
| Recomputed requests | — | 52 | **16** | 51 | +218.75% |
| Recomputed prompt tokens | — | 226,908 | **36,719** | 224,734 | +512.04% |

Phase 1 latency is survivor-biased because four tasks failed. On the same 46 tasks that succeeded under Phase 1, fair-adaptive completion p50/p95 were 413.4/786.6 s, versus 609.3/803.5 s for unrestricted adaptive and 390.8/692.0 s for Phase 1.

## Fairness-control behavior

- Grace returns: 40 requests returned within 500 ms while their slot still existed.
- Actual grace hits: 8.
- Priority grants: 8.
- Head-age denials: 32.
- One-for-one quota denials: 0.
- Recomputed requests: 51.
- Evictions: 101, including 50 terminal cleanups.
- Reliability: 50 successes, no failures, no timeouts, and no erase-verification errors.

Because the trace is a 50-task burst, the oldest FIFO head exceeds 30 seconds for most of the run. The age cutoff therefore dominates the policy before the one-for-one quota can activate. Fair-adaptive differs from zero-grace by less than 1% on throughput, prefill work, evictions, and recomputed tokens.

## Interpretation and next step

Unrestricted adaptive grace proves that retaining short-wait KV can recover Phase 1 throughput while eliminating failures. Fair-adaptive proves that bypass control can restore queue fairness. The next policy should combine these benefits by testing the quota independently:

1. Keep 500 ms cache grace.
2. Keep at most one priority resume before one FIFO admission.
3. Disable the 30-second head-age cutoff for the first ablation, or raise it to 300 seconds.
4. Compare priority grants, quota denials, cache hits, p50/p95 completion, and throughput.

The current 30-second fair-adaptive policy should not be frozen.

## Validity

All policies used the same T4 host, Qwen3-0.6B-Q8_0 model, pinned llama.cpp b11780 runtime, four slots, 32K unified KV pool, 50 AgentTrace tasks, burst arrival, seed 2, tool delays, output budgets, frozen estimator, and host-cache setting. The fair run changed only resume-priority controls. All 113 requests completed with their requested output lengths.

## Artifacts

- `runs/smoke_phase1_fifo_n4_burst_50/`: matched FIFO baseline.
- `runs/smoke_phase2_slot_aware_n4_burst_50/`: zero-grace slot-aware Phase 2.
- `runs/smoke_phase2_adaptive_n4_burst_50/`: unrestricted adaptive results.
- `runs/smoke_phase2_fair_adaptive_n4_burst_50/`: fair-adaptive results.
- `adaptive_smoke.log` and `fair_adaptive_smoke.log`: remote outputs.
- `adaptive_provenance.txt` and `fair_adaptive_provenance.txt`: configuration provenance.
- `summary.csv`: compact comparison.
