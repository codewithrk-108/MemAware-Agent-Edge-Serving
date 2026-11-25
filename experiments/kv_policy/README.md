# KV policy at tool calls on FIFO N4 (experiments 1 and 2)

At every tool call, the scheduler decides whether a task's KV cache stays on the GPU, is swapped to host memory,
or is discarded and recomputed when the task returns. The scheduler stays FIFO with at most 4 running requests.

| Run | Tool-call KV policy | Where the returning request queues |
|---|---|---|
| Baseline | none (Phase 1: the server reuses whatever is left in a slot) | back of the FIFO |
| Experiment 1 | stay / swap / discard | back of the FIFO |
| Experiment 2 | stay / swap / discard | ahead of all new tasks; dispatched once its KV need fits |

## Policy ([`policy.py`](policy.py))

Costs come from [`swap_vs_stay_vs_recompute/params.json`](../../swap_vs_stay_vs_recompute/params.json):

- swap out `T_out = 150.2 + 0.1417·P + 0.4185·G` ms and swap in `T_in = 140.6 + 0.0950·P + 0.2185·G` ms, where
  P = resident tokens that were prefilled (contiguous) and G = resident tokens that were generated (scattered)
- recompute `T_rc = −126.2 + 0.5466·L − 0.0534·L·n_dec − 0.0876·L·n_pre + 0.0228·dec_ctx` ms, with L = P + G,
  n_dec = requests decoding at the time, dec_ctx = their context; floored at the no-contention prefill time

For a task holding L cells at a tool call:

1. **Stay** if a spare slot exists, the expected idle time is at most 30 s, and after keeping L the pool still
   has room for the largest of the next 4 queued requests (plus a 512-token margin). Expected idle time is a
   running average of observed tool waits plus the queue ahead × the running average request time ÷ 4. The trace's
   recorded tool delays are never read.
2. Otherwise **swap** if `T_out + T_in < T_rc` and the host swap budget (50K tokens, about 5.5 GiB) has room.
3. Otherwise **discard**.

## Runner ([`runner.py`](runner.py))

- Same workload, arrival times, deadlines and server settings as Phase 1, except the server gets 16 slots
  (`-np 16`) so up to 12 tasks can stay while 4 requests run. The scheduler assigns slots itself.
- Swap uses llama.cpp slot save/restore into `/dev/shm`. A failed restore falls back to a full recompute.
- A slot is erased when its task finishes or fails. If a request needs a slot and none is free, the least
  recently idle staying task is discarded.
- Experiment 2: the head returning request waits until `prompt + output − its resident cells` fits in the pool,
  discarding other tasks' staying caches first if that makes it fit. Nothing behind it is dispatched meanwhile.
  New tasks are dispatched without a space check, as in Phase 1.

## Design flaw: the 30 s stay threshold (found after Study C)

The stay rule's 30 s limit on expected idle time was an arbitrary default, not derived or tuned. Tool waits in this
trace are capped at 10 s (median 0.2 s, p95 4.5 s), so the tool part of the estimate could never reach it; the limit
only ever acted on the estimated queue wait. In practice:

- **Experiment 1:** all 47 rejected stays failed the 30 s rule, always because of queue wait (median expected idle
  90–130 s), never because of tool time.
- **Experiment 2:** the 30 s rule rejected **no** stays; all 15 rejections were for lack of room. The policy was
  effectively "stay whenever the pool has room".

The threshold was chosen on the assumption that staying costs only memory. Study C
([`../idle_kv_microbench/`](../idle_kv_microbench/README.md)) later showed that every 1K cells of occupied range
adds ~0.45 ms to each running request's decode step, so staying has a speed cost. The rule should compare that cost
with eviction, roughly: stay only if `idle time × B × 0.45 ms × ΔR/1000 ÷ TPOT < min(T_out + T_in, T_rc)`. For an 8K
context with 4 requests running that gives about 3–4 s, smaller for larger contexts and more running requests. This
has not been tested in a scheduler yet.

## Assumptions

- **Workload and load:** identical to Phase 1 R2 at smoke scale: 50-task trace, Poisson arrivals with mean 5 s
  (12 tasks/min offered, well above the ~3–4 tasks/min the GPU completes), seed 2, so every run gets the same
  task order and arrival times. This is an overloaded setting: results speak to completion and throughput,
  not to SLO attainment.
