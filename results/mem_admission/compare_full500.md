| metric | Phase 1 FIFO N4 (baseline) | Phase 1 FIFO N16 | E6: cap 2,048 + lookup order |
|---|---:|---:|---:|
| completed | 480/500 | 177/500 | 500/500 |
| failed | 20 | 323 | 0 |
| kv_context_failures | 20 | 323 | 0 |
| tasks_per_min | 4.2 | 4.4 | 3.9 |
| run_length_min | 113.9 | 40.6 | 129.3 |
| latency_mean_s | 2,598.0 | 27.7 | 2,056.2 |
| latency_p50_s | 2,873.6 | 23.7 | 1,277.3 |
| latency_p90_s | 4,479.1 | 48.9 | 5,291.0 |
| latency_p95_s | 4,767.6 | 57.8 | 6,111.0 |
| latency_p99_s | 5,552.6 | 82.7 | 6,947.4 |
| latency_max_s | 5,862.6 | 102.1 | 7,348.8 |
| latency_p50_1_step_s | 1,277.6 | 20.2 | 2,422.0 |
| latency_p50_2_step_s | 3,543.8 | 24.8 | 196.9 |
| latency_p50_3+_step_s | 4,553.4 | 34.7 | 1,432.3 |
| latency_p50_mbpp_s | 2,117.9 | 23.1 | 2,834.0 |
| latency_p50_nl2bash_s | 3,647.0 | 25.4 | 81.5 |
| ttft_step_p50_s | 0.2 | 0.1 | 0.9 |
| tpot_p50_ms | 16.6 | 18.6 | 26.3 |
| prompt_tokens_computed | 1,419,543.0 | 268,394.0 | 3,007,276 |
| mean_kv_pct | 48.3 | 49.5 | 70.1 |
| peak_kv_pct | 99.9 | 100.0 | 98.1 |
| mean_running | 4.0 | 6.4 | 5.6 |
| continuations | nan | nan | 327 |
| deadlock_discards | nan | nan | 8 |
| max_wait_s | nan | nan | 6,477.4 |
| peak_running | nan | nan | 9 |
