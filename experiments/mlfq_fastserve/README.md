# Experiment 3: FastServe-style MLFQ on llama.cpp

A skip-join multi-level feedback queue (MLFQ) in the style of FastServe (Wu et al., 2023), run in front of the same
llama.cpp server as the Phase 1 FIFO N4 baseline and experiments 1–2. Short jobs should finish ahead of long ones
without the scheduler knowing output lengths. An illustrated walkthrough of the scheduler is published as a private
artifact (MLFQ Scheduler on llama.cpp).

## How it works ([`scheduler.py`](scheduler.py))

- **Job** = one model request (one agent step). A task becomes a new job every time its tool returns.
- **Levels:** quanta of 256, 512, 1,024, 2,048, 4,096 output tokens, then a last level with no limit. FIFO within a level.
- **Skip-join:** a new job enters the first level whose quantum time (quantum × running-average time per output
  token) covers its predicted prefill time (recompute cost model). In practice every job joined level 0.
- **Chunks and preemption:** a job runs in 256-token chunks, each a separate `/completion` call with the full history,
  pinned to the job's slot, so the cache is reused. After each chunk: step done → tool call or finish; quantum used →
  demote to the back of the next level; a higher-level job waiting → preempt to the front of its own level;
  otherwise run the next chunk.
- **N4:** at most 4 chunks run at once. The server has 16 slots so idle jobs keep their KV.
- **KV:** idle caches (preempted jobs and tasks waiting on a tool) stay in their slots; nothing is decided at a tool
  call. Before a dispatch, the scheduler checks `history + chunk − resident cells ≤ 32,768 − reserved`
  (reserved = running chunks at `history + chunk` plus idle caches). If it doesn't fit, or no slot is free, idle
  caches are evicted, lowest priority first (preempted jobs from the deepest level, then tasks waiting on a tool).
  Each eviction is a swap (slot save to `/dev/shm`) or a discard, whichever the cost models predict cheaper, swap
  only within a 50K-token host budget. If the head job still doesn't fit, it waits and nothing behind it starts.

## Assumptions

- **Same workload and server as the experiment 1–2 smoke runs:** 50-task trace, Poisson arrivals with mean 5 s,
  seed 2 (identical task order and arrival times), Qwen3-0.6B Q8_0, f16 KV, unified 32K-cell pool, `--cache-ram 0`,
  no mmap, greedy, `ignore_eos`, tool delays at scale 1, 21,600 s deadline, 1,800 s request timeout. `-np 16`, as in
  experiments 1–2; the baseline uses `-np 4`.
- **Preemption only between chunks.** llama.cpp cannot preempt a running request from outside, so a job yields after
  at most 256 tokens (~4 s), not at every decoding step as in FastServe.
- **Priority is per request, not per task:** each agent step starts with a fresh quantum, as FastServe treats each
  request independently.
- **Output lengths are not used** for scheduling; output budgets are used only for the KV fit check of a chunk.
- **Cost models reused unchanged** from `swap_vs_stay_vs_recompute/params.json` (fitted under FIFO N4).
- **Untuned settings:** chunk 256 tokens, the quanta above, 16 slots, 50K-token host budget, no aging.
- **SLO reference:** each task's solo latency from `results/solo_SLO/smoke50_v1` (the same 50 tasks run alone,
  KV retained). Failed tasks count as SLO misses.

## Smoke results (50 tasks, one run each)

From [`results/mlfq_fastserve/compare_smoke.md`](../../results/mlfq_fastserve/compare_smoke.md):

| | Baseline FIFO N4 | Exp 2: KV policy, returning first | **Exp 3: MLFQ** |
|---|---:|---:|---:|
| Completed | 46/50 | 50/50 | 46/50 |
| KV-context failures | 4 | 0 | 4 |
| Tasks/min | 3.34 | 3.04 | **2.19** |
| Latency mean / p50 / p95 (s) | 258 / 225 / 532 | 357 / 322 / 647 | **469 / 408 / 1,012** |
| Slowdown vs solo, p50 / p95 | 13.4× / 42.1× | 16.8× / 82.9× | 24.2× / 45.7× |
| Within 5× / 10× / 20× of solo | 12% / 30% / 68% | 18% / 38% / 58% | 8% / 16% / 34% |
| Latency p50: 1-step / 2-step / 3+-step tasks (s) | 134 / 283 / 502 | 325 / 246 / 539 | 482 / 263 / 826 |
| Time to first token of a step, p50 (ms) | 141 | 520 | 190 |
| Time per output token, p50 (ms) | 16.3 | 19.8 | **23.0** |
| Mean / peak KV use | 50% / 99.5% | 57% / 95.1% | **80%** / 99.9% |
| Run length (min) | 13.8 | 16.5 | 21.0 |

