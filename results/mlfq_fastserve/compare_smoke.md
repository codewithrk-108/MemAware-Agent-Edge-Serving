| metric | Baseline FIFO N4 | Exp 2: KV policy, returning first | Exp 3: MLFQ (FastServe-style) |
|---|---:|---:|---:|
| completed | 46/50 | 50/50 | 46/50 |
| kv_context_failures | 4 | 0 | 4 |
| tasks_per_min | 3.3 | 3.0 | 2.2 |
| latency_mean_s | 258.2 | 357.1 | 469.0 |
| latency_p50_s | 224.8 | 322.4 | 408.5 |
| latency_p95_s | 532.3 | 646.9 | 1,011.7 |
| slowdown_p50 | 13.4 | 16.8 | 24.2 |
| slowdown_p95 | 42.1 | 82.9 | 45.7 |
| SLO_2x_pct | 4.0 | 4.0 | 2.0 |
| SLO_5x_pct | 12.0 | 18.0 | 8.0 |
| SLO_10x_pct | 30.0 | 38.0 | 16.0 |
| SLO_20x_pct | 68.0 | 58.0 | 34.0 |
| latency_p50_1 step_s | 134.0 | 325.2 | 482.0 |
| latency_p50_2 steps_s | 283.3 | 246.0 | 263.1 |
| latency_p50_3+ steps_s | 502.1 | 538.9 | 825.9 |
| ttft_step_p50_ms | 140.9 | 519.9 | 189.6 |
| ttft_step_p95_ms | 2,695.5 | 2,864.3 | 1,981.0 |
| tpot_p50_ms | 16.3 | 19.8 | 23.0 |
| span_min | 13.8 | 16.5 | 21.0 |
| mean_kv_pct | 49.9 | 57.4 | 79.6 |
| peak_kv_pct | 99.5 | 95.1 | 99.9 |
| chunk_requests | nan | nan | 711.0 |
| preemptions | nan | nan | 58.0 |
| demotions | nan | nan | 224.0 |
| evictions_swap | nan | nan | 19.0 |
| evictions_discard | nan | nan | 62.0 |
| resume_recomputed_tokens | nan | nan | 229,910.0 |
