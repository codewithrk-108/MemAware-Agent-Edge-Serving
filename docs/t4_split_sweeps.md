# Two-instance T4 load sweep

Each half runs six configurations sequentially, using the same fixed 50 distinct
tasks from `data/traces/agenttrace_50_seed2.jsonl`, task/arrival seed 2, Qwen3-0.6B
Q8_0 weights, f16 KV, unified 32768-cell pool, host prompt cache off, prompt reuse
on, request FIFO, and saved tool delays at scale 1. No delay-free runs.

| Part | Concurrency | Arrival patterns | Results directory |
|---|---|---|---|
| a | 2, 8 | Poisson mean 10s, Poisson mean 5s, burst | `results/t4_load_v1_part_a` |
| b | 4, 16 | Poisson mean 10s, Poisson mean 5s, burst | `results/t4_load_v1_part_b` |

Each arrival pattern is run at both listed concurrency settings. Task and request
deadlines remain 14400s and 900s. These are ceilings; actual saturation is measured,
not assumed. Both instances use the same instrumented runtime binary/libraries.
Do not start replay during source compilation or runtime transfer/validation.

Use a dedicated Git checkout and results branch on each instance, with Git author
identity configured. Set `MODEL` and `LLAMA_SERVER` to absolute paths, and run:

```bash
export SWEEP_LOG="$HOME/t4_load_part_a.log"
nohup bash phase1/run_split_sweep_and_stop.sh a > "$SWEEP_LOG" 2>&1 < /dev/null &
```

Use `b` on the second instance. `--require-kv-metrics` is mandatory. Raw per-task,
per-request, queue/admission, process/GPU memory, and physical KV occupancy data
are saved by the runner. The launcher adds provenance hashes, a run log, and exit
status. Each half writes its own summary CSV. Previous experiment results are not
overwritten.

The launcher commits its result directory on the instance after replay, including
partial results on sweep failure. Only after a successful Git commit does it
request shutdown. It requires a `.shutdown-approved` marker in the checkout,
created only after EC2 instance-initiated shutdown behavior is confirmed **Stop**.
Without the marker, or on a commit failure, the instance remains running.

Result commits remain on each instance's EBS-backed results branch. No GitHub
credentials are copied to the instances. Stopping preserves this disk; fetch the
result branches after restart to publish them. Do not terminate/delete the disks.

## Matched 64K, Poisson-5s smokes

`smoke64k_t4_n8` and `smoke64k_t4_n16` each run the same 50 tasks and seed with
65536 KV cells, 0.2 tasks/s Poisson arrivals, saved tool delays at scale 1, host
cache off, and required physical KV metrics. The other settings match the 32K
load sweep. Use clean checkouts to preserve existing work on either instance.
The launcher accepts `SWEEP_GROUP` and `OUTPUT_GROUP` overrides, plus an optional
`RESULT_COMMIT_MESSAGE`; its default part-A/B behavior is unchanged. Use separate
output groups `t4_smoke64k_poisson5s_n8_v1` and `t4_smoke64k_poisson5s_n16_v1`.
