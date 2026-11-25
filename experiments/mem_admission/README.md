# Experiment 4: memory-aware admission, max-count backfill, no concurrency cap, fixed output caps

> **Implementation deviation found after the runs (2026-11-19).** The approved design reserves
> `history + cap` per chunk and uses the true output length only to know when a step is finished. The code
> (`chunk_len = min(cap, remaining)` in `scheduler.py`, used in `new_need` and for continuations) reserves
> `history + min(cap, true remaining output)`. Every request whose remaining output was below the cap therefore
> reserved its **exact true need (oracle)**; only longer requests were capped and continued. Consequences:
> - **cap 8,192 is exactly the oracle budget (option A)**: every output in this set is ≤ 7,471 tokens;
> - caps 1,024–4,096 are "oracle below the cap, capped and continued above it";
> - the cap analysis in lab log section 13 (`need = prompt + cap`, e.g. ~2.8 requests at cap 8,192) was **not**
>   what these runs tested.
> All numbers in "Results (50 tasks, one run per cap)" are valid for that `min(cap, true remaining)` reservation.
> The trend "larger caps do better" reflects fewer continuations, not the cost of reserving more.
> **The corrected runs with true `history + cap` reservation are in "Corrected runs" below; they lose the latency
> gains.**

## Question

Can FIFO N4 be beaten by running **as many requests as fit in the KV pool**, choosing which waiting requests to
start so that the most of them fit, with each request's reservation bounded by a **fixed output cap** instead of its
true output length?

## Design (approved before running; lab log sections 12–14)

Implementation: [`scheduler.py`](scheduler.py). Runner: [`run_caps.sh`](run_caps.sh). Comparison: [`compare.py`](compare.py).

- **No concurrency limit.** Requests run whenever their reservations fit; the server has 32 slots so slots never
  limit concurrency (the pool limits it to ~28 at most).
- **Reservation as implemented:** `need = current history + min(cap, remaining output)` (see the deviation note
  at the top: the approved design was `history + cap`);
  `free = 32,768 − 512 (margin) − reserved`, where reserved = running reservations + cells held by waiting
  continuations. Reserving the worst case of each chunk means the pool cannot overflow.
- **Fixed output cap** per admission: 1,024, 2,048, 4,096 or 8,192 tokens (≈ p50, p75, p90–95 and above the
  maximum of output length on the 50-task set; lab log section 13). *Intended:* the true remaining output is used
  only to know when a step is finished. *As implemented:* it also bounds the reservation (deviation note above).
- **Overrun rule (continuation):** if a step has output left after a chunk, its cache stays in its slot (counted as
  reserved) and it waits for another `min(cap, remaining)` cells.
- **Dispatch on every event** (arrival, chunk finished, tool returned):
  1. waiting continuations first, smallest extra need first; while any continuation is waiting and does not fit,
     no new request starts;
  2. then new requests, **smallest need first** (packing in ascending need maximizes how many fit), ties by ready
     time;
  3. deadlock breaker: if nothing is running and no waiting continuation fits, discard the one holding the fewest
     cells (it recomputes later).
- **No starvation guard** (user decision).
- **Tool call:** the slot is erased (discard); the next step re-prefills its whole history and joins the queue as a
  new request.

## Assumptions

- Same workload and server settings as the other smoke runs: 50-task trace, Poisson mean 5 s, seed 2, Qwen3-0.6B Q8_0,
  f16 KV, unified 32K pool, `--cache-ram 0`, no mmap, greedy, `ignore_eos`, tool delays at scale 1, 21,600 s deadline,
  1,800 s request timeout. Only `-np` differs (32).
