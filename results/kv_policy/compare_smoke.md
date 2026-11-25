| metric | Phase 1 recorded FIFO N4 | Baseline FIFO N4 (rerun) | Exp 1: policy, requeue back | Exp 2: policy, returning first |
|---|---:|---:|---:|---:|
| completed | 46.0 | 46.0 | 46.0 | 50.0 |
| tasks | 50.0 | 50.0 | 50.0 | 50.0 |
| completion_pct | 92.0 | 92.0 | 92.0 | 100.0 |
| failed_kv_context | 4.0 | 4.0 | 4.0 | 0.0 |
| timeouts | 0.0 | 0.0 | 0.0 | 0.0 |
| tasks_per_min | 3.3 | 3.3 | 3.1 | 3.0 |
| latency_p50_s | 222.4 | 224.8 | 267.5 | 322.4 |
| latency_p95_s | 527.5 | 532.3 | 640.3 | 646.9 |
| ready_queue_p50_s | 205.1 | 206.6 | 259.3 | 242.3 |
| ready_queue_p95_s | 453.6 | 462.8 | 506.4 | 591.7 |
| span_min | 14.1 | 13.8 | 14.9 | 16.5 |
| ttft_p50_ms | 125.0 | 140.9 | 465.4 | 519.9 |
| ttft_p95_ms | 2,598.5 | 2,695.5 | 2,304.5 | 2,864.3 |
| tpot_p50_ms | 15.9 | 16.1 | 16.7 | 19.8 |
| prefill_tokens | 163,142.0 | 155,777.0 | 175,069.0 | 136,207.0 |
| later_step_reuse_pct | 47.3 | 42.5 | 59.5 | 76.1 |
| kv_mean_pct | 52.5 | 49.9 | 50.5 | 57.4 |
| kv_peak_pct | 96.8 | 99.5 | 94.2 | 95.1 |
| decisions_stay | nan | nan | 15.0 | 48.0 |
| decisions_swap | nan | nan | 12.0 | 3.0 |
| decisions_discard | nan | nan | 35.0 | 12.0 |
| reclaims | nan | nan | 0.0 | 0.0 |
| swap_failures | nan | nan | 0.0 | 0.0 |
| restore_failures | nan | nan | 1.0 | 0.0 |
| swap_out_ms_total | nan | nan | 21,223.7 | 6,155.4 |
| swap_in_ms_total | nan | nan | 9,034.6 | 4,102.7 |
