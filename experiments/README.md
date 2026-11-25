# Phase 2 experiments: lab log

Everything done after Phase 1, in order, with what was planned, what ran, what failed and why each next step was
chosen. Each study has its own README with full method and numbers; this file is the narrative that ties them
together. Nothing here has been run on the 500-task workload yet: every result is from the 50-task smoke trace and
one run per configuration, unless stated.

## Studies and experiments

| ID | Name | Question | Folder | Status |
|---|---|---|---|---|
| A | Solo task latency | How long does each task take alone? (SLO ground truth) | [`solo_SLO/`](solo_SLO/README.md) | done (50 tasks × 3 reps) |
| B | Swap / recompute cost models | What does moving or rebuilding a request's KV cost under FIFO N4? | [`../swap_vs_stay_vs_recompute/`](../swap_vs_stay_vs_recompute/README.md) | done |
| Exp 1 | KV policy, requeue at back | Does a stay / swap / discard choice at each tool call help FIFO N4? | [`kv_policy/`](kv_policy/README.md) | smoke done: no gain |
| Exp 2 | KV policy, returning requests first | Same, with returning steps dispatched ahead once their KV fits | [`kv_policy/`](kv_policy/README.md) | smoke done: 50/50 completed, slower |
| Exp 3 | FastServe-style MLFQ | Does a skip-join multi-level feedback queue beat FIFO N4? | [`mlfq_fastserve/`](mlfq_fastserve/README.md) | smoke done: worse |
| Exp 4 | Memory-aware admission, max-count backfill, no concurrency cap, fixed output caps | Does admitting as many requests as fit beat FIFO N4? | [`mem_admission/`](mem_admission/README.md) | smoke done: oracle-informed runs (section 15) −28 to −58% median latency; **true fixed-cap runs (section 16): 50/50, 0 overflows, but median latency +14 to +42% and throughput −5 to −10% vs FIFO N4** |
| Exp 5 (E5a, E5b) | Exp 4 fixed cap 2,048 + starvation guard (60 s / 120 s) | Does a starvation guard fix Exp 4's tail? | [`mem_admission/`](mem_admission/README.md) | smoke done: longest request wait 580 → 232 / 242 s, but task p95 695 → 732 / 735 s and throughput −3 to −4%; guard fires for 50–65% of requests under overload |
| LTR-0 | Published Learning-to-Rank predictor (OPT-125M, ShareGPT/Llama-3-8B), zero-shot | How well does it predict / rank our 113 requests' output lengths? | [`ltr_ranking/`](ltr_ranking/README.md) | done: τ 0.24–0.27; with any fitted factor MAE 919–955 vs 978 tokens for a constant. With real earlier outputs (replay, section 20): τ ≤ 0.30, best MAE 925 vs 980 |
| Exp 6 (E6) | Exp 4 cap 2,048 + class × step length ranking (τ ≈ 0.33) | Does a weak but real length ranking help? | [`mem_admission/`](mem_admission/README.md) | smoke done: **p50 257 → 181 s (below FIFO N4's 225), 68% within 10× of solo (best realistic)**, but p95 695 → 747 s, −4% throughput; mbpp first steps pay. **500 tasks (section 24): 500/500 vs 480/500, p50 2,874 → 1,277 s, p95 4,768 → 6,111 s, −8% throughput** |
| Online-length | Online output-length estimation (no future data) | How fast does τ improve as history is gathered? | [`online_length/`](online_length/README.md) | done (offline replay): τ 0.31–0.34 after ~150–200 completions, then flat |
| Exp 7 (E7a, E7b) | Exp 4 cap 2,048 + order by an output-length estimator learned online (median / model), 500 tasks | Does a leak-free, self-learned ranking match or beat the offline lookup (E6)? | [`mem_admission/`](mem_admission/README.md) | done: E7a p50 1,773 s, τ 0.26; E7b p50 1,523 s, τ 0.32 (E6: 1,277 s, 0.33) |
| C | Idle-KV microbenchmark | Do idle cells in the unified pool slow other requests' decoding? | [`idle_kv_microbench/`](idle_kv_microbench/README.md) | done: yes, ~0.45 ms per 1K cells of occupied range |

## Shared setup and assumptions

- **Hardware and runtime:** one NVIDIA T4 (16 GB, PCIe Gen3 ×8 as reported by `nvidia-smi`) on an AWS EC2
  instance; llama.cpp b11780 with the Phase 1 read-only KV-occupancy patch (`runtime/`).
- **Server settings (Phase 1):** Qwen3-0.6B Q8_0, f16 KV, unified pool (`-kvu`) of 32,768 cells, `--cache-ram 0`,
  no mmap, prompt reuse on, default batching (`-b 2048 -ub 512`, continuous batching on). Phase 1 baselines use
  `-np 4`; every Phase 2 scheduler uses `-np 16` so idle tasks can keep a slot while 4 requests run.
- **Workload:** `data/traces/agenttrace_50_seed2.jsonl` (50 tasks, 35 MBPP / 15 NL2Bash, 113 requests, 63 tool
  waits). Load tests use Poisson arrivals with mean 5 s and seed 2, so every scheduler sees the same task order and
  arrival times as the Phase 1 R2-style smoke run. This offers 12 tasks/min against ~3 tasks/min of capacity: it is
  an overloaded setting, so it measures completion and throughput, not SLO attainment.
- **Replay, not execution:** greedy decoding with `ignore_eos`, each request generates exactly its recorded output
  budget, tool waits are sleeps of the recorded duration (150× scaled, ≤ 10 s). Tools are never run.
- **KV size:** 112 KiB per token (measured: 114,688 bytes per token moved by slot save).
- **"Mean KV use" / "occupancy"** in every table = the mean, over 1-second samples for the whole run (from just
  before the first arrival to the end, including the final drain), of the runtime's `kv_cells_used / 32,768`
  (`kv_memory.csv`, `harness/kv_memory.py`). It counts **all occupied cells**: running requests plus idle caches left
  in slots; shared cells count once. It is a count, not the occupied range that Study C shows sets decode speed.
- **Swap path:** llama.cpp slot save/restore into RAM-backed `/dev/shm`, the only GPU↔host path this runtime offers.
- **Tasks recorded from Qwen3-1.7B are kept.** The server always runs Qwen3-0.6B, and the two models share a
  tokenizer, so those trajectories replay identically.

## Chronological log

### 1. Framing (2026-11-13)

- **Dataset review.** Re-derived the Phase 1 dataset statistics from the trace files (task, request and token counts
  match `data/README.md`). Clarified two terms used later: a *step* is one model request in a task's agent loop
  (k steps = k requests and k − 1 tool waits), and a *distinct problem* is one benchmark question; AgentTrace has
  runs from two models per problem, and the 500-task sample keeps one run per problem.
- **Baseline choice.** The Phase 1 report names FIFO N4 (concurrency 4, 32K pool) as the baseline to beat
  (94.4–96.0% completion, 4.21–4.30 tasks/min on 500 tasks) and N1 as the zero-failure reference.
- **SLO proposal and critique.** The proposal was a per-step-count SLO (α × category time), minimizing the average
  difference to it, gated on ≥ 95% completion. Reasoning against parts of it, from data:
  - solo latency varies 2.5–15× within a step-count category, and step count explains only 47% of its variance
    while output tokens plus tool time explain 98.8%, so the SLO should be per task (α × that task's solo latency);
  - a signed average difference lets early finishes cancel misses; use SLO attainment (failed tasks count as
    misses), normalized lateness, slowdown and goodput instead;
  - N4's own completion sits at the 95% threshold, so the gate needs repeated runs or a relative threshold.
- **Overload argument.** The user objected that queueing is the scheduler's fault. The counter-argument, with
  numbers: the 500 tasks need 1,591,939 output tokens; the best sustained rate seen on this T4 is ~230 tokens/s, so
  any schedule needs ≥ 115 min, while Poisson 5 s delivers all tasks in ~42 min. Even shortest-job-first at full
  speed under burst arrival finishes the 250th task at ~24 min. Estimated slowdowns of the Phase 1 runs against a
  fitted solo-latency model were 133–214× at the median. Conclusion: at these rates, latency is mostly backlog;
  SLO comparisons need sub-capacity loads (~2.5–3.8 tasks/min). This shaped every later caveat.
- **Runtime facts checked.** Prompt caching is on everywhere; in Phase 1, first requests reused ~92% of their
  prompt (the shared ~1,048-token system prompt left in a slot) but later steps reused only 25–28%, because under
  load the slot was overwritten during the tool wait. llama.cpp's default batching splits prompts into 2,048-token
  logical batches and 512-token micro-batches; attention over chunked prefill is exact, and decode tokens of other
  requests share each step.

### 2. Study A: solo task latency (2026-11-13)

- **Plan changes.** First request: all 500 tasks × ~10 reps. Estimated from R1 at ~3.3 h per pass, i.e. ~66 h for
  two KV modes, so the user cut it to 3 reps, then to a smoke test on the 50-task set only.
- **Misstep 1:** the user asked to use only Qwen3-0.6B. I read this as dropping tasks recorded from Qwen3-1.7B,
  stopped the first launch, and found the 50-task set has only 28 such tasks; I switched to the first 50 0.6B tasks of
  the 500-task file. The user clarified that the server already runs only 0.6B and both models share a tokenizer, so
  the filter was unnecessary. The filtered launch had been killed by an interrupted tool call before finishing any
  task; I removed its output and restarted on the original 50-task set. The filter remains as `--source-model`
  (default `all`).
- **Result:** 300/300 task-runs completed. Median 15.7 s, p95 116.7 s; per-task variation ~0.2% between reps.
  Evicting KV during tool waits costs a median +2.3% per multi-step task when alone (max +12%).
- **Caveat found later:** every task-run starts from an erased pool, so the shared system prompt is recomputed each
  time; Phase 1 usually reused it. Solo latency is slightly pessimistic (a few hundred ms per task).

### 3. Study B: swap and recompute cost models (2026-11-17)

- **Goal (user):** fit swap time `a·L + b` (GPU→CPU and CPU→GPU) and recompute time `c + d·L + e·L²` for a
  stay / swap / discard scheduler, using the 50-task set.
- **Misstep 2:** I first wrote a measurement of isolated requests (one at a time). The user interrupted: the models
  must be fitted under FIFO N4 load, treating the 113 requests as independent jobs, with no tool waits. The
  interrupted run had already written slot files to `/dev/shm` (one of 918 MB), which confirmed that slot save works;
  I deleted them.
- **Crash:** the first full run died before measuring anything: the warm-up pass used repetition −1, and NumPy
  rejects a negative seed. Fixed (warm-up uses 999) and rerun (~60 min).
- **Clarified definition:** swap L = tokens in the slot (prompt + generated − 1, the last sampled token is never
  cached); recompute L = prompt tokens prefilled.
- **Result (L-only forms):** swap out `0.1856·L + 351.6` ms, swap in `0.1146·L + 230.5` ms, recompute
  `−34.5 + 0.4840·L − 1.054e−5·L²` ms; R² 0.53–0.80, mean error 29–41%. 36 of 339 requests (11%) failed on
  context size and were excluded. 301 of 303 restores were fully reused.
- **Surprises:** recompute under N4 is 4–5× slower per token than alone (0.41 vs 0.09 ms/token) and concave
  (`e < 0`); swap through llama.cpp reaches only 0.6 / 1.0 GB/s against ~7.9 GB/s of PCIe.
- **User: "the mean error is quite large."** Diagnosis: the same request varies 16–25% between repetitions, so
  L alone cannot do better. Testing features showed:
  - splitting prompt (prefilled, contiguous) and generated (scattered) tokens explains swap out: 0.42 vs 0.14 ms
    per token, R² 0.60 → 0.83, mean error 33% → 19%; contention adds nothing for swap;
  - swap in improves less (41% → 35%), probably because free-cell fragmentation at restore time matters (not logged);
  - contention features (requests decoding/prefilling during the prefill, their context) bring recompute from 31%
    to 26%.
  These "split length" and "contention" models are what Exp 1–3 use.

### 4. Exp 1 and Exp 2: KV policy on FIFO N4 (2026-11-17)

- **Design.** At each tool call: stay if a slot is spare, expected idle time ≤ 30 s and the pool keeps room for the
  next queued requests; otherwise swap if predicted cheaper than recompute, else discard. Exp 1 requeues returning
  steps at the back (as Phase 1); Exp 2 puts them ahead of new tasks and dispatches them once their KV fits.
- **Checks before the smoke run:** a forced-swap run and a policy run on 6 tasks; restores were reused, stays kept
  their slot. Some requests reused only part of their own cache (e.g. 1,514 of 2,440 tokens) even after staying, so
  it is not a restore bug; the Phase 1 harness has the same behavior.
- **Small failures:** the comparison table needed `tabulate` (not installed; replaced with a plain writer); the
  Phase 1 reference path for the smoke run was wrong (`part_a` instead of `part_b`; fixed).
- **Result (50 tasks):** the baseline rerun reproduced the Phase 1 recorded run (46/50, 3.3 tasks/min, p50 225 vs
  222 s). Exp 1: 46/50, 3.1 tasks/min, p50 268 s: no gain, because with the returning step at the back of a long
  queue the expected idle time exceeds 30 s (all 47 rejected stays), and the failures come from four large requests
  running together. Exp 2: **50/50**, 0 failures, but 3.0 tasks/min (−8%) and p50 322 s (+43%): returning steps wait
  at the head for space while workers idle.
- **Side cost found:** policy runs erase a slot when its task ends, so new tasks lose the shared system prompt
  (first-token time 56 ms → ~600 ms).
- **Instance shutdown request.** The user asked to stop the EC2 instance after the runs. I could not verify that its
  shutdown behavior is Stop rather than Terminate (no AWS credentials on the box), and terminate would delete all
  uncommitted results, so I asked; the user chose to stop it themselves.

### 5. Token round-trip check (2026-11-17)

- A full request returned 1,336 token IDs for 1,337 generated. The "missing" token is the last sampled token, which
  llama.cpp never writes to the cache; the returned IDs match the cached sequence exactly, so resuming with
  `history + returned tokens` is correct.
- Two identical greedy requests diverged after 34 tokens (GPU numerical nondeterminism). With fixed output lengths
  this does not affect timing.
- Still unexplained: some resumes reuse less than the full cache (e.g. 2,335 of 2,423 tokens). Logged per request;
  it affects all schedulers alike.

### 6. Exp 3: FastServe-style MLFQ (2026-11-18)

- **Design.** Skip-join MLFQ with quanta 256 → 4,096 tokens plus an unlimited level; jobs run in 256-token chunks
  (llama.cpp cannot be preempted from outside, so preemption happens between chunks); idle KV stays in its slot and
  is evicted (swap or discard by the cost models) only when a dispatch doesn't fit. Kept in its own folder at the
  user's request.
- **Concern raised mid-run:** "taking more time than previous runs means the approach is bad?" Answer: run length
  is bounded by total work ÷ throughput; MLFQ is judged on median latency and slowdown, and it is bad only if
  throughput drops or median latency does not improve. (At that moment the run had been going for 1.5 min.)
- **Live observation during the run:** KV use 89–91% and ~23 ms per token, against 16 ms in the baseline.
- **Result (50 tasks):** worse on every headline metric: 46/50, 2.19 tasks/min (−35%), p50 408 s, p95 1,012 s,
  lower SLO attainment at every α. Diagnosis: (1) keeping every idle cache raised mean KV use to 80%, and time per
  token rose with it (16.3 → 23.0 ms); (2) 62 discards plus partial reuse recomputed 230K prompt tokens; (3) per-
  request priority resets at each tool call, so one-step tasks with long answers waited longest (median 482 vs
  134 s); (4) 4 context failures remained even with the fit check (possibly fragmentation, not verified).

### 7. Brainstorm and Study C: idle-KV microbenchmark (2026-11-18)

- **Observation behind it:** across the baseline, Exp 2 and Exp 3, time per token rose with mean KV use
  (50% → 16.3 ms, 57% → 19.8 ms, 80% → 23.0 ms). If idle cells slow other requests' attention, idle-KV
  management is about speed as well as memory, which changes how a scheduler should treat stay vs evict.
- **Ideas ranked** (full list in the chat brainstorm, kept here for the paper): memory-aware admission with a higher
  concurrency cap and backfill; capping idle KV; retry instead of fail on context errors; keeping the system-prompt
  prefix warm; task-level shortest-remaining-work-first with aging; smaller prefill chunks; sub-capacity loads.
  Study C runs first because its answer decides whether capping idle KV is worth anything.
- **Run:** 36 configurations (order × 1 or 4 active requests × 1K or 4K context × 0–24K idle cells) × 3 reps,
  108 measurements, 0 errors, ~30 min. Early numbers already showed a 3× slowdown; a follow-up (15 measurements)
  was added to separate the number of occupied cells from how far they reach into the pool.
- **Result:** decode time per token rises linearly by **~0.45 ms per 1K occupied cells** (R² 1.000 for one request,
  0.998 for four), idle or not: 24K idle cells make one request 3.0× slower and cut 4-request throughput by 57%.
  The cost follows the **highest occupied cell, not the count**: 5,120 cells cost 7.4 ms/token when low in the
  pool and 16.4 ms (same as a full pool) when one idle cache sits at the top.
- **Confound found and documented:** with 4 active requests in the idle-first order, their own prefills fall inside
  the timed window and inflate per-token time (15.3 → 21.0 ms for 4 × 4K); the active-first order is the clean one.
- **Consequences:** this explains most of experiment 3's slowdown; "stay" has a speed cost (derived break-even:
  staying with 8K cells while 4 requests run pays off only for idle periods under ~3–4 s); evicting caches from the
  middle of the pool does not help, only shrinking the top of the occupied range does; the 30 s stay threshold in
  experiments 1–2 was far too permissive. Details: [`idle_kv_microbench/README.md`](idle_kv_microbench/README.md).

### 8. Review of the 30 s stay threshold (2026-11-18)

- **User question:** why 30 s, when tool delays are never that long, so everything would stay?
- **Answer and admission:** 30 s was an arbitrary default, not derived or tuned. Tool waits are capped at 10 s
  (median 0.2 s), so the tool part of the expected idle time never came near it; the limit acted only on the queue
  wait estimate. Experiment 1's 47 rejected stays were all due to queue wait; experiment 2's 30 s rule rejected no
  stays at all (all 15 rejections were for lack of room), so experiment 2 was effectively "stay whenever there is
  room". The threshold assumed staying costs only memory; Study C showed it also costs speed.
- **Replacement (not yet tested):** stay only if the slowdown imposed on running requests over the idle period is
  smaller than the cheaper eviction, roughly `idle × B × 0.45 ms × ΔR/1000 ÷ TPOT < min(T_swap, T_rc)`; about 3–4 s
  for an 8K context with 4 running. Recorded in [`kv_policy/README.md`](kv_policy/README.md#design-flaw-the-30-s-stay-threshold-found-after-study-c).

### 9. Solo runs vs the FIFO N4 baseline (2026-11-18)

- **User question:** how did the standalone (solo) runs compare with FIFO N4?
- **Data:** solo = Study A, retain mode, mean of 3 reps (`results/solo_SLO/smoke50_v1/retain/`); FIFO N4 =
  baseline smoke rerun (`results/kv_policy/smoke_baseline_fifo_n4/`), same 50 tasks, Poisson mean 5 s, seed 2,
  `-np 4`. The comparison was computed ad hoc from those CSVs (per-task latency joined on `task_id`, per-request
  service time joined on `task_id` + `step`).

| | Solo | FIFO N4 |
|---|---:|---:|
| Completed | 50/50 | 46/50 |
| Task latency p50 / p95 / mean (s) | 15.7 / 101.5 / 27.1 | 224.8 / 532.3 / 258.2 |
| Request time p50 / p95 (s) | 7.27 / 33.8 | 17.5 / 79.1 |
| First token p50 / p95 (ms) | 36 / 139 | 141 / 2,695 |
| Time per output token p50 / p95 (ms) | 6.38 / 10.1 | 16.1 / 26.9 |
| Later-step cache reuse | 97% | 43% |

- **Slowdown per task** (FIFO ÷ solo, completed tasks): p50 13.4×, p95 42.1×, range 1.2–44×; similar across step
  counts (1 step 11.7×: 11.0 → 134.0 s; 2 steps 14.3×: 14.4 → 283.3 s; 3+ steps 12.2×: 36.5 → 502.1 s).
- **Where FIFO latency goes:** 79% ready-queue wait, 20% model time, 0.5% tool waits. Most of the gap is backlog,
  because 12 tasks/min are offered against ~3.3 completed.
- **Requests are also slower once running:** per-request service time is 2.65× solo at the median (4.2× at p95),
  mostly from slower decoding (16.1 vs 6.4 ms per token, explained by the occupied range of the shared pool in
  Study C) and lost cache reuse (43% vs 97%).
- **SLO attainment** (all 50 tasks, failures count as misses): within 2× solo 4%, 5× 12%, 10× 30%, 20× 68%.
- **The 4 tasks FIFO N4 lost** are multi-step tasks (3–9 steps) that failed at step 2–4 with "Context size has
  been exceeded": `agenttrace-mbpp-467-qwen3-1.7b` (6 steps, failed after 4), `agenttrace-mbpp-18-qwen3-1.7b`
  (4, after 3), `agenttrace-nl2bash-137-qwen3-1.7b` (9, after 3), `agenttrace-mbpp-55-qwen3-1.7b` (3, after 2).

### 10. Active vs idle KV in the occupancy figures (2026-11-18)

- **User question:** is "mean KV" the KV of active requests, or all filled KV averaged over time? Answer: all
  occupied cells averaged over the run (definition under *Shared setup*). To separate the two, the KV of running
  requests was estimated from `requests.csv`: for every request, prompt length + output generated so far (output
  assumed to grow linearly from first token to end), summed over requests running at each `kv_memory.csv` sample.
  Approximate, not a runtime counter.

| Run | Mean KV, all occupied cells | Active requests' KV (estimate) | Idle share of occupied cells |
|---|---:|---:|---:|
| Baseline FIFO N4 | 49.9% | 49.6% | 0.5% |
| Exp 1 | 50.5% | 47.4% | 6.1% |
| Exp 2 | 57.4% | 51.5% | 10.4% |
| Exp 3 MLFQ | 79.6% | 47.1% | 40.9% |

- Restricting to samples with at least one request running changes every figure by less than 0.3 points.
- **Active KV is about the same in all runs (47–52%)**; the differences in mean occupancy come from idle caches.
- **The baseline holds almost no idle KV:** under overload its 4 slots are reused at once, so leftovers are
  overwritten; the flip side is 43% later-step cache reuse.
- **Exp 3 held 41% of occupied cells idle** for the same active work, which is what slowed its decoding
  (23.0 vs 16.3 ms per token). Direct support for Study C at run level.
- Computed ad hoc from `results/kv_policy/smoke_*/{kv_memory,requests}.csv` and
  `results/mlfq_fastserve/smoke_exp3_mlfq/{kv_memory,requests}.csv`.

### 11. How llama.cpp places and evicts KV: read from the source (2026-11-18)

- **User question:** with 16K of the pool full and 4 new requests arriving, do they fill empty cells or evict?
- **Answered from the b11780 source** (`~/t4runtime/llama-b11780-kv-src`, the binary used for every run):
  1. *Slot choice* (`tools/server/server-context.cpp`, `get_available_slot`, `get_free_slots`): a pinned `id_slot`
     is used as given (all Phase 2 schedulers). Otherwise the idle slot whose cached prompt shares > 10% with the new
     prompt (`slot_prompt_similarity = 0.1` in `common/common.h`), else an empty slot, else the least recently used
     idle slot. Reusing a slot drops its old cache beyond the common prefix; this is the only eviction while space
     remains, and it touches only that slot.
  2. *Cell placement* (`src/llama-kv-cache.cpp`, `find_slot(ubatch, cont=false)`): tokens take free cells one by
     one, not necessarily contiguous, searching from a moving head that jumps back to cell 0 when enough cells below
     it are free. New requests fill free cells and holes; nothing is evicted while free cells exist.
  3. *Out of space* (`process` decode path, `try_clear_idle_slots`): with `-kvu`, the server purges one idle slot's
     whole cache (first in slot-number order, not LRU) and retries; with nothing to purge it halves the batch and
     retries; at batch size 1 it raises "Context size has been exceeded" and **fails every request being processed**,
     clearing their caches.
  4. *Attention range* (`get_n_kv`): attention runs over `pad(used_max_p1)`, every cell up to the highest occupied
     one. This **confirms Study C's range result in the source**.
- **Checked against the runs' server logs:**

| Run | Idle caches purged by the server | "No free space, retry" warnings | Overflow events (requests failed) |
|---|---:|---:|---:|
| Baseline FIFO N4 | 0 | 52 | 1 (4, all at 744.3 s) |
| Exp 1 | 0 | 40 | 1 (4, all at 756.5–756.6 s) |
| Exp 2 | 0 | 0 | 0 |
| Exp 3 MLFQ | 19 | 40 | 1 (4, all at 1,062.1 s) |

- **Corrections to earlier statements:**
  - Every "4 failures" result is **one overflow event** that failed all 4 running requests at once, not four
    separate failures.
  - Exp 3's failures were attributed to possible fragmentation. That is ruled out (cells need not be contiguous).
    The cause is that **the server purged 19 idle caches on its own**, which the scheduler still counted as
    resident; its accounting drifted until an overflow. The purges also added to Exp 3's recomputation.
  - Study C's mechanism, previously "inferred, not verified in the source", is now verified.
- **Consequences for the next scheduler:** read the server's purges (log or `/slots`) or keep the pool far enough
  from full that they never trigger; one overflow costs every running request, so an admission margin is worth
  more than it looks.

### 12. Output budget used for KV reservation (2026-11-19)

- **User question (while designing Exp 4, memory-aware admission):** what output budget is assumed per request?
- **Answer:** the trace's recorded output length for that step (`n_gen`), i.e. the number of tokens the source model
  actually generated. The replay sends exactly that as `n_predict` with `ignore_eos`, so the budget **equals the true
  output length and is known in advance: an oracle**. Sizes on the 50-task set: median 995, p95 4,641 tokens per
  request (up to 14,875 on the 500-task set). Phase 1's admission diagnostics and the fit checks in Exp 2 and Exp 3
  used the same `prompt + n_gen`.
- **Implication:** with the oracle, worst-case reservation wastes nothing at the end of a request, so admission
  results are an **upper bound**. A real server only knows the client's `max_tokens` (often 4K–16K) or the
  remaining context, and would admit far fewer requests.
- **Options recorded:** A. oracle (`n_gen`); B. fixed cap, e.g. `min(4,096, 16,384 − prompt)`; C. predicted budget
  (e.g. p90 by task class and step) with an overrun fallback (eviction or preemption). Proposed order: A first as the
  ceiling, then B or C to see how much of the gain survives.

### 13. Choosing fixed output caps from the 50-task set (2026-11-19)

- **User request:** propose fixed per-request output caps (option B) based on the 50-task set.
- **Output length per request** (113 requests): p25 561, p50 995, p75 2,095, p90 3,852, p95 4,640, max 7,471.
  MBPP is longer than NL2Bash (p50 1,498 vs 711; max 7,471 vs 3,867); first steps are longer than later steps
  (p50 1,698 vs ~890). Prompt length at request time: p50 1,826, p90 7,997, max 14,794.
- **Trade-off for a cap c** (`need = prompt + c`; "fit" = 32,256 ÷ mean need):

| Cap | Requests exceeding it | Output beyond the cap | Mean need | ≈ concurrent fit |
|---:|---:|---:|---:|---:|
| 512 | 78.8% | 69.2% | 3,940 | 8.2 |
| 1,024 | 48.7% | 48.5% | 4,452 | 7.2 |
| 1,536 | 37.2% | 35.2% | 4,964 | 6.5 |
| 2,048 | 26.5% | 24.7% | 5,476 | 5.9 |
| 3,072 | 13.3% | 12.4% | 6,500 | 5.0 |
| 4,096 | 8.8% | 5.0% | 7,524 | 4.3 |
| 6,144 | 0.9% | 0.7% | 9,572 | 3.4 |
| 8,192 | 0% | 0% | 11,620 | 2.8 |
| oracle (true `n_gen`) | – | – | 5,018 | 6.4 |

- **Finding:** a cap that no request exceeds (8,192) fits only ~2.8 requests at a time, fewer than FIFO N4's 4; N4
  reaches 4 only by reserving nothing, which is why it overflows. A fixed cap therefore needs an overrun rule.
- **Proposed overrun rule (continuation):** generate up to the cap; if unfinished, request another cap's worth of
  reservation; continue if it fits, else wait with the cache kept and counted; continuations get first claim on
  freed space; if waiting continuations fill the pool, discard the one holding the least cache (recompute later).
  The workload is never truncated, so results stay comparable.
- **Proposed runs:** caps 1,024 (≈ p50), 2,048 (≈ p75), 4,096 (≈ p90–95), optionally 8,192 (never continues);
  recommended first: 2,048 and 4,096.
- **Caveat:** the percentiles come from the same 50 tasks the runs use (in-sample); the 500-task set would test them
  out of sample.

### 14. Exp 4: memory-aware admission with fixed output caps (2026-11-19)

- **Request (user):** memory-aware scheduling with backfill and no concurrency limit, fitting the maximum number of
  waiting jobs into the KV pool; algorithm to be approved before running.
- **Design iterations before approval:**
  1. First proposal: oracle budget (`n_gen`), smallest-need-first packing, discard at tool calls, returning steps at
     the back, 120 s starvation guard. The user removed the starvation guard and accepted the rest.
  2. The user then asked what the output budget was. It was the oracle (section 12); the user asked for fixed caps
     instead (option B). Cap analysis (section 13) showed a safe cap fits fewer requests than N4, so a continuation
     rule was added for requests that outrun their cap. The user chose caps 1,024 / 2,048 / 4,096. Continuation was
     my proposed default; the user did not pick between continuation and truncation explicitly.
- **Implementation bugs before the runs:** a logging field double-counted reservations (fixed before running);
  the first 6-task check crashed with `TypeError: unhashable type: 'Job'` (dataclass equality; fixed with
  `eq=False`). The rerun check passed: continuations reused their cache, peak actual KV 19,608 ≤ reserved 19,800.
- **Assumption (strict blocking, no backfill), made explicit 2026-11-19 after the user asked about it:** while any
  continuation is waiting, no new request is admitted, even one small enough for the leftover space. Example:
  3,000 cells free, two continuations each need 2,048: the first starts, 952 cells remain, the second waits, and a
  new request needing 800 is also held back; the 952 cells stay empty until enough is freed for the second
  continuation. Reason: the waiting continuation holds its cache, and letting small new requests take freed cells
  could keep it waiting indefinitely while holding memory (deadlock risk). Cost: up to `cap − 1` idle cells per
  wait. This applies to all Exp 4 runs and carries over to E5. Backfilling new requests into that space is an
  untested variant. Details: [`mem_admission/README.md`](mem_admission/README.md) (Assumptions).
- **Runs:** three 50-task runs executed one after another (~16 min each), no failures, no deadlock discards.
- **Results:**
  - **50/50 completed at every cap, 0 overflow events.** Actual KV never exceeded the reservation at any of the
    884–892 one-second samples per run.
  - **Median latency 162 / 131 / 95 s** (caps 1,024 / 2,048 / 4,096) vs 225 s for FIFO N4 (−28 / −42 / −58%);
    mean 254 / 239 / 214 s vs 258 s. Median slowdown vs solo 8.9 / 7.7 / 6.8× vs 13.4×; p95 18.3 / 15.2 / 12.7×
    vs 42.1×. Within 10× of solo: 64 / 74 / 84% vs 30%; within 20×: 94 / 96 / 100% vs 68%.
  - **Throughput did not rise:** 3.10 / 3.15 / 3.16 tasks/min vs 3.34 (output 186–189 vs 202 tokens/s; the baseline
    also skipped the output of its 4 failed tasks). Mean running requests rose from 3.7 to 5.1–5.7, but time per
    token rose from 16.3 to 26.6–30.2 ms (fuller pool, Study C), and discarding at tool calls made prefill 2.5× larger
    (390K vs 156K prompt tokens), lifting a step's first-token time to 1.2–1.7 s.
  - **Tail got worse:** p95 latency 678–729 s vs 532 s; longest wait 548–595 s. Without a starvation guard, large
    requests (often long histories re-prefilled after a discard) wait while smaller ones are admitted. Tasks with 3+
    steps: median 552–591 s vs 502 s. One-step tasks: 61–68 s vs 134 s.
  - **Larger caps did better on every latency measure:** continuations fell from 114 to 10, and a waiting
    continuation blocks all new admissions.
- **Correction (section 15):** these runs reserved `history + min(cap, true remaining output)`, not the approved
  `history + cap`, so requests shorter than the cap had oracle reservations.
- **First Phase 2 scheduler to beat FIFO N4 on completion and typical latency together**; it still loses on
  throughput and p95. Next candidates: keep the cache through short tool waits (cost-based stay) to remove the
  extra prefill; a starvation guard for the tail; cap 8,192; sub-capacity load and the 500-task set.
  Details: [`mem_admission/README.md`](mem_admission/README.md).

### 15. Exp 4 cap 8,192, and a deviation from the approved design (2026-11-19)

- **Request (user):** also run cap 8,192. Added to `run_caps.sh` and `compare.py`; only the new cap ran (~16 min).
- **Result:** 50/50 completed, 0 overflow events, 0 continuations, actual KV within the reservation at all 886
  samples; 3.19 tasks/min, mean latency 205 s (best of all runs), p50 102 s, p95 667 s, slowdown p50 / p95
  6.7× / 12.1×, within 10× of solo 86%, within 20× 100%.
- **Deviation found while checking this run:** the code reserves `history + min(cap, true remaining output)`
  (`chunk_len` in `experiments/mem_admission/scheduler.py`), whereas the approved design (and my description of it:
  "the true remaining output is used only to know when a step is finished") reserves `history + cap`. Consequences:
  - every request whose remaining output was below the cap reserved its exact true need, i.e. the **oracle**;
  - **cap 8,192 is exactly the oracle budget (option A of section 12)**, since every output in this set is ≤ 7,471;
  - the "larger caps do better" trend (continuations 114 → 41 → 10 → 0) reflects fewer stalls from continuations,
    not the cost of reserving more;
  - the cap analysis of section 13 (`need = prompt + cap`; ~2.8 requests fit at cap 8,192) was never tested.
- **Status:** all four Exp 4 runs are kept and relabelled as "reservation = min(cap, true remaining output)". True
  fixed-cap admission (reserve `history + cap`) has not been run; it needs a one-line change and a rerun of the four
  caps (~65 min), pending the user's decision.

### 16. Exp 4 rerun with true fixed-cap reservation (2026-11-19)

- **Instruction (user):** do not assume or use the output length; admission must only check whether
  `prompt length + cap` fits in the KV pool. Rerun if the implementation did otherwise (it did; section 15).
- **Fix:** `scheduler.py` now reserves `history + cap` for every new request and `held cells + cap` for every
  continuation (`reserve_len`; `--reserve cap`, the default). The true remaining output is used only as the
  generation length of a chunk, to stop where the recorded step ended, standing in for end-of-sequence; it affects no
  admission, ordering or reservation. `--reserve min_true` reproduces the first runs.
- **Check:** 6 tasks, cap 1,024: every new request reserved exactly `prompt + 1,024` (e.g. 569 tokens generated,
  2,106 cells reserved); continuations reserved held cells + 1,024; 6/6 completed.
- **Runs:** caps 1,024 / 2,048 / 4,096 / 8,192 as `results/mem_admission/smoke_exp4fc_cap*` via
  `experiments/mem_admission/run_fixed_caps.sh`; log `results/mem_admission_fixed_caps.log`. The first runs
  (`smoke_exp4_cap*`) are kept as the oracle-informed reference.
- **Run outcome:** all four completed one after another (~16 min each), no errors, no deadlock discards.
- **Results** (50 tasks, Poisson mean 5 s, seed 2; one run each):

| | Baseline FIFO N4 | Fixed cap 1,024 | Fixed cap 2,048 | Fixed cap 4,096 | Fixed cap 8,192 | Oracle (cap 8,192, section 15) |
|---|---:|---:|---:|---:|---:|---:|
| Completed | 46/50 | 50/50 | 50/50 | 50/50 | 50/50 | 50/50 |
| Overflow events | 1 | 0 | 0 | 0 | 0 | 0 |
| Tasks/min | **3.34** | 3.00 | 3.18 | 3.16 | 3.03 | 3.19 |
| Output tokens/s | 202 | 180 | 190 | 189 | 181 | 191 |
| Latency mean / p50 / p95 (s) | **258 / 225 / 532** | 300 / 283 / 752 | 286 / 257 / 695 | 293 / 268 / 709 | 335 / 319 / 718 | 205 / 102 / 667 |
| Slowdown vs solo p50 / p95 | 13.4× / 42.1× | 11.9× / 23.7× | 11.3× / 31.6× | 12.5× / 35.2× | 15.6× / 47.6× | 6.7× / 12.1× |
| Within 5× / 10× / 20× of solo | 12 / 30 / 68% | 10 / 42 / 84% | 12 / 48 / 80% | 16 / 42 / 72% | 12 / 34 / 66% | 24 / 86 / 100% |
| Latency p50 by steps 1 / 2 / 3+ (s) | 134 / 283 / 502 | 96 / 286 / 642 | 96 / 301 / 591 | 73 / 309 / 594 | 92 / 351 / 627 | 67 / 78 / 544 |
| Mean / peak running | 3.7 / 4 | 5.3 / 13 | 4.8 / 10 | 3.6 / 6 | 2.2 / 3 | 5.0 / 10 |
| Mean KV reserved / used | – / 50% | 87 / 76% | 86 / 66% | 86 / 52% | 77 / 31% | – / 65% |
| Time per output token p50 (ms) | 16.3 | 29.2 | 24.2 | 18.6 | 11.3 | 26.0 |
| Continuations | – | 114 | 41 | 10 | 0 | 0 |
| Dispatches blocked by a waiting continuation | – | 27% | 15% | 6% | 0% | – |
| KV samples above reservation | – | 0 / 931 | 0 / 884 | 0 / 894 | 0 / 966 | 0 / 886 |

- **Findings:**
  - Memory-aware admission stays **safe without the oracle**: 50/50 completed and zero overflows at every cap;
    actual KV never exceeded the reservation.
  - **The latency gains of section 14 came from the oracle.** With true fixed caps, median latency is worse than
    FIFO N4 at every cap (257–319 s vs 225 s), as are mean (286–335 vs 258 s) and p95 (695–752 vs 532 s);
    throughput is 5–10% lower (3.00–3.18 vs 3.34 tasks/min).
  - Relative measures still favour admission at caps 1,024–4,096 because FIFO N4's 4 failures count as misses and
    short tasks go first: p95 slowdown 23.7–35.2× vs 42.1×, within 10× of solo 42–48% vs 30%, within 20× 72–84% vs
    68%; one-step tasks 73–96 s vs 134 s. Two-step and 3+-step tasks are slower than under FIFO N4.
  - **Cause: worst-case reservation wastes the pool.** At caps 1,024–4,096, 86% of the pool is reserved on average
    while 52–76% is used; at cap 8,192, 77% reserved vs 31% used, at most 3 requests at once. Small caps pack more
    requests but spawn continuations (114 at cap 1,024) that block new admissions (27% of dispatches) and slow
    decoding (29.2 ms per token). Discarding at tool calls keeps prefill ~2.5× the baseline (387–393K vs 156K).
  - **Best fixed cap: 2,048** (≈ p75 of output length): highest throughput among fixed caps (3.18) and best median
    (257 s), still behind FIFO N4 on raw latency and throughput.
- Details: [`mem_admission/README.md`](mem_admission/README.md#corrected-runs-true-fixed-cap-reservation-historycap).

### 17. Request throughput and concurrency per run (2026-11-19)

- **User question:** what is the average requests per second for each run?
- **Computed** from each run's `meta.json` (`span_s`: first arrival to last finish), `tasks.csv` (`steps_done`) or
  `requests.csv`, and `timeline.csv` (time-weighted mean of running requests). A *model request* is one agent step;
  chunked schedulers (Exp 3, Exp 4) make several HTTP calls per step, reported separately.

| Run | Model requests completed | Requests/s (per min) | HTTP calls/s | Mean running | Run length |
|---|---:|---:|---:|---:|---:|
| Baseline FIFO N4 | 103 / 113 | 0.125 (7.5) | 0.130 | 3.66 | 13.8 min |
| Exp 1 | 108 | 0.120 (7.2) | 0.125 | 3.59 | 14.9 min |
| Exp 2 | 113 | 0.114 (6.9) | 0.114 | 3.65 | 16.5 min |
| Exp 3 MLFQ | 106 | 0.084 (5.0) | 0.563 | 3.63 | 21.0 min |
| Exp 4 fixed cap 1,024 | 113 | 0.113 (6.8) | 0.227 | 5.32 | 16.7 min |
| Exp 4 fixed cap 2,048 | 113 | 0.120 (7.2) | 0.163 | 4.75 | 15.7 min |
| Exp 4 fixed cap 4,096 | 113 | 0.119 (7.2) | 0.130 | 3.62 | 15.8 min |
| Exp 4 fixed cap 8,192 | 113 | 0.114 (6.8) | 0.114 | 2.20 | 16.5 min |
| Exp 4 oracle (cap 8,192) | 113 | 0.120 (7.2) | 0.120 | 4.97 | 15.7 min |

- Every run completes 0.11–0.13 model requests per second except Exp 3 (0.084). Requests are long: ~1,590 output
  tokens on average at ~180–200 output tokens/s shared by everything running.
- The baseline's 0.125 is slightly flattered: it completed only 103 of 113 requests (its 4 failed tasks never ran
  their remaining steps) and its run ended sooner.
- More concurrency did not buy more requests per second: fixed cap 1,024 ran 5.3 requests at once vs 3.7 but each
  token took 29 vs 16 ms; fixed cap 8,192 ran only 2.2 at once with 11 ms tokens and landed at the same rate. Decode
  throughput on this GPU is roughly flat across these concurrency levels once the occupied range grows (Study C).

### 18. Exp 5: starvation guard on Exp 4 fixed cap 2,048 (2026-11-19)

- **Request (user):** go back to Exp 4 (where the user had removed the starvation guard) and add one at 60 s and
  120 s as E5a and E5b; procedure to be approved before running.
- **Discussion before approval (recorded because it shaped the design):**
  1. The user asked what "continuations first" means. Explained: a continuation is a step that hit the 2,048-token
     cap with output left; it keeps its cache and waits for another 2,048 cells; continuations are admitted before
     new requests; the deadlock breaker discards one only if nothing can run.
  2. The user asked what happens when two continuations wait and only one fits. Answer from the code: the system
     waits; no new request is admitted even if it fits the leftover space. This implicit Exp 4 assumption is now
     written into section 14 and the Exp 4 README (Assumptions).
  3. The user asked whether starving requests block continuations. In the proposal they do not: continuations stay
     first (they hold cells; finishing them frees space). Starving requests block only other new requests.
  4. The user asked whether task time is computed end to end. Verified in code and data: `completion_s = finish of
     last step − arrival`, so it includes all queue waits, prefill, decode, continuation waits and tool delays
     (completion − tool − queue time ≥ 4.8 s for every completed task in both the baseline and Exp 4). Unchanged in
     E5. The starvation clock is a separate number: the current step's queue wait only. End-to-end task age was
     rejected as the trigger (median task time ~257 s would make nearly every task "starving").
  5. The user said "proceed" without picking among the open choices, so the defaults were used: continuations
     first, per-request queue-wait clock, cap 2,048 for both runs.
- **Algorithm:** continuations (unchanged) → starving new requests (wait ≥ T), oldest first, head-of-line block if
  the oldest does not fit → other new requests smallest-need-first only when nobody starves → deadlock breaker.
  Full description: [`mem_admission/README.md`](mem_admission/README.md) (Experiment 5).
- **Checks:** 8-task burst did not exercise the guard (everything fit); 20-task burst with T = 5 s: 24 promotions,
  all admitted, 99 s of guard blocking, 20/20, no overflow.
- **Runs:** `experiments/mem_admission/run_starvation.sh` → `results/mem_admission/smoke_e5_cap2048_starve{60,120}/`,
  log `results/mem_admission/run_e5.log`. Both runs ~16.5 min, 50/50, no errors.
- **Results:**

| | FIFO N4 baseline | Exp 4 fixed cap 2,048 (no guard) | **E5a: guard 60 s** | **E5b: guard 120 s** |
|---|---:|---:|---:|---:|
| Completed / overflow events | 46 / 1 | 50 / 0 | 50 / 0 | 50 / 0 |
| Tasks/min | 3.34 | 3.18 | 3.04 (−4%) | 3.07 (−3%) |
| Task latency mean (s) | 258 | 286 | 307 | 309 |
| Task latency p50 / p95 (s) | 225 / 532 | 257 / 695 | 253 / 732 | 276 / 735 |
| Slowdown vs solo p50 / p95 | 13.4× / 42.1× | 11.3× / 31.6× | 12.5× / 43.6× | 13.3× / 41.0× |
| Within 10× / 20× of solo | 30 / 68% | 48 / 80% | 40 / 72% | 34 / 72% |
| Task latency p50, 1 / 2 / 3+ steps (s) | 134 / 283 / 502 | 96 / 301 / 591 | 127 / 295 / 572 | 151 / 293 / 617 |
| **Longest single request wait (s)** | – | 580 | **232** | **242** |
| New-request wait p50 / p95 / p99 (s) | – | – / – / 438 | 92 / 210 / 227 | 90 / 229 / 241 |
| New-request wait mean (s) | – | 85.3 | 94.2 | 94.4 |
| Admissions that overtook an older request | – | 92 | 17 | 37 |
| Requests promoted to starving (of 113 new requests) | – | – | 74 (65%) | 56 (50%) |
| Time new admissions were held by the guard | – | – | 500 s (51% of the 988 s run) | 356 s (36% of 976 s) |
| Mean running / mean KV use | 3.7 / 50% | 4.8 / 66% | 4.5 / 65% | 4.7 / 65% |
| Step first-token time p50 (s) | 0.14 | 1.19 | 1.71 | 1.43 |
| Time per output token p50 (ms) | 16.3 | 24.2 | 24.0 | 24.7 |
| Continuations / deadlock discards | – | 41 / 0 | 41 / 0 | 41 / 0 |

- **The guard does what it was built to do for single requests:** the longest wait fell from 580 s to 232 s (60 s)
  and 242 s (120 s), p99 wait from 438 s to 227–241 s, and overtakes from 92 to 17–37. No overflow, no deadlock
  discard, 50/50 completed.
- **It does not improve tasks; it makes them slightly worse.** Task p95 rose 695 → 732 / 735 s, mean 286 → 307 /
  309 s, throughput fell 4% / 3%, and the share of tasks within 10× of solo fell 48% → 40% / 34%. Per-request
  waits got shorter at the top but longer on average (mean 85 → 94 s): the guard moves waiting around, it does not
  remove it.
- **Why: under this overload the thresholds are below the typical wait.** The median new-request wait is ~90 s, so
  65% (60 s) and 50% (120 s) of new requests became starving. The guard then runs most of the time (blocking new
  admissions for 51% / 36% of the run) and the scheduler degenerates into FIFO by ready time with head-of-line
  blocking: space freed while the oldest starving request does not yet fit stays empty, so fewer requests run
  (4.8 → 4.5 / 4.7) and the packing benefit of smallest-need-first is lost.
- **Who gained and who lost:** one-step tasks, which smallest-need-first had favoured, lost the most (p50 96 →
  127 / 151 s); 3+-step tasks gained a little at 60 s (591 → 572 s) and lost at 120 s (617 s). Task tail latency is
  dominated by the number of steps (each step queues again, ~90 s per queueing), not by one long wait, so capping
  individual waits does not cap task time.
- **60 s vs 120 s:** 60 s cut the longest wait slightly more and helped 3+-step tasks slightly; 120 s blocked less
  and kept throughput marginally higher. The differences (1–8%) are within what one run per configuration can
  distinguish.
- **Conclusion:** a fixed-time starvation guard is not useful when the system is this overloaded, because nearly
  everyone waits longer than the threshold. A guard only makes sense if T sits well above the typical wait (it
  would then fire rarely), or at sub-capacity load. Fixing the tail of agent tasks needs task-level fairness
  (e.g. prioritising tasks by accumulated wait or remaining steps), not per-request bounds.

### 19. LTR step 1: the published OPT predictor on our requests, zero-shot (2026-11-19)

- **Context:** the user asked for an LTR (Fu et al., NeurIPS 2024) scheduler design (proposal in conversation: frozen
  Qwen3 embeddings + linear head, not yet approved), then asked whether the paper's OPT model could be trained on the
  paper's data and used here. Found that the authors publish both trained predictors and training data, but the labels
  are Llama-3 output lengths on chat prompts, and llama.cpp does not support OPT (the predictor must run in PyTorch
  beside llama.cpp). Disk was 97% full; the largest item, `/opt/pytorch` (8.2 GB), turned out to be a usable CUDA
  PyTorch, so no torch install was needed.
- **Request (user):** first check how far the original predictor's predictions are from the true output tokens on
  the 113 requests, whether a factor can bring them in line, and the error after the factor.
- **Method:** [`ltr_ranking/opt_zero_shot.py`](ltr_ranking/opt_zero_shot.py); published
  `opt-125m-llama3-8b-sharegpt-score` predictor used as in vllm-ltr; 113 test requests, 2,283 calibration requests
  from problems not in the test set; three prompt variants (served prompt head / tail, user text only); mappings
  score → tokens fitted on calibration (constant median, label inverse, one factor, linear, log-linear, isotonic).
- **Setbacks:** a venv over `/opt/pytorch` could not see its torch and pulled 277 MB of packages (deleted; used
  `pip --target ~/ltr_pylib` instead); transformers 5.23 could not build OPT's tokenizer (`GPT2TokenizerFast`
  silently returned `[0]`), replaced by `tokenizers.ByteLevelBPETokenizer` + `</s>`, verified against the reference
  ids; a column bug at the end of the first run (fixed, rerun).
- **Results (test, 113 requests; true median 995, mean 1,590 tokens):**
  - Raw label inverse predicts ~8,000 tokens for everything (MAE 6,602): scores span only −3.9 to +3.7.
  - **One factor** (k = 0.116) gives MAE 978, identical to predicting the constant median (951 tokens) for all.
  - **Best fitted mappings:** MAE 919–955 tokens (−3 to −6% vs constant), median error 51–60%, 50–58% within 2×,
    42–55% underestimated; in-sample best 900 MAE. Predictions are squeezed (p10–p90 594–1,609 vs true 400–3,853).
  - **Kendall τ 0.24–0.27** (paper in-domain 0.54–0.62, cross-dataset 0.40–0.45); prompt length 0.03. Later steps:
    τ 0.05–0.10 with head truncation of the full prompt, 0.23 with user text only.
  - Scoring 8–17 ms per request on the T4.
- **Conclusion:** the published predictor gives a weak ranking and essentially no token-count information for our
  workload; no factor makes it usable as an output budget. Training on Qwen3 output lengths from our own non-test
  agent requests is the next step if LTR is pursued. Details: [`ltr_ranking/README.md`](ltr_ranking/README.md).

### 20. LTR step 2: replay with real outputs; streaming token loss found (2026-11-19)

- **Request (user):** the zero-shot check lacked the model's previous outputs (with their tool calls); replay the
  workload as in the experiments and measure again how far the predicted budgets are from the true token counts.
- **Replay** (`experiments/ltr_ranking/replay_outputs.py`), same generation settings as all experiments. Took five
  attempts: (1) found that **the streamed client loses token ids** of multi-byte characters split across tokens
  (llama.cpp sends no stream message for a token that ends in an incomplete UTF-8 character and later sends only the
  completing token's id; `tools/server/server-context.cpp` lines 2028–2029, 2245, 2284–2287) → non-streamed requests;
  (2) KV overflow with 4 concurrent long histories, results lost because they were written only at the end;
  (3) my retry/saving fix did not apply (scripted edit failed its match) and the old code crashed again;
  (4) livelock: synchronized retries overflowed 36 times in a row → KV-aware admission inside the replay (uses
  recorded `n_gen`; fine because the replay is not a scheduler under test); (5) interrupted at 49/50 tasks by the
  session ending, resumed and finished (113 requests, 0 retries).
- **Token loss in earlier experiments (from the same cause):** Exp 4 cap 2,048 histories lost 198 tokens over 14 of
  104 handovers (max 82), E5a 89 over 11; ≈0.1% of generated tokens; a few handovers recomputed part of their prompt.
  Does not change any scheduling conclusion. Fix for future schedulers: take token ids from a non-streamed response.
- **No generated output contains an end-of-turn token** (`ignore_eos`): outputs run past the model's natural end,
  often repeating themselves.
- **Results** (`experiments/ltr_ranking/opt_with_outputs.py`, leave-one-task-out mappings): best case MAE 925 tokens
  vs 980 for a constant (−6%), median error 57%, 56% within 2×, 50% underestimated; one factor = constant. Kendall τ
  best 0.30 (was 0.24 without outputs), later steps 0.11; the tail-truncated input ranks worse (later steps −0.08).
  Half the prompts exceed OPT's 2,048-token window (median 5,100 tokens for later steps).
- **Conclusion:** the published predictor is not usable as an output budget in any variant; the remaining LTR option
  is training it on Qwen3 lengths from our own non-test requests. Details:
  [`ltr_ranking/README.md`](ltr_ranking/README.md) (Step 2).

### 21. Output-length distribution of the smoke workload (2026-11-20)

- **Request (user):** a histogram of the true output length of the 113 requests, to see the distribution.
- First attempt with the chart tool sorted bars by size and split each bin into two bars (not a histogram);
  redrawn with matplotlib ([`workload_stats/output_length_hist.py`](workload_stats/output_length_hist.py)).

![True output length of the 113 requests](workload_stats/output_length_hist.png)

- Median 995, mean 1,590, p25 / p75 561 / 2,095, p90 3,853, max 7,471 tokens; right-skewed, most requests 250–1,250.
- First steps (50) median 1,698 vs later steps (63) 873; the tail above 3,000 is almost all first steps.
- Longer than the Exp 4 caps: 55 (1,024), 30 (2,048), 10 (4,096), 0 (8,192).
- Ties to earlier results: many continuations at small caps (Exp 4), few jobs finish in MLFQ's first level (Exp 3),
  single-number length estimates are far off (LTR study). Details: [`workload_stats/README.md`](workload_stats/README.md).

### 22. Input-length distribution of the smoke workload (2026-11-20)

- **Request (user):** the same for input lengths. Plotted both meanings
  ([`workload_stats/input_length_hist.py`](workload_stats/input_length_hist.py)): (a) new input tokens of each step,
  (b) the full prompt (history incl. earlier outputs) when the request is sent.

![Input length of the 113 requests](workload_stats/input_length_hist.png)

- (a) Bimodal: first steps 1,080–1,303 tokens (system prompt + task), tool results median 42, max 334.
- (b) Full prompt median 1,826, mean 3,428, p90 7,998, max 14,794; later steps median 4,360; 11 above 8K, none above 16K.
- The context grows through the model's own outputs, not the tools: discarding at a tool call re-prefills thousands
  of tokens to add ~42 (Exp 4's 2.5× prefill); half of later-step prompts exceed OPT's 2,048 window (LTR study).
  Details: [`workload_stats/README.md`](workload_stats/README.md).

### 23. Exp 6 (E6): Exp 4 cap 2,048 with a class × step length ranking (τ ≈ 0.33) (2026-11-20)

- **Context:** the user asked why the Exp 4 oracle's p50 was so low (102 s). Answer from the data: exact reservations
  packed ~5 requests vs 2.2 at fixed cap 8,192, and smallest-need-first with exact needs is shortest-job-first; the
  38 one- and two-step tasks (of 50) finished in 67–78 s, while 3+-step tasks (544 s) and p95 (667 s) were worse than
  FIFO N4. The user noted τ 0.33 is a weak ranking; agreed (≈2 of 3 pairs right, many ties) and proposed a τ sweep
  (noisy oracle at τ 0 / 0.3 / 0.5 / 0.7 / 1.0, 5 runs, reduced to 3 by reusing Exp 4 cap 2,048 as τ ≈ 0). **The
  user chose to run only τ ≈ 0.33**, i.e. the real lookup ranking.
- **Correction before running:** my first lookup also split by the recording model (0.6B / 1.7B), which is trace
  metadata a server would not know; dropped. Table = median output of (task class, step 0 / 1 / 2+) over the 2,283
  calibration requests (problems not in the test set), saved as `experiments/mem_admission/length_lookup.json`:
  mbpp 2,048 / 737 / 834, nl2bash 748 / 651 / 778. On the 113 test requests: Kendall τ-b 0.33, MAE 864 tokens,
  6 distinct values (it mainly separates mbpp first steps from everything else).
- **Algorithm:** Exp 4 fixed cap 2,048 unchanged (reservation `history + 2,048`, continuations first and blocking,
  discard at tool calls, no starvation guard), except that waiting new requests are sorted by predicted output
  length (shortest first), then by need, then by ready time, and every request that fits is admitted in that order.
  The true output length is not used by the scheduler. Code: `scheduler.py --order lookup`.
- **Check:** 12 tasks, burst, 12/12, no overflow, lookup order recorded (`results/mem_admission_checks/check_e6/`).
- **Run:** `results/mem_admission/smoke_e6_cap2048_lookup/`, log `results/mem_admission/run_e6.log`; ~16 min, 50/50.
- **Results:**

| | FIFO N4 baseline | Exp 4 fixed cap 2,048 (τ ≈ 0) | **E6: lookup order (τ 0.33)** | Exp 4 oracle (τ 1, exact reservation) |
|---|---:|---:|---:|---:|
| Completed / overflow events | 46 / 1 | 50 / 0 | 50 / 0 | 50 / 0 |
| Tasks/min | 3.34 | 3.18 | 3.05 | 3.19 |
| Task latency mean / p50 / p95 (s) | 258 / 225 / 532 | 286 / 257 / 695 | **269 / 181 / 747** | 205 / 102 / 667 |
| Slowdown vs solo p50 / p95 | 13.4× / 42.1× | 11.3× / 31.6× | **8.2×** / 48.9× | 6.7× / 12.1× |
| Within 10× / 20× of solo | 30 / 68% | 48 / 80% | **68 / 82%** | 86 / 100% |
| p50 by task: 1 / 2 / 3+ steps (s) | 134 / 283 / 502 | 96 / 301 / 591 | 171 / **86** / 649 | 67 / 78 / 544 |
| p50 by class: mbpp / nl2bash (s) | – | 189 / 274 | 289 / **69** | – |
| New-request wait p50, mbpp first / later steps (s) | – | 22 / 66 | 90 / 22 | – |
| New-request wait p50, nl2bash first / later steps (s) | – | 3 / 148 | 6 / 7 | – |
| Longest wait (s) | – | 580 | 448 | 605 |
| Time per output token p50 (ms) / mean running | 16.3 / 3.7 | 24.2 / 4.8 | 25.2 / 4.7 | 26.0 / 5.0 |
| Continuations / deadlock discards | – | 41 / 0 | 40 / **1** | 0 / 0 |

- **The weak ranking helped the typical task a lot:** median task latency fell from 257 s (Exp 4 cap 2,048, same
  reservations, no useful ranking) to 181 s (−30%) and is now below FIFO N4's 225 s; mean 286 → 269 s; tasks within
  10× of solo rose from 48% to 68%, the best of any realistic scheduler so far. That is about 49% of the way from
  τ ≈ 0 (257 s) to the oracle (102 s), although the oracle also had tighter packing.
- **What it actually did:** the table ranks mbpp first steps (predicted 2,048) behind everything else (650–835). So
  nl2bash tasks and the later steps of any task go first: nl2bash task median 274 → 69 s, two-step tasks 301 → 86 s
  (oracle 78 s). New nl2bash later steps waited a median 7 s instead of 148 s.
- **Who paid:** mbpp first steps waited a median 90 s instead of 22 s; one-step tasks (all 17 are mbpp) rose from
  96 to 171 s, mbpp task median 189 → 289 s; 3+-step tasks 591 → 649 s; p95 rose 695 → 747 s and p95 slowdown
  31.6× → 48.9×. Throughput fell 4% (3.18 → 3.05 tasks/min), within the range of other single runs.
- **One deadlock discard** (first in any Exp 4-family run): at 583 s nothing was running and the waiting
  continuation could not fit, so the scheduler erased a 3,347-cell mbpp first-step cache, which recomputed later.
  Harmless here (50/50), but it shows the ordering can leave the pool full of waiting continuations.
- **Caveat:** one run; the gain is concentrated in which class goes first, and the 50-task mix (17 one-step mbpp,
  15 nl2bash) decides how that shows in the median. On a workload with a different class mix the result could differ.
- **Conclusion:** even τ 0.33 buys a large improvement in typical latency and SLO attainment at the cost of the tail
  and of the long-first-step class, i.e. the classic shortest-job-first trade-off. A starvation guard for the
  held-back class, or a better ranker, are the obvious next steps.

### 24. E6 on the 500-task set vs the Phase 1 FIFO N4 baseline (2026-11-20)

- **Request (user):** run E6 on the 500-task set (the Phase 1 baseline dataset) and compare it with the baseline.
- **Baseline:** Phase 1 `results/t4_full500_v1_part_a/t4_full500_v1_c2_n4_poisson5s_ctx32k_seed2/` (FIFO N4, `-np 4`,
  32K unified pool, `--cache-ram 0`, Poisson mean 5 s, seed 2, 21,600 s deadline): 480/500 (20 KV-overflow
  failures), p50 2,874 s, p95 4,768 s, 4.21 tasks/min, 114 min.
- **Same workload:** `make_workload` reproduces the baseline's task order and arrival times exactly (checked: same
  500 task ids in order, last arrival 2,355 s).
- **Leakage fix:** the smoke lookup table was fitted on all problems except the 50 smoke ones, i.e. it included most
  of the 500-task set. Refitted on the 287 tasks (650 requests) whose problems are not in the 500-task set:
  `experiments/mem_admission/length_lookup_full500.json` (mbpp 2,040 / 767 / 943, nl2bash 763 / 572 / 751; nearly
  the same as before). On the 1,068 requests of the 500-task set: Kendall τ-b 0.32, MAE 813 vs 933 for a constant.
- **Run:** E6 exactly as in section 23 (cap 2,048, reservation `history + 2,048`, `-np 32`, lookup order, no
  starvation guard) with `--lookup length_lookup_full500.json`:
  `results/mem_admission/full500_e6_cap2048_lookup/`, log `results/mem_admission/run_full500_e6.log`.
- **Comparison:** [`mem_admission/compare_full500.py`](mem_admission/compare_full500.py) →
  `results/mem_admission/compare_full500.{csv,md}`. No solo latencies exist for the 500 tasks, so slowdown and SLO
  attainment are not computed; failed tasks are excluded from latency statistics (as in Phase 1) and reported
  separately.
- **Results** (`results/mem_admission/compare_full500.{csv,md}`; run 129 min, finished while the session was closed):

| | Phase 1 FIFO N4 | **E6: cap 2,048 + lookup order** |
|---|---:|---:|
| Completed / KV-overflow failures | 480/500 / 20 | **500/500 / 0** |
| Tasks/min | 4.21 | 3.87 (−8%) |
| Task latency mean / p50 (s) | 2,598 / 2,874 | **2,056 / 1,277** (−21% / −56%) |
| Task latency p90 / p95 / p99 / max (s) | 4,479 / 4,768 / 5,553 / 5,863 | 5,291 / 6,111 / 6,947 / 7,349 (+18 to +28%) |
| p50 by task: 1 / 2 / 3+ steps (s) | 1,278 / 3,544 / 4,553 | 2,422 / **197** / **1,432** |
| p50 by class: mbpp / nl2bash (s) | 2,118 / 3,647 | 2,834 / **82** |
| Step first-token time p50 / time per token p50 | 0.2 s / 16.6 ms | 0.9 s / 26.3 ms |
| Prompt tokens computed | 1.42 M | 3.01 M (2.1×, discard at tool calls) |
| Mean KV use / mean running | 48% / 4.0 | 70% / 5.6 |
| Continuations / deadlock discards / longest wait | – | 327 / 8 / 6,477 s |

- **Reading:** the 50-task pattern holds at 500 tasks and is stronger. Median latency fell 56% and no task failed
  (the baseline lost 20 to KV overflows), because nl2bash tasks and later steps go first (nl2bash median 3,647 → 82 s,
  2-step tasks 3,544 → 197 s). The cost is again the held-back class and the tail: one-step tasks (mbpp) 1,278 →
  2,422 s, p95 +28%, longest single wait 6,477 s (no starvation guard), throughput −8% (slower decode at 70% KV and
  2.1× prefill from discards). 8 deadlock discards (vs 1 at 50 tasks) show waiting continuations filling the pool
  more often at scale. Latency statistics count completed tasks only, which flatters the baseline (its 20 failures
  are excluded).

### 25. Audit of the τ = 0.33 lookup ranking (2026-11-20)

- **Request (user):** explain Kendall's τ, how the 0.33 was obtained and computed, and whether it leaked test data.
  Published as an explainer page: https://claude.ai/artifact/FNmwPVZpiVcrWxDsuH6zYt (private).
- **Recomputed by hand** over all 6,328 pairs of the 113 requests: 3,504 concordant, 1,618 discordant, 1,203 tied in
  the prediction (6 table values), 2 tied in the truth, 1 tied in both. τ-b = (3,504 − 1,618) / √(6,325 × 5,124) =
  0.3313 (matches scipy); τ-a = 0.298; 68.4% of separable pairs ordered correctly.
- **Where the signal is:** a single flag "mbpp first step?" scores τ-b 0.367 on its own; first-vs-later step 0.258;
  within the five short cells (78 requests) only 0.134. The table effectively says "mbpp first steps are long".
- **Leakage audit:** table values use only the 2,283 fitting requests (clean); both recordings of every test problem
  excluded (clean); the recording-model feature (not available to a server) was dropped before Exp 6 (fixed);
  **the predictor was chosen by comparing about six candidates on the same 113 test requests, and the step buckets
  were set in the same session, so 0.33 is mildly optimistic as a held-out number** (selection bias); reusing the
  smoke table on the 500-task set would leak, so it was refitted on the 287 non-overlapping tasks before that run
  (τ-b 0.32 on its 1,068 requests). The OPT predictor's τ needs no fitting, so it involves no test data.
- **Limits recorded:** τ pools all requests (a scheduler compares only concurrent ones), ignores the size of mistakes,
  says nothing about budget accuracy (MAE 864 tokens), and has a standard error of about ±0.06 at n = 113.

### 26. Online output-length estimation without future data (2026-11-25)

- **Request (user):** start from the fixed-budget setup and update output-length estimates from gathered history only;
  how much does Kendall τ improve over time? Before that, the user proposed this as a leakage-free approach; an offline
  check (5-fold cross-validation grouped by problem over all 2,506 pool requests) gave class × step 0.27 → all
  features 0.37, and on the 113 smoke requests (fit on the 2,283) 0.34 → 0.35; the user asked which sets those used
  (answered: pool-wide out-of-fold vs the E6 fit/test split).
- **Method:** [`online_length/simulate.py`](online_length/simulate.py), offline replay of information on the 500-task
  set using admission and completion times from the 500-task E6 run; an estimator at a request's admission sees only
  requests completed before it. Fixed 2,048 budget until 30 completions, then refit every 20 completions. Two
  estimators: online class × step median, online gradient-boosted trees with admission-time features.
- **Per-window τ** rose from ~0.1 to 0.24–0.39 but dipped late; the dip is window composition (E6 admitted mbpp first
  steps late), not worse estimates. Recorded as a pitfall of window scoring.
- **Learning curve** (train on the first K completions, score on all others): τ 0.21–0.30 at 30–100 completions,
  **0.31–0.34 from about 150–200 completions (11–16 min into the run), then flat**, equal to the frozen offline table
  (0.32). MAE ~900 tokens and ~55% underestimated at every K.
- **Conclusion:** online learning reaches the offline estimator's quality within ~15 minutes with no leakage, but more
  history does not raise τ; the ceiling (~0.33) is set by the features. Use estimates for ordering only. Details and
  figures: [`online_length/README.md`](online_length/README.md).

### 27. Exp 7 (E7a, E7b): ordering by an output-length estimator learned online, 500-task set (2026-11-25)

- **Request (user):** set up the online-estimation experiment (section 26) and run E7a and E7b directly on the
  500-task set, then compare with all baselines and experiments. (The user interrupted the 50-task check and asked to
  go straight to 500 tasks.)
- **Algorithm:** Exp 4 fixed cap 2,048 unchanged (reservation `history + 2,048`, continuations first and blocking,
  deadlock breaker, discard at tool calls, no starvation guard, `-np 32`). Waiting new requests are ordered by need
  (as Exp 4) until 30 requests (agent steps) have completed **in this run**; then by predicted output length (shortest
  first, ties by need, then ready time). The estimator learns only from completed requests of the same run (their
  generated token count and the features they had when they became ready) and is refitted every 20 completions; all
  waiting requests are rescored after each refit; a new request is scored when it becomes ready. No lookup table, no
  offline data. Estimates are never used as budgets.
  - **E7a:** median per (class, step 0 / 1 / 2+), overall median for an unseen cell.
  - **E7b:** gradient-boosted trees on log length; features: class, step, this task's previous generated lengths (last,
    mean), tool result error flag and length, history tokens, task text length, number of test cases.
  - Code: [`mem_admission/online_estimator.py`](mem_admission/online_estimator.py), `scheduler.py --order
    online_median | online_gbm`; runner [`mem_admission/run_e7_full500.sh`](mem_admission/run_e7_full500.sh).
- **Environment change:** run with `/opt/pytorch/bin/python3` (scikit-learn 1.9.1, pandas 3.1.0, numpy 2.3.5) instead of
  the repo venv; installing scikit-learn + scipy into the venv (~150–200 MB) was too risky with 441 MB free disk.
- **Checks and incidents:** a 25-task burst check of E7b was started, moved to the background by the user, and then
  stopped before finishing (so no check output). A `pkill -f check_e7b` also killed its own shell (the pattern was in
  its command line), so the first launch never happened; relaunched. E7a ran past its first refits without errors
  (98 requests finished at 3 min); both estimator paths were also tested offline on 40 tasks (3 refits each).
- **Comparison:** [`mem_admission/compare_full500.py`](mem_admission/compare_full500.py) now includes Phase 1 FIFO N4
  (baseline), Phase 1 FIFO N16 (same workload; 177/500 completed, so its latencies cover only short survivors), E6,
  E7a, E7b, and computes the online Kendall τ-b of each run's predictions made at admission.
- **Runs:** `results/mem_admission/full500_e7a_cap2048_online_median/`, `full500_e7b_cap2048_online_gbm/` (plus
  `online_model.csv` with every refit), log `results/mem_admission/run_full500_e7.log`.
- **Attempt 1 lost (2026-11-25):** E7a stopped at 774 of ~1,400 requests (~02:40 UTC) when the Claude session ended,
  because the runner had been started as a session background task; E7b never started. Partial run kept as
  `results/mem_admission/full500_e7a_interrupted_attempt1/` (no meta.json; not used), log
  `run_full500_e7_attempt1_interrupted.log`. Relaunched at 05:38 UTC with `setsid nohup … & disown` so the runs survive
  the session. Lesson: long runs must be detached from the session.
- **E7a result (finished ~07:50 UTC, run `results/mem_admission/full500_e7a_cap2048_online_median/`):**

  | metric | FIFO N4 (baseline) | E6 lookup | E7a online median |
  |---|---:|---:|---:|
  | completed | 480/500 | 500/500 | 500/500 |
  | tasks/min | 4.21 | 3.87 | 3.78 |
  | latency mean (s) | 2,598 | 2,056 | 2,210 |
  | latency p50 (s) | 2,874 | 1,277 | 1,773 |
  | latency p90 / p95 / p99 (s) | 4,479 / 4,768 / 5,553 | 5,291 / 6,111 / 6,947 | 5,681 / 6,332 / 7,277 |
  | latency max (s) | 5,863 | 7,349 | 7,591 |
  | p50 1-step / 2-step / 3+-step (s) | 1,278 / 3,544 / 4,553 | 2,422 / 197 / 1,432 | 2,082 / 188 / 4,170 |
  | p50 mbpp / nl2bash (s) | 2,118 / 3,647 | 2,834 / 81 | 2,109 / 117 |
  | continuations / deadlock discards | – | 327 / 8 | 329 / 6 |
  | max wait (s) | – | 6,477 | 7,020 |
  | Kendall τ-b of the order used | – | 0.33 (frozen lookup, offline) | 0.26 (online, 1,031 requests) |

  - The online median refitted 52 times; the first 30 completions (~160 s) were ordered by need.
  - Online τ-b by quarter of the run (requests in admission order, 257–258 each): 0.155 / 0.156 / 0.318 / 0.240,
    trained on 30–270 / 270–530 / 530–790 / 790–1,050 completed steps. Per-quarter τ also mixes in population
    composition (which classes and steps arrive in that quarter), as seen in section 26.
  - **Reading:** E7a keeps E6's main gains over FIFO (no failures, p50 −38% vs FIFO), but is worse than E6 on every
    latency figure (p50 +39%, p95 +4%) and slightly lower throughput. The weaker early ranking (τ ≈ 0.16 for the first
    half) costs mostly 3+-step tasks (p50 1,432 → 4,170 s). The leak-free class×step median does not reach the offline
    lookup's ranking on this run.
- **E7b result (finished ~10:05 UTC, run `results/mem_admission/full500_e7b_cap2048_online_gbm/`; runner log
  `results/mem_admission/run_full500_e7.log`; full table `results/mem_admission/compare_full500.{csv,md}`):**

  | metric | FIFO N4 (baseline) | FIFO N16 | E6 lookup | E7a online median | E7b online model |
  |---|---:|---:|---:|---:|---:|
  | completed | 480/500 | 177/500 | **500/500** | **500/500** | **500/500** |
  | tasks/min | **4.21** | (4.36, 65% failed) | 3.87 | 3.78 | 3.70 |
  | run length (min) | 113.9 | 40.6 | 129.3 | 132.3 | 135.0 |
  | latency mean (s) | 2,598 | – | **2,056** | 2,210 | 2,173 |
  | latency p50 (s) | 2,874 | – | **1,277** | 1,773 | 1,523 |
  | latency p90 / p95 / p99 (s) | **4,479 / 4,768 / 5,553** | – | 5,291 / 6,111 / 6,947 | 5,681 / 6,332 / 7,277 | 5,317 / 6,136 / 7,134 |
  | latency max (s) | **5,863** | – | 7,349 | 7,591 | 7,850 |
  | p50 1-step / 2-step / 3+-step (s) | 1,278 / 3,544 / 4,553 | – | 2,422 / 197 / **1,432** | 2,082 / **188** / 4,170 | 2,702 / 295 / 2,326 |
  | p50 mbpp / nl2bash (s) | **2,118** / 3,647 | – | 2,834 / **81** | 2,109 / 117 | 2,846 / 101 |
  | TTFT p50 (s) / TPOT p50 (ms) | 0.18 / 16.6 | – | 0.89 / 26.3 | 0.94 / 26.6 | 0.97 / 27.2 |
  | prompt tokens computed | 1.42 M | – | 3.01 M | 3.00 M | 3.04 M |
  | mean KV use / mean running | 48% / 4.0 | – | 70% / 5.6 | 70% / 5.6 | 70% / 5.6 |
  | continuations / deadlock discards | – | – | 327 / 8 | 329 / 6 | 326 / 9 |
  | max wait (s) | – | – | **6,477** | 7,020 | 7,402 |
  | Kendall τ-b of the order used | – | – | **0.33** (offline lookup, fitted on non-test tasks) | 0.26 (online) | 0.32 (online) |

  FIFO N16 latencies are over the 177 tasks that survived (323 failed on KV overflow), so they are not comparable.

  Online τ-b by quarter of the run (admission order, ~258 requests each; estimator trained on 30–270 / 270–530 /
  530–790 / 790–1,050 completed steps):

  | quarter | 1 | 2 | 3 | 4 | whole run |
  |---|---:|---:|---:|---:|---:|
  | E7a online median | 0.155 | 0.156 | 0.318 | 0.240 | 0.260 (1,031 requests) |
  | E7b online model | 0.231 | 0.259 | 0.367 | 0.294 | 0.317 (1,032 requests) |

  - **The model beats the median at every stage of the run** (+0.06 to +0.10 τ per quarter, +0.06 overall), as the
    offline replay of section 26 predicted. History features (previous step lengths, tool-result error) give it a
    usable ranking much earlier.
  - **Latency follows τ:** E7b's p50 (1,523 s) sits between E7a (1,773 s) and E6 (1,277 s); mean and p90 are close to
    E6. E7b nearly matches E6's τ overall (0.32 vs 0.33) but its weaker first half (0.23–0.26) is when the queue
    builds up, so it still trails E6 on median.
  - **Same memory behaviour in all three ordering runs:** 0 failures, ~70% mean KV, ~5.6 running, ~3.0 M prompt tokens
    (tool-call discards), 326–329 continuations, 6–9 deadlock discards. Ordering changes who waits, not how much is
    served; throughput is 3.70–3.87 tasks/min in every case (−8 to −12% vs FIFO N4).
  - **Every memory-aware run trades tail for median vs FIFO:** p50 −38 to −56%, but p95 +28 to +33% and max +25 to
    +34%. Shortest-predicted-first holds back long mbpp first steps; the tail is those tasks.
  - **Verdict:** a leak-free online estimator is viable (E7b gets 96% of the offline lookup's τ with no outside data,
    all tasks complete, p50 −47% vs FIFO), but neither online variant beats E6 on this run. One run per configuration;
    differences of a few percent between E6 / E7a / E7b are within what one would expect from run-to-run variation
    in admission order, so only the p50 ranking (E6 < E7b < E7a) and the τ ranking are taken as signal.

## Results at a glance

| | Baseline FIFO N4 | Exp 1 | Exp 2 | Exp 3 MLFQ | Exp 4 fixed cap 1,024 | Exp 4 fixed cap 2,048 | Exp 4 fixed cap 4,096 | Exp 4 fixed cap 8,192 | E5a (cap 2,048, guard 60 s) | E5b (cap 2,048, guard 120 s) | E6 (cap 2,048, lookup order) | Exp 4 oracle* |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Completed (of 50) | 46 | 46 | **50** | 46 | **50** | **50** | **50** | **50** | **50** | **50** | **50** | **50** |
| Overflow events | 1 | 1 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Tasks/min | **3.34** | 3.08 | 3.04 | 2.19 | 3.00 | 3.18 | 3.16 | 3.03 | 3.04 | 3.07 | 3.05 | 3.19 |
| Latency mean (s) | **258** | – | 357 | 469 | 300 | 286 | 293 | 335 | 307 | 309 | 269 | 205 |
| Latency p50 / p95 (s) | **225 / 532** | 268 / 640 | 322 / 647 | 408 / 1,012 | 283 / 752 | 257 / 695 | 268 / 709 | 319 / 718 | 253 / 732 | 276 / 735 | 181 / 747 | 102 / 667 |
| Slowdown vs solo p50 / p95 | 13.4× / 42.1× | – | 16.8× / 82.9× | 24.2× / 45.7× | 11.9× / **23.7×** | **11.3×** / 31.6× | 12.5× / 35.2× | 15.6× / 47.6× | 12.5× / 43.6× | 13.3× / 41.0× | **8.2×** / 48.9× | 6.7× / 12.1× |
| Within 10× / 20× of solo | 30 / 68% | – | 38 / 58% | 16 / 34% | 42 / **84%** | **48** / 80% | 42 / 72% | 34 / 66% | 40 / 72% | 34 / 72% | **68** / 82% | 86 / 100% |
| Longest single request wait (s) | – | – | – | – | – | 580 | – | – | **232** | 242 | 448 | – |
| Time per output token, p50 (ms) | 16.3 | 16.7 | 19.8 | 23.0 | 29.2 | 24.2 | 18.6 | 11.3 | 24.0 | 24.7 | 25.2 | 26.0 |
| Mean KV use | 50% | 51% | 57% | 80% | 76% | 66% | 52% | 31% | 65% | 65% | 66% | 65% |
| Mean running requests | 3.7 | – | 3.7 | 3.6 | 5.3 | 4.8 | 3.6 | 2.2 | 4.5 | 4.7 | 4.7 | 5.0 |

### 50-task smoke set

The table above is the 50-task smoke set.

### 500-task set (same workload as the Phase 1 FIFO N4 baseline; one run each)

| | FIFO N4 (baseline) | E6 (cap 2,048, lookup order) | E7a (cap 2,048, online median) | E7b (cap 2,048, online model) |
|---|---:|---:|---:|---:|
| Completed (of 500) | 480 | **500** | **500** | **500** |
| Tasks/min | **4.21** | 3.87 | 3.78 | 3.70 |
| Latency mean (s) | 2,598 | **2,056** | 2,210 | 2,173 |
| Latency p50 / p95 (s) | 2,874 / **4,768** | **1,277** / 6,111 | 1,773 / 6,332 | 1,523 / 6,136 |
| Latency max (s) | **5,863** | 7,349 | 7,591 | 7,850 |
| Kendall τ-b of the order | – | **0.33** (offline) | 0.26 (online) | 0.32 (online) |

FIFO N16 (500 tasks) completed only 177/500. Details in section 27 and `results/mem_admission/compare_full500.md`.

\* Exp 4 oracle = reservation `history + true remaining output` (cap 8,192 run of section 15); not achievable
without knowing output lengths, kept as an upper bound. The oracle-below-cap runs (caps 1,024–4,096, section 15)
are in [`mem_admission/README.md`](mem_admission/README.md). Bold = best among realistic (non-oracle) schedulers.

## Conclusions so far (50-task smoke, overloaded load, one run per configuration)

1. **FIFO N4 is still the best realistic scheduler on throughput and raw latency** (3.34 tasks/min; mean 258 s,
   p50 225 s, p95 532 s), but it loses 8% of tasks to a single KV overflow that fails every running request at once.
2. **Memory-aware admission (Exp 4) eliminates overflow** at every cap tested, with no knowledge of output length,
   and improves the failure-aware measures (tasks within 10× / 20× of solo, p95 slowdown). It costs 5–10% throughput
   and makes median and tail latency worse, because worst-case reservation leaves 11–46 points of the pool reserved
   but unused.
3. **Knowing output lengths would change the picture**: the oracle reservation halves typical latency (p50 102 s,
   86% of tasks within 10× of solo). Closing the gap between fixed caps and the oracle (better budget estimates,
   growing reservations, overrun handling) is the most promising direction.
4. **Idle KV costs speed, not only memory** (Study C): ~0.45 ms per decode step per 1K cells of occupied range,
   following the highest occupied cell. Schedulers that keep caches around (Exp 2, Exp 3) pay for it in time per
   token; one that discards everything (Exp 4) pays ~2.5× prefill instead. A cost-based middle ground is untested.
5. **Per-request priority (Exp 3, MLFQ) does not suit agents**: steps reset their priority at every tool call, and
   keeping every idle cache made decoding 41% slower.
6. **A fixed-time starvation guard (Exp 5) bounds individual waits but not task latency under overload.** At 60 s
   and 120 s it cut the longest request wait from 580 s to ~240 s, but task p95 and mean got slightly worse and
   throughput fell 3–4%: with a median wait of ~90 s, half or more of all requests became "starving", so the guard
   ran 36–51% of the time and turned packing into FIFO with head-of-line blocking. Agent task tails come from many
   queueings per task, so task-level fairness is the next thing to try, not per-request bounds.
7. **Ordering by even a weak length ranking pays off for typical latency (Exp 6).** A class × step lookup (τ 0.33)
   cut Exp 4's median from 257 to 181 s and lifted tasks within 10× of solo from 48% to 68%, the best realistic
   result so far, by holding back mbpp first steps (predicted long). The tail and that class paid (p95 747 s,
   one-step mbpp tasks 96 → 171 s), the shortest-job-first trade-off.
9. **On the 500-task set, memory-aware admission plus length ordering wins on completion and median, loses on tail
   and throughput (E6, E7).** All three ordering runs finished 500/500 (FIFO N4: 480) and cut p50 by 38–56%, but
   p95 rose ~30% and throughput fell 8–12%.
10. **A leak-free online estimator nearly matches the offline lookup (E7).** Learning only from this run's completed
    requests, gradient-boosted trees with history features reached τ 0.32 (offline lookup 0.33) and beat the online
    class × step median (0.26) in every quarter. Its weak first half still cost median latency (1,523 vs 1,277 s).
    Better cold-start ranking, not a better steady-state model, is where the remaining gap is.
8. **Caveats:** all of this is one run per configuration on 50 tasks under overload (12 tasks/min offered vs ~3
   served), with caps chosen in-sample. Sub-capacity loads and the 500-task set are needed before claims.

## Reproducing every experiment (configs and commands)

Every run writes its full configuration to `<run dir>/meta.json` (`run` = scheduler / workload settings,
`server_args` = exact llama-server command line, `env` = machine and model, `doc` = algorithm text). The tables below
are copied from those files; when in doubt, `meta.json` is authoritative.

### Common setup (all runs)

| item | value |
|---|---|
| GPU / host | NVIDIA T4 (16 GB), EC2, 15.4 GiB RAM, Amazon Linux 2023 (kernel 6.12) |
| llama.cpp | b11780 with KV instrumentation. Phase 1: `/home/ec2-user/t4runtime/llama-b11780-kv-src/build/bin/llama-server`; Phase 2: same build copied to `/home/ec2-user/bin/llama-server-kv` |
| model | `/home/ec2-user/Qwen3-0.6B-Q8_0.gguf` (Qwen3-0.6B Q8_0, 639,446,688 bytes) |
| server flags (all scheduling runs) | `-c 32768 -np <slots> --port 8090 --metrics -ngl 999 -kvu --cache-ram 0 --load-mode none` (+ `--slot-save-path <tmp dir>` when the scheduler erases / saves slots). One unified 32,768-cell KV pool, no host prompt cache, no mmap |
| decoding | greedy, `ignore_eos`, `n_predict` = the recorded step length (or the cap chunk), so every run generates the same tokens as the trace |
| workloads | smoke: `data/traces/agenttrace_50_seed2.jsonl` (50 tasks, 113 requests); full: `data/traces/agenttrace_full_500_seed2.jsonl` (500 tasks, 1,068 requests) |
| arrivals | Poisson, rate 0.2 tasks/s (mean 5 s), seed 2 (identical arrival times across runs on the same trace); tool delays replayed from the trace (`delay_scale` 1.0) |
| limits | run deadline 21,600 s, per-request timeout 1,800 s (Phase 1 smoke load sweep: 14,400 s / 900 s) |
| environment | `export MODEL=/home/ec2-user/Qwen3-0.6B-Q8_0.gguf LLAMA_SERVER=/home/ec2-user/bin/llama-server-kv` before every run |
| Python | repo venv `.venv/bin/python` (or `uv run python`) for runs without scikit-learn; `/opt/pytorch/bin/python3` for E7, `compare_full500.py`, online-length and LTR analyses (scikit-learn, scipy, torch); LTR also needs `PYTHONPATH=~/ltr_pylib` and the checkpoints in `~/models/ltr/` |
| long runs | launch detached so they survive the terminal / Claude session: `setsid nohup <cmd> > <log> 2>&1 < /dev/null & disown` (section 27, attempt 1 lost) |

All commands are run from the repository root. Run scripts skip any run whose `meta.json` already exists (delete the
run directory to redo it).

### Per-experiment configuration

| experiment | run dir(s) | trace / tasks | slots (`-np`) | scheduler config (from `meta.json` → `run`) |
|---|---|---|---:|---|
| Phase 1 FIFO N4 baseline, 500 (R2) | `results/t4_full500_v1_part_a/t4_full500_v1_c2_n4_poisson5s_ctx32k_seed2/` | full / 500 | 4 | FIFO request scheduler, concurrency 4, `cache_prompt` true, ctx 32,768, `cache_ram` 0, `kv_unified` true, `mmap` false, rate 0.2, seed 2 |
| Phase 1 FIFO N16, 500 (R3) | `.../t4_full500_v1_c3_n16_poisson5s_ctx32k_seed2/` | full / 500 | 16 | as R2, concurrency 16 |
| Phase 1 FIFO N1, 500 (R1) | `.../t4_full500_v1_c1_n1_poisson10s_ctx32k_seed2/` | full / 500 | 1 | as R2, concurrency 1, rate 0.1 |
| Phase 1 smoke load sweep | `results/t4_load_v1_part_b/t4_load_v1_{burst,poisson10s,poisson5s}_n{4,16}_ctx32k_seed2/` | smoke / 50 | 4 / 16 | FIFO, rate 0 (burst) / 0.1 / 0.2, deadline 14,400 s, timeout 900 s |
| Baseline rerun (smoke) | `results/kv_policy/smoke_baseline_fifo_n4/` | smoke / 50 | 4 | as R2 on the smoke trace |
| Study A: solo latency | `results/solo_SLO/smoke50_v1/{retain,evict}/` | smoke / 50 | 4 | one task at a time, 3 reps, modes retain / evict, ctx 32,768, seed 2 |
| Study B: swap / stay / recompute costs | `swap_vs_stay_vs_recompute/data/` | smoke | – | 3 reps, 4 workers, ctx 32,768, port 8091, seed 2 |
| Study C: idle-KV microbenchmark | `experiments/idle_kv_microbench/data/` | synthetic | – | 3 reps, 256 generated tokens, port 8096, seed 2 (+ `run_range.py`) |
| Exp 1: KV policy, requeue back | `results/kv_policy/smoke_exp1_policy_back/` | smoke / 50 | 16 | concurrency 4, requeue back; policy capacity 32,768, margin 512, stay max idle 30 s, host budget 50,000 tokens, tool prior 1.3 s, service prior 7.0 s, EMA 0.05 |
| Exp 2: KV policy, requeue front | `results/kv_policy/smoke_exp2_policy_front/` | smoke / 50 | 16 | as Exp 1, requeue front |
| Exp 3: MLFQ | `results/mlfq_fastserve/smoke_exp3_mlfq/` | smoke / 50 | 16 | concurrency 4, chunk 256, quanta 256 / 512 / 1,024 / 2,048 / 4,096 / ∞, host budget 50,000, TPOT prior 16 ms, EMA 0.05 |
| Exp 4 oracle-informed (first runs) | `results/mem_admission/smoke_exp4_cap{1024,2048,4096,8192}/` | smoke / 50 | 32 | cap as named, reserve `min_true` (history + min(cap, true remaining)), margin 512, order need; **uses true length, upper bound only** |
| Exp 4 fixed cap | `results/mem_admission/smoke_exp4fc_cap{1024,2048,4096,8192}/` | smoke / 50 | 32 | cap as named, reserve `cap` (history + cap), margin 512, order need, no starvation guard |
| E5a / E5b: starvation guard | `results/mem_admission/smoke_e5_cap2048_starve{60,120}/` | smoke / 50 | 32 | cap 2,048, reserve cap, margin 512, `starve_s` 60 / 120 |
| E6 smoke: lookup order | `results/mem_admission/smoke_e6_cap2048_lookup/` | smoke / 50 | 32 | cap 2,048, reserve cap, order lookup, `length_lookup.json` (fitted on 2,283 non-smoke requests) |
| E6 500: lookup order | `results/mem_admission/full500_e6_cap2048_lookup/` | full / 500 | 32 | cap 2,048, reserve cap, order lookup, `length_lookup_full500.json` (fitted on 650 requests of 287 tasks not in the 500 set) |
| E7a: online median | `results/mem_admission/full500_e7a_cap2048_online_median/` | full / 500 | 32 | cap 2,048, reserve cap, order `online_median`; estimator MIN_FIT 30, REFIT 20, median per (class, step 0/1/2+) |
| E7b: online model | `results/mem_admission/full500_e7b_cap2048_online_gbm/` | full / 500 | 32 | cap 2,048, reserve cap, order `online_gbm`; MIN_FIT 30, REFIT 20, `HistGradientBoostingRegressor(max_iter=200, learning_rate=0.05, min_samples_leaf=min(30, max(5, n//10)), random_state=0)` on log length, 9 features (section 27) |

(The `lookup` field in E7 `meta.json` shows the default `length_lookup.json`; it is not read when the order is
`online_*`.)

### Commands

```bash
cd /home/ec2-user/rohan/MemAware-Agent-Edge-Serving
export MODEL=/home/ec2-user/Qwen3-0.6B-Q8_0.gguf LLAMA_SERVER=/home/ec2-user/bin/llama-server-kv

# Phase 1 FIFO N4 baseline on 500 tasks (same config as R2; writes results/<group>/<name>/).
# For N16 use concurrency=16, parallel=16; for the smoke rerun use the 50-task trace and n_tasks=50.
.venv/bin/python -u - <<'PY'
from phase1.runner import RunConfig, execute
from harness.server import ServerConfig
execute(RunConfig(name="full500_fifo_n4", trace="data/traces/agenttrace_full_500_seed2.jsonl", n_tasks=500,
                  arrival_rate=0.2, concurrency=4, delay_scale=1.0, cache_prompt=True, deadline_s=21600,
                  request_timeout_s=1800, scheduler="request", require_kv_metrics=True, seed=2,
                  server=ServerConfig(ctx=32768, parallel=4, kv_unified=True, cache_ram=0, mmap=False)),
        group="phase1_repro")
PY

# Study A (solo latency), ~2 h, resumable
.venv/bin/python -m experiments.solo_SLO.run_solo --reps 3
.venv/bin/python -m experiments.solo_SLO.analyze
# Study B (cost models), ~60 min
.venv/bin/python -m swap_vs_stay_vs_recompute.measure --reps 3
.venv/bin/python -m swap_vs_stay_vs_recompute.fit
# Study C (idle-KV microbenchmark), ~30 min + ~3 min
.venv/bin/python -m experiments.idle_kv_microbench.run --reps 3
.venv/bin/python -m experiments.idle_kv_microbench.run_range
.venv/bin/python -m experiments.idle_kv_microbench.plot

# Baseline rerun + Exp 1 + Exp 2 (smoke), then comparison
experiments/kv_policy/run_experiments.sh smoke
#   single runs:
.venv/bin/python -u -m experiments.kv_policy.runner --name smoke_exp1_policy_back  --requeue back  \
  --trace data/traces/agenttrace_50_seed2.jsonl --n-tasks 50 --rate 0.2
.venv/bin/python -u -m experiments.kv_policy.runner --name smoke_exp2_policy_front --requeue front \
  --trace data/traces/agenttrace_50_seed2.jsonl --n-tasks 50 --rate 0.2

# Exp 3 MLFQ (smoke), ~21 min
.venv/bin/python -u -m experiments.mlfq_fastserve.scheduler --name smoke_exp3_mlfq \
  --trace data/traces/agenttrace_50_seed2.jsonl --n-tasks 50 --rate 0.2
.venv/bin/python -m experiments.mlfq_fastserve.compare

# Exp 4 oracle-informed caps (upper bound only) and fixed caps (smoke)
experiments/mem_admission/run_caps.sh          # --cap C --reserve min_true is the old default of these runs
experiments/mem_admission/run_fixed_caps.sh    # = scheduler --name smoke_exp4fc_capC --cap C --reserve cap
# Exp 5 starvation guard (smoke)
experiments/mem_admission/run_starvation.sh    # = scheduler --cap 2048 --reserve cap --starve-s 60 | 120
# E6 lookup order, smoke and 500
.venv/bin/python -u -m experiments.mem_admission.scheduler --name smoke_e6_cap2048_lookup --cap 2048 --reserve cap \
  --order lookup --lookup length_lookup.json
.venv/bin/python -u -m experiments.mem_admission.scheduler --name full500_e6_cap2048_lookup --cap 2048 --reserve cap \
  --order lookup --lookup length_lookup_full500.json \
  --trace data/traces/agenttrace_full_500_seed2.jsonl --n-tasks 500 --rate 0.2
.venv/bin/python -m experiments.mem_admission.compare                       # smoke comparison table
# E7a then E7b (500), ~2.2 h each, then the 500-task comparison; run detached
setsid nohup experiments/mem_admission/run_e7_full500.sh > results/mem_admission/run_full500_e7.log 2>&1 < /dev/null & disown
#   single runs:
/opt/pytorch/bin/python3 -W ignore -u -m experiments.mem_admission.scheduler --name full500_e7a_cap2048_online_median \
  --trace data/traces/agenttrace_full_500_seed2.jsonl --n-tasks 500 --rate 0.2 --cap 2048 --reserve cap --order online_median
/opt/pytorch/bin/python3 -W ignore -u -m experiments.mem_admission.scheduler --name full500_e7b_cap2048_online_gbm \
  --trace data/traces/agenttrace_full_500_seed2.jsonl --n-tasks 500 --rate 0.2 --cap 2048 --reserve cap --order online_gbm
/opt/pytorch/bin/python3 -W ignore -m experiments.mem_admission.compare_full500

# Analyses (no server needed unless noted)
.venv/bin/python experiments/workload_stats/output_length_hist.py
.venv/bin/python experiments/workload_stats/input_length_hist.py
/opt/pytorch/bin/python3 -W ignore -m experiments.online_length.simulate
/opt/pytorch/bin/python3 -W ignore -c "from experiments.online_length.simulate import learning_curve; learning_curve()"
PYTHONPATH=~/ltr_pylib /opt/pytorch/bin/python3 -W ignore -m experiments.ltr_ranking.opt_zero_shot     # ~2 min
.venv/bin/python -u -m experiments.ltr_ranking.replay_outputs        # needs the server, ~15 min, resumable
PYTHONPATH=~/ltr_pylib /opt/pytorch/bin/python3 -W ignore -m experiments.ltr_ranking.opt_with_outputs  # ~1 min
```

Notes for reproducers:
- Defaults of `experiments.mem_admission.scheduler`: `--trace` smoke, `--n-tasks 50`, `--rate 0.2`, `--reserve cap`,
  `--order need`, no `--starve-s`; slots 32, margin 512, ctx 32,768, deadline 21,600 s, timeout 1,800 s, seed 2 are
  fixed in code. The oracle-informed Exp 4 runs predate `--reserve`; reproduce them with `--reserve min_true`.
- Output length is used by the harness only to stop generation where the recorded step ended (a real server stops
  at end-of-sequence); it is never an input to admission, ordering or reservation, except in the oracle runs.
- Results are single runs; expect run-to-run variation of a few percent in latency from admission-order timing.

## Where everything is

Paths are relative to the repository root. Run logs under `results/` match `results/**/*.log` in `.gitignore`, so
they are **not committed** unless force-added (`git add -f`); the ones outside `results/` are committed normally.

| Study | Code | Results | Run log |
|---|---|---|---|
| A: solo latency | `experiments/solo_SLO/run_solo.py`, `analyze.py` | `results/solo_SLO/smoke50_v1/{retain,evict}/` (`task_metadata.jsonl`, `tasks.csv`, `requests.csv`, `tool_waits.csv`, `per_task.csv`), `compare.csv`, `meta_20261113T184414.json`, `server.log` | `results/solo_SLO/smoke50_v1/run.log` (copied from `/home/ec2-user/solo_smoke50_v1.log`) |
| B: cost models | `swap_vs_stay_vs_recompute/measure.py`, `fit.py` | `swap_vs_stay_vs_recompute/data/` (`measurements.csv`, `trace_requests.csv`, `meta.json`, `server.log`), `params.json`, `fit_report.md`, `fit.png` | `swap_vs_stay_vs_recompute/measure.log` |
| Exp 1, Exp 2 + baseline rerun | `experiments/kv_policy/policy.py`, `runner.py`, `run_experiments.sh`, `compare.py` | `results/kv_policy/smoke_baseline_fifo_n4/`, `smoke_exp1_policy_back/`, `smoke_exp2_policy_front/` (each: `tasks.csv`, `requests.csv`, `timeline.csv`, `kv_memory.csv`, `gpu_memory.csv`, `meta.json`; policy runs also `decisions.csv`, `events.csv`), `compare_smoke.{csv,md}` | `results/kv_policy_smoke.log` |
| Exp 3: MLFQ | `experiments/mlfq_fastserve/scheduler.py`, `compare.py` | `results/mlfq_fastserve/smoke_exp3_mlfq/` (as above plus `evictions.csv`, `joins.csv`), `compare_smoke.{csv,md}` | `results/mlfq_fastserve_smoke.log` |
| Exp 4: memory-aware admission | `experiments/mem_admission/scheduler.py`, `run_caps.sh`, `run_fixed_caps.sh`, `compare.py` | `results/mem_admission/smoke_exp4_cap{1024,2048,4096,8192}/` (oracle-informed), `smoke_exp4fc_cap{1024,2048,4096,8192}/` (fixed cap) (`tasks.csv`, `requests.csv`, `reservations.csv`, `dispatches.csv`, `events.csv`, `timeline.csv`, `kv_memory.csv`, `gpu_memory.csv`, `meta.json`, `server.log`), `compare_smoke.{csv,md}` | `results/mem_admission_smoke.log`, `results/mem_admission_smoke_cap8192.log` (oracle-informed), `results/mem_admission_fixed_caps.log` (fixed cap) |
| Workload stats: output- and input-length histograms (113 requests) | `experiments/workload_stats/output_length_hist.py`, `input_length_hist.py`, [`README.md`](workload_stats/README.md) | `experiments/workload_stats/output_length_hist.png` (median 995, mean 1,590, p75 2,095, p90 3,853, max 7,471; first steps median 1,698, later steps 873; 55 / 30 / 10 requests exceed 1,024 / 2,048 / 4,096), `input_length_hist.png` (new input and full prompt) | – |
| Exp 6: lookup ordering | `experiments/mem_admission/scheduler.py --order lookup`, `length_lookup.json`, `length_lookup_full500.json`, `compare_full500.py` | `results/mem_admission/smoke_e6_cap2048_lookup/`, `full500_e6_cap2048_lookup/` (Exp 4 files), `compare_full500.{csv,md}`, check `results/mem_admission_checks/check_e6/` | `results/mem_admission/run_e6.log`, `run_full500_e6.log` |
| Exp 7: online-estimator ordering (500 tasks) | `experiments/mem_admission/scheduler.py --order online_median / online_gbm`, `online_estimator.py`, `run_e7_full500.sh`, `compare_full500.py` (run with `/opt/pytorch/bin/python3`) | `results/mem_admission/full500_e7a_cap2048_online_median/`, `full500_e7b_cap2048_online_gbm/` (Exp 4 files plus `online_model.csv`; `requests.csv` has `pred`, `pred_n`), `compare_full500.{csv,md}`; lost attempt `full500_e7a_interrupted_attempt1/` | `results/mem_admission/run_full500_e7.log`, `run_full500_e7_attempt1_interrupted.log` |
| Online length estimation (offline replay) | `experiments/online_length/simulate.py` (run with `/opt/pytorch/bin/python3`) | `results/online_length/` (`predictions.csv`, `windows.csv`, `summary.json`, `tau_over_time.png`, `learning_curve.{csv,png}`) | `results/online_length/run.log` |
| LTR-0: published predictor, zero-shot | `experiments/ltr_ranking/opt_zero_shot.py` (run with `/opt/pytorch/bin/python3`, `PYTHONPATH=~/ltr_pylib`); checkpoint in `~/models/ltr/` | `results/ltr_ranking/opt_zero_shot/` (`predictions.csv`, `errors.csv`, `rank_reference.csv`, `by_step.csv`, `meta.json`) | `results/ltr_ranking/opt_zero_shot/run.log` |
| LTR step 2: replay + predictor with real outputs | `experiments/ltr_ranking/replay_outputs.py`, `opt_with_outputs.py` | `results/ltr_ranking/replay_outputs/` (`outputs.jsonl`: generated token ids per step), `results/ltr_ranking/opt_with_outputs/` (`predictions.csv`, `errors.csv`, `by_step.csv`, `tau_with_vs_without_outputs.csv`, `meta.json`) | `results/ltr_ranking/replay_outputs.log` and `replay_outputs_attempt{2_failed,3_livelock,4_interrupted}.log`, `opt_with_outputs/run.log` |
| Exp 5: starvation guard | `experiments/mem_admission/scheduler.py --starve-s`, `run_starvation.sh`, `compare.py` | `results/mem_admission/smoke_e5_cap2048_starve{60,120}/` (same files as Exp 4; `events.csv` has `starving` rows, `requests.csv` a `starving` column, `reservations.csv` `starving` / `blocked_by_starving`), checks in `results/mem_admission_checks/check_e5{,b}/`, `compare_smoke.{csv,md}` | `results/mem_admission/run_e5.log` |
| C: idle-KV microbenchmark | `experiments/idle_kv_microbench/run.py`, `run_range.py`, `plot.py` | `experiments/idle_kv_microbench/data/` (`measurements.csv`, `range_measurements.csv`, `meta.json`, `server*.log`), `idle_kv.png` | `experiments/idle_kv_microbench/run.log`, `run_range.log` |
| Phase 1 reference (smoke) | `phase1/runner.py` | `results/t4_load_v1_part_b/t4_load_v1_poisson5s_n4_ctx32k_seed2/` | – |
| Phase 1 reference (500 tasks, R2) | `phase1/runner.py` | `results/t4_full500_v1_part_a/t4_full500_v1_c2_n4_poisson5s_ctx32k_seed2/` | – |
| Solo-vs-FIFO comparison (section 9) | computed ad hoc from the Study A and baseline CSVs above | – | – |

The Exp 3 walkthrough page is a private claude.ai artifact (https://claude.ai/artifact/D75dSYcfCyFpg7K5kEZ5Ak); its
HTML is not in the repository.

## Open items

0. Exp 4 follow-ups: reserve less than the worst case (an estimated budget that grows as tokens are produced, with
   preemption or swap on overrun) to close the gap to the oracle; cost-based stay at tool calls (cut the 2.5×
   prefill); a starvation guard (tail); sub-capacity load; 500-task set.
1. Use Study C in the scheduler: keep the occupied range low (evict idle caches at the top of the pool; stay only
   for short expected idle), and count the per-step slowdown when deciding stay vs evict.
2. Memory-aware admission with a higher concurrency cap (6–8) and backfill: the most likely throughput win. Study C
   adds a caveat: more running requests also widen the occupied range, so throughput gains flatten as the pool fills.
3. Keep the shared system-prompt prefix when a task finishes.
4. Task-level priority (tokens served per task, with aging) instead of per-request.
5. Sub-capacity loads (2.5–3.8 tasks/min) for SLO comparisons; the 500-task workload before any conclusion.
6. Solo latency for all 500 tasks (retain mode, one rep, ~3.3 h).
7. Explain the remaining partial cache reuse on resume.
