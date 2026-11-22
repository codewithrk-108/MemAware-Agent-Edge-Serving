# Agent Handoff: Phase 2 Memory-Aware Scheduling

## Objective and current state

This project extends the Phase 1 request-level FIFO replay system for multi-step LLM agents. The goal is to improve the completion/throughput/latency frontier under a fixed llama.cpp unified KV pool: maximize successful task throughput, prevent KV-context failures, preserve useful cached prefixes, and keep queue latency bounded.

The active branch is `phase2-two-queue`. The current implementation is complete through full 500-task AgentTrace R4 and R5 experiments. All raw artifacts, logs, archives, comparisons, and plots are committed and pushed. The working tree was clean at the last handoff check.

## Phase 1 baseline

Phase 1 uses plain request FIFO with fixed maximum concurrency `N`. It dispatches the FIFO head whenever a worker is available; its memory fit calculation is diagnostic and does not prevent dispatch. llama.cpp uses a unified f16 KV pool, so KV is shared rather than divided equally among slots.

Important 500-task baselines:

| Run | Configuration | Success | Throughput | p50 | p95 |
|---|---|---:|---:|---:|---:|
| R1 | N=1, Poisson 10s, 32K | 500/500 | 2.70/min | 4,029s | 7,351s |
| R4 | N=4, Poisson 2s, 32K | 472/500 | 4.26/min | 3,984s | 5,420s |
| R5 | N=16, Poisson 2s, 32K | 100/500 | 5.55/min | 127s | 270s |

R5 latency and throughput are strongly survivor-biased: 400 tasks fail with KV-context errors and leave the system. The latency values include only 100 survivors.

## Optimizations implemented over FIFO

1. **Central memory-aware dispatcher.** Admission decisions are atomic rather than independently made by workers.
2. **Logical KV reservations.** Active requests reserve prompt plus output-budget cells; physical KV samples validate the policy offline.
3. **Authoritative memory-aware admission.** A request waits instead of being dispatched when it would exceed the safe KV budget. This is the main source of reliability improvement.
4. **Computed backfill view.** FIFO remains authoritative, but younger requests may run when the head does not fit and the candidate fits both memory and runtime allowance.
5. **Aging and starvation protection.** Old heads close the backfill window. Tested thresholds are 30 and 360 seconds with an 8-second guard.
6. **Runtime prediction.** A frozen estimator predicts request time from prompt and output size. Headline experiments use estimated timing; oracle timing is diagnostic only.
7. **Tool-return reserve.** Bounded logical capacity is retained for agents likely to return from tool waits.
8. **Explicit slot ownership.** Requests use their retained slot or an unowned compatible slot, avoiding incorrect cache-to-task assignment.
9. **Slot-aware cache accounting and eviction.** Idle caches are erased through llama.cpp's slot endpoint; reclaimed physical KV is verified.
10. **500ms cache-preservation grace.** A slot is temporarily protected when its owner enters a short tool call, avoiding immediate cache destruction.
11. **Adaptive concurrency.** Grace-protected idle slots are temporarily excluded from eligible workers. Active concurrency therefore varies from zero to configured `N`.
12. **Resume priority and fairness controls.** Implemented unrestricted resume, one-resume/one-FIFO quota, and optional head-age cutoff.
13. **Cost-aware dynamic resume.** A within-grace return receives priority when estimated avoided prefill time exceeds an age- and queue-pressure-weighted delay cost.
14. **Detailed telemetry.** Results include queue state, admission reason, backfill, reservations, physical KV, grace holds/hits, resume score, eviction verification, recomputation, latency, and throughput.

## What produced the largest gains

### 1. Memory-aware admission plus slot-safe eviction: largest reliability gain

The strongest result is failure elimination. On R5, FIFO completed only 100/500; both Phase 2 policies completed 500/500 with zero KV-context failures. On R4, FIFO completed 472/500; Phase 2 completed 500/500. This is the clearest and largest improvement over Phase 1.

The cost is lower effective concurrency and longer queueing. R5 FIFO runs near N=16 and fails quickly; Phase 2 safely sustains about N=5.5 and waits for every task to complete.

