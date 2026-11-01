# AgentTrace Full-500 Task Sweep Report

## Scope

This report consolidates the completed 500-task AgentTrace runs found on:

- `34.217.112.115` (`ip-172-31-3-22.us-west-2.compute.internal`)
- `32.191.69.145` (`ip-172-31-46-52.us-west-2.compute.internal`)

All runs used `agenttrace_full_500_seed2.jsonl`, Qwen3-0.6B-Q8_0, request-FIFO scheduling, saved tool delays, prompt reuse enabled, host prompt cache disabled, and physical KV metrics. The common context budget was 32K unless noted otherwise.

The runs were split across two result trees:

- `phase-1-full500-v1/results/t4_full500_v1_part_a`
- `phase-1-full500-v1/results/t4_full500_v1_part_b`

## Run matrix

| Run | Host | Concurrency | Arrival pattern | KV capacity | Completed | Failed | Success rate | Avg queue | Max queue | Avg running | Span (s) |
|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `c1_n1_poisson10s` | `34.217.112.115` | 1 | Poisson, 10 s | 32,768 | 500 | 0 | 100.0% | 174.04 | 331 | 1.00 | 11,094.3 |
| `c2_n4_poisson5s` | `34.217.112.115` | 4 | Poisson, 5 s | 32,768 | 480 | 20 | 96.0% | 191.20 | 358 | 3.99 | 6,836.9 |
| `c3_n16_poisson5s` | `34.217.112.115` | 16 | Poisson, 5 s | 32,768 | 177 | 323 | 35.4% | 0.03 | 6 | 6.40 | 2,437.6 |
| `c4_n4_poisson2s` | `32.191.69.145` | 4 | Poisson, 2 s | 32,768 | 472 | 28 | 94.4% | 247.76 | 451 | 4.00 | 6,646.4 |
| `c5_n16_poisson2s` | `32.191.69.145` | 16 | Poisson, 2 s | 32,768 | 100 | 400 | 20.0% | 37.99 | 79 | 15.72 | 1,081.7 |
| `c6_n4_burst` | `32.191.69.145` | 4 | Burst, all tasks at t=0 | 32,768 | 476 | 24 | 95.2% | 289.92 | 496 | 4.00 | 6,639.9 |
| `c7_n16_poisson2s_ctx64k` | `32.191.69.145` | 16 | Poisson, 2 s | 65,536 | 340 | 160 | 68.0% | 220.99 | 384 | 15.05 | 4,553.4 |

No run recorded queued or running task timeouts. Every failure was classified as a KV-context error.

## Performance and queueing

| Run | Completion p50 (s) | Completion p95 (s) | Queue p50 (s) | Queue p95 (s) | Ready-queue p95 (s) | Throughput (tasks/min) |
|---|---:|---:|---:|---:|---:|---:|
| `c1_n1_poisson10s` | 4,029.3 | 7,351.1 | 2,179.1 | 3,348.9 | 7,279.2 | 2.70 |
| `c2_n4_poisson5s` | 2,873.6 | 4,767.6 | 1,345.8 | 2,354.7 | 4,768.7 | 4.21 |
| `c3_n16_poisson5s` | 23.7 | 57.8 | ~0.0 | ~0.0 | ~0.0 | 4.36 |
| `c4_n4_poisson2s` | 3,984.3 | 5,420.4 | 1,646.3 | 2,932.8 | 5,592.2 | 4.26 |
| `c5_n16_poisson2s` | 126.7 | 269.6 | 75.2 | 114.0 | 208.1 | 5.55 |
| `c6_n4_burst` | 4,241.4 | 6,049.6 | 1,936.9 | 3,405.2 | 6,160.5 | 4.30 |
| `c7_n16_poisson2s_ctx64k` | 1,959.4 | 3,631.0 | 984.5 | 2,065.3 | 3,420.5 | 4.48 |

The average and maximum queue columns above are task/request entries waiting
in the scheduler's FIFO queue, computed from `timeline.csv`. They are
time-weighted over each complete run. `Avg running` is the corresponding
time-weighted number of active model requests; it can be below configured
concurrency when requests fail early or when no runnable work is available.

The high throughput of the 16-way 32K run is not a reliable capacity result:
most tasks failed quickly from KV exhaustion. The 64K run completed more work
and had substantially higher completion latency because more requests remained
resident and progressed further before failure.

## Memory and KV-cache results

| Run | Average KV utilization | Peak KV cells | KV capacity | Peak KV utilization | Peak GPU (MiB) | Peak server footprint (MiB) |
|---|---:|---:|---:|---:|---:|---:|
| `c1_n1_poisson10s` | 13.63% | 16,062 | 32,768 | 49.02% | 4,383 | 701.2 |
| `c2_n4_poisson5s` | 48.76% | 32,738 | 32,768 | 99.91% | 4,383 | 719.1 |
| `c3_n16_poisson5s` | 49.83% | 32,768 | 32,768 | 100.00% | 4,383 | 754.3 |
| `c4_n4_poisson2s` | 48.97% | 32,737 | 32,768 | 99.91% | 4,383 | 717.6 |
| `c5_n16_poisson2s` | 77.04% | 32,746 | 32,768 | 99.93% | 4,377 | 739.4 |
| `c6_n4_burst` | 49.24% | 32,747 | 32,768 | 99.94% | 4,383 | 717.6 |
| `c7_n16_poisson2s_ctx64k` | 74.05% | 65,536 | 65,536 | 100.00% | 7,999 | 801.0 |