- **A waiting continuation blocks all new admissions, with no backfill.** If several continuations wait and only
  some fit (for example, one is started and the space left is smaller than the next one's extra need), no new
  request starts, even one small enough for the leftover space. The leftover cells stay empty until running
  requests free enough for the next continuation. With a fixed cap every continuation needs the same extra cells,
  so once one does not fit, none does. Reason: a continuation already holds its cache in the pool; if small new
  requests kept taking freed cells it might never collect its extra reservation while holding memory, pushing the
  pool towards deadlock. Cost: up to `extra − 1` cells (≤ 2,047 at cap 2,048) can sit unused while a continuation
  waits, which contributes to the gap between reserved and used KV. (`scheduler.py`, `dispatch`:
  `blocked = bool(self.cont_q)` skips the new-request step.) Clarified to the user on 2026-11-19; not varied in
  any run. Backfilling new requests into that leftover space is a possible variant, not tested.
- Caps were chosen from the same 50 tasks they are tested on (in-sample).
- The workload is never truncated: every request generates its full recorded output, possibly over several chunks.

## Check before the runs

6 tasks, burst arrival, cap 1,024: 6/6 completed; continuations reused their cache (1 token recomputed); new steps
re-prefilled fully as intended; up to 6 requests ran at once; **peak actual KV 19,608 ≤ peak reserved 19,800**.

Two implementation bugs were hit on the way: the `free_before` log field double-counted the admitted request's
reservation (fixed before any run), and the first launch crashed with `TypeError: unhashable type: 'Job'` because a
default dataclass is not hashable (fixed with `eq=False`, which also makes list removal use identity).

## Results (50 tasks, one run per cap): first runs, reservation = min(cap, true remaining output)

From [`results/mem_admission/compare_smoke.md`](../../results/mem_admission/compare_smoke.md):

| | Baseline FIFO N4 | Exp 2 | Exp 3 MLFQ | **Exp 4, cap 1,024** | **cap 2,048** | **cap 4,096** | **cap 8,192 (= oracle)** |
|---|---:|---:|---:|---:|---:|---:|---:|
| Completed | 46/50 | 50/50 | 46/50 | **50/50** | **50/50** | **50/50** | 50/50 |
| Overflow events | 1 | 0 | 1 | 0 | 0 | 0 | 0 |
| Tasks/min | 3.34 | 3.04 | 2.19 | 3.10 | 3.15 | 3.16 | 3.19 |
| Output tokens/s | 202 | – | – | 186 | 189 | 189 | 191 |
| Latency mean (s) | 258 | 357 | 469 | 254 | 239 | **214** | **205** |
| Latency p50 (s) | 225 | 322 | 408 | 162 | 131 | **95** | 102 |
| Latency p95 (s) | **532** | 647 | 1,012 | 729 | 695 | 678 | 667 |
| Slowdown vs solo p50 / p95 | 13.4× / 42.1× | 16.8× / 82.9× | 24.2× / 45.7× | 8.9× / 18.3× | 7.7× / 15.2× | **6.8× / 12.7×** | **6.7× / 12.1×** |
| Within 5× / 10× / 20× of solo | 12 / 30 / 68% | 18 / 38 / 58% | 8 / 16 / 34% | 12 / 64 / 94% | 22 / 74 / 96% | 20 / **84 / 100%** | 24 / **86 / 100%** |
| Latency p50 by steps 1 / 2 / 3+ (s) | 134 / 283 / 502 | 325 / 246 / 539 | 482 / 263 / 826 | 68 / 211 / 591 | 66 / 69 / 581 | 61 / 84 / 552 | 67 / 78 / 544 |
| Ready-queue wait p50 / p95 per task (s) | 194 / 417 | – | – | 70 / 437 | 66 / 432 | 29 / 413 | 29 / 399 |
| Mean / peak running requests | 3.7 / 4 | 3.7 / 4 | 3.6 / 4 | 5.7 / 13 | 5.4 / 11 | 5.1 / 11 | 5.0 / 10 |
| Time per output token p50 (ms) | 16.3 | 19.8 | 23.0 | 30.2 | 28.5 | 26.6 | 26.0 |
| First token of a step p50 (ms) | 141 | 520 | 190 | 1,661 | 1,282 | 1,157 | 1,255 |
| Mean KV, all / active (%) | 49.9 / 49.6 | 57.4 / 51.5 | 79.6 / 47.1 | 77.3 / 76.2 | 73.0 / 72.9 | 66.7 / 67.5 | 65.4 / 66.1 |
| Prompt tokens computed | 155,777 | 136,207 | – | 390,219 | 394,071 | 387,320 | 387,321 |
| Continuations (steps needing one) | – | – | – | 114 (55) | 41 (30) | 10 (10) | 0 (0) |
| Admissions that overtook an older request | – | – | – | 86 | 88 | 87 | 90 |
| Longest wait (s) | – | – | – | 548 | 581 | 595 | 605 |
| Peak reserved / peak actual KV (cells) | – | – | – | 32,239 / 31,361 | 32,229 / 32,165 | 32,254 / 28,882 | 32,205 / 30,987 |

### What worked

- **Every task completed and the pool never overflowed** at any cap. Actual KV never exceeded the reservation:
  checked at every 1-second KV sample against the reservation in force at that moment (884–892 samples per run,
  0 violations, all four caps). The accounting is exact when no idle caches exist.
- **Typical latency dropped sharply.** Median task latency fell 28% (cap 1,024), 42% (2,048) and **58% (4,096)**
  below the baseline; mean latency fell up to 17%. Median slowdown against solo fell from 13.4× to 6.8×, p95 from
  42.1× to 12.7×. Tasks within 10× of solo: 30% → 84%; within 20×: 68% → 100%.
- **One-step tasks gained most** (median 134 s → 61–68 s): smallest-need-first packing behaves like
  shortest-job-first.
- **Larger caps did better on almost every latency measure** (cap 8,192 best on mean, p95, slowdown and SLO; its
  median, 102 s, is within run-to-run noise of cap 4,096's 95 s). Because of the deviation note, a larger cap here
  means fewer continuations (114 → 41 → 10 → 0), not a larger reservation; a waiting continuation blocks every new
  request, so fewer continuations means fewer stalls.

### What did not

- **Throughput did not rise** (3.10–3.16 vs 3.34 tasks/min; output 186–189 vs 202 tokens/s), even though mean
  concurrency rose from 3.7 to 5.1–5.7. Two reasons, both measured:
  1. *Slower decoding with more requests and a fuller pool:* time per token 26.6–30.2 ms vs 16.3 ms. Active KV rose
     from ~50% to 67–76%, and Study C shows each step pays ~0.45 ms per 1K cells of occupied range.
  2. *2.5× more prefill:* discarding the cache at every tool call made each returning step recompute its whole
     history (390K prompt tokens vs 156K), which also raised first-token time to 1.2–1.7 s.
  The baseline's 3.34 tasks/min also counts only 46 tasks: it dropped 4 long ones, so it did 7% less output work
  (166K vs 180K tokens).
- **Tail latency got worse** (p95 678–729 s vs 532 s; longest wait 548–595 s). With no starvation guard, large
  requests wait while smaller ones keep being admitted. The longest waits were steps that re-prefill a long history
  after a discard (e.g. a 9,025-token history with a 4,096-token chunk: need 13,121 cells, waited 595 s).
- **Tasks with 3+ steps were slower** (median 552–591 s vs 502 s): each of their returning steps has a large need
  (full history) and is admitted late.

## Corrected runs: true fixed-cap reservation (`history + cap`)

After the deviation was found, the user instructed: do not use the output length at all; admission must only check
whether `prompt + cap` fits. `scheduler.py` now reserves `history + cap` for new requests and `held cells + cap` for
continuations (`--reserve cap`, the default; `--reserve min_true` reproduces the runs above). The true output length
is used only as each chunk's generation length, to stop where the recorded step ended (standing in for
end-of-sequence). A 6-task check confirmed every new request reserved exactly `prompt + cap`. Runs:
`results/mem_admission/smoke_exp4fc_cap{1024,2048,4096,8192}/`, `run_fixed_caps.sh`, log
`results/mem_admission_fixed_caps.log`.

| | Baseline FIFO N4 | **Fixed cap 1,024** | **2,048** | **4,096** | **8,192** | Oracle (cap 8,192 above) |
|---|---:|---:|---:|---:|---:|---:|
| Completed | 46/50 | 50/50 | 50/50 | 50/50 | 50/50 | 50/50 |
| Overflow events | 1 | 0 | 0 | 0 | 0 | 0 |
| Tasks/min | **3.34** | 3.00 | 3.18 | 3.16 | 3.03 | 3.19 |
| Output tokens/s | 202 | 180 | 190 | 189 | 181 | 191 |
| Latency mean / p50 / p95 (s) | **258 / 225 / 532** | 300 / 283 / 752 | 286 / 257 / 695 | 293 / 268 / 709 | 335 / 319 / 718 | 205 / 102 / 667 |
| Slowdown vs solo p50 / p95 | 13.4× / 42.1× | 11.9× / **23.7×** | **11.3×** / 31.6× | 12.5× / 35.2× | 15.6× / 47.6× | 6.7× / 12.1× |
| Within 5× / 10× / 20× of solo | 12 / 30 / 68% | 10 / 42 / **84%** | 12 / **48** / 80% | 16 / 42 / 72% | 12 / 34 / 66% | 24 / 86 / 100% |
| Latency p50 by steps 1 / 2 / 3+ (s) | 134 / **283** / **502** | 96 / 286 / 642 | 96 / 301 / 591 | **73** / 309 / 594 | 92 / 351 / 627 | 67 / 78 / 544 |
| Ready-queue wait p50 / p95 per task (s) | 194 / 417 | 165 / 471 | 171 / 453 | 201 / 493 | 290 / 589 | 29 / 399 |
| Mean / peak running | 3.7 / 4 | 5.3 / 13 | 4.8 / 10 | 3.6 / 6 | 2.2 / 3 | 5.0 / 10 |
| Mean KV reserved / actually used | – | 87% / 76% | 86% / 66% | 86% / 52% | 77% / 31% | – / 65% |
| Time per output token p50 (ms) | 16.3 | 29.2 | 24.2 | 18.6 | 11.3 | 26.0 |
| First token of a step p50 (ms) | 141 | 1,795 | 1,191 | 897 | 406 | 1,255 |
| Continuations | – | 114 | 41 | 10 | 0 | 0 |
| Dispatches blocked by a waiting continuation | – | 27% | 15% | 6% | 0% | – |
| Longest wait (s) | – | 586 | 580 | 613 | 707 | 605 |
| KV samples above reservation | – | 0 / 931 | 0 / 884 | 0 / 894 | 0 / 966 | 0 / 886 |

### What the corrected runs show

- **Safety holds without the oracle:** 50/50 at every cap, no overflow, actual KV never above the reservation.
- **The latency gains were the oracle's.** Without it, median latency is *worse* than FIFO N4 at every cap
  (257–319 s vs 225 s), mean latency too (286–335 s vs 258 s), and p95 is worse (695–752 s vs 532 s).
- **Throughput is 5–10% below FIFO N4** (3.00–3.18 vs 3.34 tasks/min; 180–190 vs 202 output tokens/s).
- **Some relative measures still improve** because failures count against FIFO N4 and short tasks go first:
  median slowdown 11.3–12.5× vs 13.4× (caps 1,024–4,096), p95 slowdown 23.7–35.2× vs 42.1×, tasks within 10× of
  solo 42–48% vs 30%, within 20× 72–84% vs 68%; one-step tasks finish in 73–96 s vs 134 s. Two-step and 3+-step tasks
  are slower than in FIFO N4.
- **Why it loses:** worst-case reservation wastes the pool. Reserved KV averages 86% while only 52–76% is used
  (caps 1,024–4,096); at cap 8,192 only 31% is used and at most 3 requests run at once. Small caps pack more requests
  but create many continuations (114 at cap 1,024) that block new admissions 27% of the time and slow decoding
  (29.2 ms per token with 5.3 running). Discarding the cache at every tool call also keeps prefill at ~2.5× the
  baseline (387–393K vs 156K prompt tokens).
- **Best fixed cap: 2,048** (≈ p75 of output length): highest throughput (3.18) and best median and p50 slowdown
  among fixed caps, but still behind FIFO N4 on raw latency and throughput.

### Revised next steps

1. Reserve less than the worst case and handle the rare overrun (e.g. reserve a running estimate such as p50 of
   observed output, grow the reservation as tokens are produced, and preempt or swap on overrun).
2. Keep the cache through short tool waits (cost-based stay) to remove the 2.5× prefill.
3. A starvation guard for the tail.
4. Sub-capacity load and the 500-task set.

## Interpretation (first runs, reservation = min(cap, true remaining output))

Memory-aware admission removes overflow entirely and, through smallest-first packing, sharply cuts typical latency
and slowdown. It does not raise throughput on this GPU, because more concurrent requests make each decode step slower
(a wider occupied range) and discarding at tool calls more than doubled prefill. The cost falls on large and
multi-step requests.

## Next steps suggested by these results

1. Keep the cache through short tool waits instead of always discarding (the cost-based stay rule), to cut the 2.5×
   prefill and the large needs of returning steps.
2. A starvation guard or aging to bound the tail (p95, longest wait).
3. Re-run caps 1,024–8,192 with the reservation the design intended (`history + cap`), to measure true fixed-cap
   admission; the current runs are an oracle-below-cap variant.
4. Run at a sub-capacity load and on the 500-task set (caps chosen in-sample here).

## Experiment 5: Exp 4 fixed cap 2,048 plus a starvation guard (E5a 60 s, E5b 120 s)

**Why:** the fixed-cap Exp 4 runs had the worst tail of all schedulers (p95 695–752 s, longest wait 580–707 s).
Smallest-need-first packing keeps passing over large requests, often long histories re-prefilled after a discard.
The user had removed the starvation guard from Exp 4 and asked for it back, at 60 s and 120 s, with the procedure
approved before running.

**Design (approved 2026-11-19).** Everything is Exp 4 fixed cap 2,048 (`--reserve cap`), plus:

- **Starvation clock = per-request queue wait:** `now − ready time`, where ready time is the task's arrival for step
  1 and the tool's return for later steps (also the time a deadlock-discarded continuation re-queues). Tool delays
  and running time do not count; the clock restarts for every step. A new request with wait ≥ T is *starving*.
  Rejected alternative (discussed with the user): end-to-end task age. With a median task time of ~257 s, almost
  every multi-step task would be starving after a step or two, turning the guard into FIFO by task arrival.
- **Dispatch order:**
  1. waiting continuations, smallest extra need first (unchanged; still first, still block every new request while
     one waits; Assumptions above);
  2. starving new requests, oldest ready time first; if the oldest starving request does not fit, **no other new
     request is admitted** (head-of-line block, no backfill: safe backfill would need to know when space frees,
     i.e. output lengths); a starving request whose need exceeds the whole pool would never block (none expected);
  3. only when no request is starving: the other new requests, smallest need first (unchanged);
  4. deadlock breaker (unchanged).
- **Why continuations stay above starving requests:** they already hold cells; making them wait leaves those cells
  occupied and idle, and finishing them is what frees space. Each lasts at most one 2,048-token chunk (41 in the
  whole Exp 4 cap 2,048 run), so the delay they add to a starving request is short. The alternative (starving above
  continuations) gives a firmer bound but idles held cells and needs the deadlock breaker in more cases; not run.
- Starvation is checked at dispatch events (arrival, chunk finished, tool returned), so a request is promoted at the
  first event after it crosses T (events are seconds apart under this load).
- **Task time is unchanged:** finish of the last step − task arrival, end to end (all queue waits, prefill, decode,
  continuation waits, tool delays). The guard's per-request wait is reported in addition.
- **New outputs:** `requests.csv` column `starving` (admitted as starving); `events.csv` rows `starving` (one per
  promotion); `reservations.csv` columns `starving`, `blocked_by_starving`; summary `starving_promotions`,
  `starving_admissions`, `new_wait_p50_s`, `new_wait_p95_s`, `starve_blocked_s` (total time new admissions were held
  by the guard).
- **Code:** `scheduler.py --starve-s T` (off by default, so Exp 4 is reproduced without it); runner
  [`run_starvation.sh`](run_starvation.sh) → `results/mem_admission/smoke_e5_cap2048_starve{60,120}/`, log
  `results/mem_admission/run_e5.log`.

**Checks before the runs** (`results/mem_admission_checks/`, not part of the results):
- 8 tasks, burst arrival (rate 5/s), T = 5 s: everything fit at once (peak 8 running, 27,189 reserved), no request
  ever waited, so the guard never fired. Not a test of the guard.
- 20 tasks, burst, T = 5 s: 20/20 completed, 24 promotions, all 24 later admitted as starving, guard blocking time
  99 s, longest new-request wait 59.7 s, peak reserved 32,109 of 32,256, peak actual KV 24,301 cells, no overflow, no
  deadlock discard.

**Results (50 tasks, Poisson mean 5 s, seed 2, one run each; runs finished 2026-11-19):**

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

Values from `results/mem_admission/compare_smoke.{csv,md}` and each run's `requests.csv`; "new requests" exclude
continuations (154 chunk requests − 41 continuations = 113).

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

## Experiment 6: Exp 4 cap 2,048 ordered by a class × step length lookup (τ ≈ 0.33)

**Why:** the Exp 4 oracle's low median (102 s) came from exact reservations plus shortest-job-first ordering. This
run tests the ordering part with a real, weak length ranking. Design and context: lab log section 23.

**Algorithm:** Exp 4 fixed cap 2,048 unchanged, except waiting new requests are sorted by predicted output length
(median of task class × step 0 / 1 / 2+ over 2,283 calibration requests, [`length_lookup.json`](length_lookup.json):
mbpp 2,048 / 737 / 834, nl2bash 748 / 651 / 778), then by need, then by ready time. Kendall τ-b on the 113 test
requests 0.33. Code: `scheduler.py --order lookup`; run `results/mem_admission/smoke_e6_cap2048_lookup/`, log
`results/mem_admission/run_e6.log`.

**Results (50 tasks, one run):**

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

### E6 on the 500-task set vs the Phase 1 FIFO N4 baseline

Same workload as the Phase 1 baseline (`t4_full500_v1_c2_n4_poisson5s_ctx32k_seed2`), lookup refitted on the 287 tasks
whose problems are not in the 500-task set (`length_lookup_full500.json`, τ-b 0.32). Comparison script
[`compare_full500.py`](compare_full500.py). Details and reasoning: lab log section 24.

Results (`results/mem_admission/compare_full500.{csv,md}`, run 129 min):

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

## Files

`results/mem_admission/smoke_exp4_cap{1024,2048,4096,8192}/`: `tasks.csv`, `requests.csv` (one row per chunk: reservation,
free cells at admission, running count, wait, overtakes, continuation flag), `reservations.csv` (running / waiting /
reserved after every dispatch), `dispatches.csv`, `events.csv` (deadlock discards; none), `timeline.csv`,
`kv_memory.csv`, `gpu_memory.csv`, `memory.csv`, `meta.json`, `server.log`. Run logs: `results/mem_admission_smoke.log` (caps 1,024–4,096) and
`results/mem_admission_smoke_cap8192.log` (gitignored).

```bash
export MODEL=/path/to/Qwen3-0.6B-Q8_0.gguf LLAMA_SERVER=/path/to/instrumented/llama-server
experiments/mem_admission/run_caps.sh        # ~65 min: caps 1,024, 2,048, 4,096, 8,192, then the comparison
```