### 2. Cache grace and resume priority: largest cache-recomputation gain in smoke tests

The 500ms grace period prevented short tool waits from causing immediate slot reassignment. In the matched Poisson-5s smoke test, unrestricted adaptive scheduling completed 50/50 versus FIFO's 46/50, improved throughput by 6.1%, reduced prefill work by 30.7%, and produced 38 actual grace hits. Its p95 increased by 22.2%, motivating fairness and cost-aware resume work.

### 3. Cost-aware resume: best successful smoke and R4 policy

On the 50-task Poisson smoke, cost-aware age 30 completed 50/50 at 3.394 tasks/min with p50/p95 279/640 seconds. It outperformed fixed one-resume/one-FIFO quota on throughput, p50, p95, cache hit, and prefill work. The score was permissive: 39 of 40 returns received priority.

On full R4, cost-aware scheduling eliminated 28 FIFO failures, retained 98.6% of FIFO throughput, and improved p50 by 12.2%. Its p95 was 16.2% worse.

### 4. Aging 360: clear backfill gain, modest end-to-end gain

At N=16, increasing aging from 30 to 360 seconds raised backfills from 38 to 135. It improved p50 by 4.5%, p95 by 1.8%, average queue length by 6.7%, and throughput by 0.5%. It reduced cache-hit ratio by 1.7 points and increased recomputed prompt tokens by 4.8%. Backfill works, but eviction/recomputation offsets much of its benefit.

## Current headline results

### Full R4: N=4, Poisson mean 2s, 32K, 500 tasks

| Metric | FIFO R4 | Cost-aware age 30 |
|---|---:|---:|
| Success | 472/500 | **500/500** |
| KV failures | 28 | **0** |
| Throughput | **4.260/min** | 4.200/min |
| p50 | 3,984s | **3,499s** |
| p95 | **5,420s** | 6,299s |
| Peak KV | 99.9% | 92.5% |
| Average KV | — | 48.7% |
| Average ready queue | — | 225.2 |
| Backfills | diagnostic only | 0 |

Conclusion: strong Pareto improvement in success and median latency with almost unchanged throughput, but worse tail latency.

### Full R5: N=16, Poisson mean 2s, 32K, 500 tasks

| Metric | FIFO R5 | Cost-aware age 30 | Cost-aware age 360 |
|---|---:|---:|---:|
| Success | 100/500 | **500/500** | **500/500** |
| KV failures | 400 | **0** | **0** |
| Throughput | 5.550/min* | 4.166/min | **4.186/min** |
| p50 | 127s* | 3,218s | **3,074s** |
| p95 | 270s* | 6,275s | **6,164s** |
| Average queue | — | 222.0 | **207.0** |
| Time-weighted running N | 15.70 | 5.54 | **5.60** |
| Average KV | 77.8% | 66.5% | 66.8% |
| Peak KV | 99.9% | 96.3% | **94.9%** |
| Backfills | — | 38 | **135** |
| Cache hit | — | **45.8%** | 44.1% |
| Recomputed prompt tokens | — | **935,215** | 979,891 |

`*` FIFO R5 reports only 100 survivors; 400 failures remove load and make its latency/throughput unsuitable as a direct quality comparison.

On the exact 100 task IDs that FIFO completed, Phase 2 remains slower because it keeps all 500 tasks alive. Median accumulated ready-queue time is 97 seconds for FIFO, 3,136 seconds for age 30, and 2,823 seconds for age 360. Matching task identity does not match system load.

## Adaptive-concurrency interpretation

Configured N is a maximum, not a target. In R5, FIFO's time-weighted running N is 15.70. Phase 2 averages 5.54/5.60 because KV-safe admission blocks unsafe dispatches.

The cache-grace mechanism itself has a small direct effect on the ceiling:

- Age 30: 1,168 grace-hold decisions; 1,138 protect one slot and 30 protect two.
- Age 360: 1,213 grace-hold decisions; 1,164 protect one slot, 47 protect two, and two protect three.