GPU VRAM was not exhausted in the 32K runs: peak usage was approximately
4.4 GiB of 15 GiB. Increasing the KV capacity to 64K increased peak GPU usage
to approximately 8.0 GiB, still below the 15 GiB device limit. The dominant
resource bottleneck was therefore the unified KV capacity, not total GPU VRAM.

Average KV utilization is the time-weighted mean of the sampled
`usage_ratio` values in `kv_memory.csv`, over the complete run including idle
and draining periods. It is therefore lower than the peak and should not be
interpreted as utilization conditional on active decoding.

## Failure and admission behavior

| Run | Requests | Failed requests | KV-context errors | Admission decisions | Head-would-exceed-budget | Backfill opportunities |
|---|---:|---:|---:|---:|---:|---:|
| `c1_n1_poisson10s` | 1,068 | 0 | 0 | 1,068 | 0 | 0 |
| `c2_n4_poisson5s` | 1,036 | 20 | 20 | 1,036 | 37 | 23 |
| `c3_n16_poisson5s` | 741 | 323 | 323 | 741 | 299 | 0 |
| `c4_n4_poisson2s` | 1,036 | 28 | 28 | 1,036 | not recorded | not recorded |
| `c5_n16_poisson2s` | 657 | 400 | 400 | not recorded | not recorded | not recorded |
| `c6_n4_burst` | 1,037 | 24 | 24 | 1,037 | not recorded | not recorded |
| `c7_n16_poisson2s_ctx64k` | 909 | 160 | 160 | not recorded | not recorded | not recorded |

The 32K N=16 configurations fail earlier and more often because many active
histories compete for the shared pool. N=4 remains mostly reliable, but its
lower concurrency creates substantial FIFO and ready-queue delay. Doubling the
context capacity from 32K to 64K materially improves N=16 success from 20.0%
to 68.0%, but does not eliminate KV failures.

## Prompt reuse and generation

| Run | Cache-hit ratio | Prefill tokens computed | Cached prompt tokens | Generation tok/s |
|---|---:|---:|---:|---:|
| `c1_n1_poisson10s` | 0.383 | 1,806,028 | 1,120,500 | 143.5 |
| `c2_n4_poisson5s` | 0.425 | 1,419,543 | 1,071,570 | 221.5 |
| `c3_n16_poisson5s` | 0.602 | 268,394 | 693,001 | 166.8 |
| `c4_n4_poisson2s` | 0.433 | not recorded | not recorded | not recorded |
| `c5_n16_poisson2s` | 0.173 | not recorded | not recorded | not recorded |
| `c6_n4_burst` | 0.434 | not recorded | not recorded | not recorded |
| `c7_n16_poisson2s_ctx64k` | 0.560 | not recorded | not recorded | not recorded |

The high cache-hit ratio in the N=16 32K run is partly a consequence of
early failures and should not be interpreted as better end-to-end efficiency.

## Conclusions

1. **N=1 is the reliability reference.** It completed all 500 tasks with
   49% peak KV utilization, but took approximately 3.08 hours.
2. **N=4 is the practical reliability/performance region.** It completed
   94.4–96.0% of tasks under Poisson load and 95.2% under burst load, while
   maintaining approximately 4.2–4.3 completed tasks/minute.
3. **N=16 with 32K KV is an admission-pressure stress test.** It completed
   only 20.0–35.4% of tasks; failures were KV-context errors, not queue or
   deadline timeouts.
4. **Increasing KV capacity helps but does not fully solve N=16.** The 64K
   run reached 68.0% success, with peak GPU usage increasing from about 4.4
   GiB to 8.0 GiB.
5. **Queueing is driven by concurrency and arrival pressure.** N=4 builds
   multi-thousand-second tail waits, while N=16 has little queueing in the
   32K Poisson-5s run because requests fail before a large backlog forms.
6. **The dominant bottleneck is shared KV-cache capacity.** GPU memory headroom
   remains available in the 32K configurations, so increasing VRAM alone
   would not address the observed failures unless the KV budget is increased
   as well.

## Reproducibility

Common settings:

```text
tasks:              agenttrace_full_500_seed2.jsonl
tasks per run:      500
model:              Qwen3-0.6B-Q8_0.gguf
scheduler:          request FIFO
seed:               2
delay scale:        1.0
cache prompt:       enabled
host cache:         --cache-ram 0
KV mode:            unified (-kvu)
deadline:           21,600 s
request timeout:    1,800 s
```

The primary runtime controls were `-c 32768` or `-c 65536` and `-np` equal to
the configured concurrency. Metrics came from `meta.json`, `timeline.csv`,
`kv_memory.csv`, `gpu_memory.csv`, and `tasks.csv` in each remote result
directory.