- **Same server for all runs** apart from the slot count: Qwen3-0.6B Q8_0, f16 KV, unified 32K-cell pool,
  `--cache-ram 0`, no mmap, prompt reuse on, greedy, `ignore_eos`, tool delays at scale 1, 21,600 s task deadline,
  1,800 s request timeout. The baseline uses `-np 4` as in Phase 1; the policy runs use `-np 16` so staying
  tasks can keep a slot while 4 requests run. Running requests are capped at 4 in every run.
- **Output budgets are known:** a request's KV need is `prompt + output budget`, as in Phase 1's admission
  diagnostics. With `ignore_eos` the budget equals the actual output.
- **Tool durations are not known in advance:** the policy only sees running averages of observed tool waits and
  request times (priors 1.3 s and 7 s).
- **Cost models are reused as-is** from the FIFO N4 measurement on independent requests; their remaining error
  (19–35%) is accepted. Predicted recompute is floored at the no-contention prefill time.
- **Thresholds are untuned defaults:** stay only if expected idle ≤ 30 s; 512-token margin; headroom = largest
  need among the next 4 queued requests; host swap budget 50K tokens.
- **Reserved KV** is counted conservatively: each running request at its full `prompt + output budget` from the
  start, each staying task at its current cells.
- **A failed restore** falls back to a full recompute; a request that hits "Context size has been exceeded"
  fails its task, as in Phase 1.

## Smoke results (50 tasks, Poisson 5 s, seed 2, one run each)

The baseline rerun on this machine reproduces the Phase 1 recorded run (46/50, 3.3 tasks/min, p50 222–225 s).

| | Phase 1 recorded | Baseline rerun | Exp 1 | Exp 2 |
|---|---:|---:|---:|---:|
| Completed | 46/50 | 46/50 | 46/50 | **50/50** |
| KV-context failures | 4 | 4 | 4 | **0** |
| Tasks/min | 3.26 | 3.3 | 3.1 | 3.0 |
| Latency p50 / p95 (s) | 222 / 527 | 225 / 532 | 268 / 640 | 322 / 647 |
| Later-step cache reuse | – | 42.5% | 59.5% | 76.1% |
| Prefill tokens computed | – | 155.8K | 175.1K | 136.2K |
| Mean / peak KV use | – | 49.9% / 99.5% | 50.5% / 94.2% | 57.4% / 95.1% |
| Stay / swap / discard | – | – | 15 / 12 / 35 | 48 / 3 / 12 |

Full table: [`results/kv_policy/compare_smoke.md`](../../results/kv_policy/compare_smoke.md).

- **Experiment 2 completed every task**: no KV-context failures, against 4 in the baseline. It was 8% slower in
  throughput and its median latency was 43% higher. Returning requests wait at the head of the queue for space
  while the 4 workers sit idle (strict FIFO), and staying caches raise mean KV use (57% vs 50%).
- **Experiment 1 did not help.** With the returning request at the back of a long queue, the expected idle time
  is usually over 30 s (47 of 62 stays rejected for that reason), so the policy mostly discards or swaps. That
  frees memory during waits, but the failures come from four large requests running together, which the policy
  does not control.
- **Erasing finished slots has a cost.** The baseline reuses the shared ~1,048-token system prompt at every
  task's first request (median first-token time 56 ms); the policy runs erase it and pay ~0.6 s instead.
- This is one 50-task run per configuration. The completion difference is 4 tasks; run the 500-task workload
  (`run_experiments.sh full`) before drawing conclusions.

## Known issue

Some requests reuse only part of their own cache, even when it stayed (for example 1,514 of 2,440 tokens):
the generated token IDs sent back with the next request diverge from what the server cached. The Phase 1
harness behaves the same way, so the comparison is fair, but every policy run would reuse a little more if
this were fixed.

## Run

```bash
export MODEL=/path/to/Qwen3-0.6B-Q8_0.gguf LLAMA_SERVER=/path/to/instrumented/llama-server
experiments/kv_policy/run_experiments.sh smoke   # ~45 min: baseline, exp 1, exp 2 on 50 tasks
experiments/kv_policy/run_experiments.sh full    # ~6–7 h on the 500-task Phase 1 workload
```

Each run writes the Phase 1 files to `results/kv_policy/<name>/` plus `decisions.csv` (one row per tool call:
P, G, predicted costs, expected idle time, action, measured swap time) and `events.csv` (reclaims, swap and
restore failures).