MLFQ activity: 711 chunk requests, 58 preemptions, 224 demotions, 81 evictions (19 swap, 62 discard; 75 of preempted
jobs, 6 of tasks waiting on a tool).

**Experiment 3 was worse than the baseline on every headline metric:** 35% lower throughput, 82% higher median
latency, and lower SLO attainment at every α. It did not reduce failures either.

## Why it lost

1. **A full pool slows every request.** Keeping every idle cache pushed mean KV use to 80%, and decoding slowed to
   23.0 ms per token. Across the three runs, time per token rises with mean KV use (50% → 16.3 ms, 57% → 19.8 ms,
   80% → 23.0 ms). This is consistent with attention in llama.cpp's unified pool scanning the whole occupied range,
   including other requests' idle cells. Idle KV costs speed, not just memory. **Confirmed afterwards by Study C**
   ([`../idle_kv_microbench/`](../idle_kv_microbench/README.md)): every 1K cells of occupied range adds ~0.45 ms per
   decode step, and the cost follows the highest occupied cell, not the number of cells.
2. **Evictions caused heavy recomputation.** 62 discards and occasional partial cache reuse meant 229,910 prompt
   tokens were recomputed when jobs resumed (median resume recomputes 1 token, but p95 is 2,909), more than the
   baseline's whole prefill total (155,777).
3. **Per-request priority favours multi-step tasks.** Every agent step re-enters level 0 with a fresh quantum, so a
   task with many short steps stays at high priority, while a single long step is demoted level by level.
   One-step tasks, whose single request is often long, went from a 134 s median in the baseline to 482 s.
4. **KV-context failures remained (4).** The fit check should prevent overflow, yet the pool still overflowed at
   99.9% use. *First guess (wrong):* fragmentation. *Corrected from the llama.cpp source and server log (lab log
   section 11):* cells need not be contiguous, so fragmentation is ruled out. When a batch found no free space, the
   server purged idle slots' caches on its own (19 times in this run); the scheduler still counted those caches as
   resident, its accounting drifted, and one overflow at 1,062.1 s failed all 4 running requests at once.
5. **Overload hides the benefit.** Arrivals (12 tasks/min) far exceed capacity (~3 tasks/min), so every task queues;
   reordering cannot shorten the backlog, and the slowdown above reduces capacity further.

## What to try next

- Evict idle caches before the pool is nearly full (e.g. keep it under 60–70%), not only when a job doesn't fit.
- Track priority per **task** (total tokens served across all its steps), so agents don't reset to level 0 at each
  tool call.
- Add a KV safety margin of a few thousand cells to the fit check.
- Run at a load below capacity (2.5–3.8 tasks/min) where ordering, not backlog, decides latency.
- Measure time per token against idle cells in the pool directly (a microbenchmark), to confirm cause 1.

## Run

```bash
export MODEL=/path/to/Qwen3-0.6B-Q8_0.gguf LLAMA_SERVER=/path/to/instrumented/llama-server
uv run python -m experiments.mlfq_fastserve.scheduler --name smoke_exp3_mlfq \
  --trace data/traces/agenttrace_50_seed2.jsonl --n-tasks 50 --rate 0.2      # ~21 min
uv run python -m experiments.mlfq_fastserve.compare                         # needs the kv_policy smoke runs
```

Outputs in `results/mlfq_fastserve/<name>/`: the Phase 1 files (`tasks.csv` with per-task preemptions and chunks,
`requests.csv` with one row per chunk including level and outcome, `timeline.csv`, `kv_memory.csv`,
`gpu_memory.csv`, `meta.json`), plus `evictions.csv` (predicted costs, action, save time) and `joins.csv`
(skip-join level and predicted prefill for every job).