Thus grace usually changes the temporary ceiling from 16 to 15. Memory-aware admission and KV/slot compatibility—not grace protection—explain most of the reduction from N=16 to actual N≈5.5.

## Known limitations and risks

1. **Conservative reservations.** Full prompt plus output budget overestimates incremental KV demand when a resident prefix can be reused.
2. **Tail latency.** Failure elimination converts fast FIFO errors into long waits. Queueing dominates full-run p95.
3. **Cost gate is too permissive at N=16.** Both R5 runs granted all 336 within-grace resume opportunities; no return was denied by cost. The formula divides queue cost by concurrency, making the gate weaker as N grows.
4. **Backfill/cache tradeoff.** Aging 360 increases backfill but also eviction and recomputation.
5. **No dynamic server slot count.** llama-server starts with fixed `-np N`; the scheduler only varies active use up to N.
6. **Eviction verification warnings.** Full R4 and both R5 runs each had one erase where aggregate physical KV release was not immediately observed. All runs remained safe and completed without context failures.
7. **Single full-run repetition.** R4 and each R5 threshold were run once. Repetitions are required for confidence intervals.
8. **FIFO latency is survivor-biased.** A truly fair latency experiment should replay only the same 100-task cohort under each policy with identical arrivals, or report failure-penalized latency over all 500 tasks.

## Recommended next work

Highest-value next experiment: improve KV utilization without losing 100% completion.

1. Replace full-context reservations with verified incremental reservations: subtract only a confirmed resident prefix and retain a safety margin.
2. Make the resume-cost formula concurrency-invariant; remove or recalibrate the division by configured N, then sweep the queue-cost weight on development runs.
3. Use a dynamic aging/backfill budget tied to head wait and measured recomputation cost rather than fixed 30/360-second thresholds.
4. Repeat R4 and R5 with at least three seeds/runs and report confidence intervals.
5. Add a fair 100-task cohort replay and a failure-penalized 500-task latency metric.
6. Consider a host KV tier only after measuring save/restore versus recomputation at 2K, 4K, 8K, and 16K contexts.

## Important artifacts

- `research.md`: implementation findings and experiment history.
- `plan.md`: architecture, decisions, todos, and completed extensions.
- `phase2/runner.py`: integrated Phase 2 scheduler and telemetry.
- `phase2/scheduler.py`: pure memory-aware FIFO/backfill selection.
- `phase2/smoke.py`: named smoke and full-run configurations.
- `phase2_result/R4_COST_AWARE_COMPARISON.md`: full R4 comparison.
- `phase2_result/R5_COST_AWARE_AGING_COMPARISON.md`: full R5 comparison.
- `phase2_result/r5_cost_aware_summary.csv`: R5 headline metrics.
- `phase2_result/r5_common_fifo_success_latency.csv`: common-task latency control.
- `phase2_result/r5_adaptive_concurrency_effect_over_time.png`: FIFO and Phase 2 N(t), including adaptive ceiling events.
- `phase2_result/runs/`: raw tasks, requests, scheduler decisions, KV/GPU samples, evictions, logs, and metadata.

## Reproduction and operations

Local validation command:

```bash
uv run pytest -q
```

Last validated suite: 107 passed, 1 skipped, 35 subtests.

For unattended EC2 experiments, the validated pattern is:

1. Launch the benchmark with `nohup` through a supervisor script.
2. Stream a dedicated supervisor log.
3. Archive the unique result group and supervisor log.
4. Record SHA-256.
5. Call `sync`.
6. Schedule `sudo shutdown -h +1`.

This stopped—not terminated—the tested instances and retained EBS artifacts across restart. EC2 workspaces do not have GitHub credentials, so retrieve artifacts after restart and push through the authenticated local checkout.

## Recent result commits

- `bde3517` — full R4 cost-aware results.
- `27e3ac2` — full R5 age-30 results.
- `7ceb6c6` — full R5 age-360 results and comparison.
- `d131e5e` — common-task latency and concurrency plot.
- `cb7b600` — adaptive-concurrency effect plot.
- `2ca1618` — FIFO added to concurrency comparison.
