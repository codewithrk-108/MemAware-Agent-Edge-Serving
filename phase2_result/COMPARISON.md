# Phase 2 matched T4 smoke result

## Outcome

The current Phase 2 policy did **not** improve the matched Phase 1 FIFO result. Both policies completed 46 of 50 tasks and failed four tasks with KV-context errors. Phase 2 retained 97.7% of FIFO throughput, satisfying the smoke's 90% throughput floor, but it did not satisfy the primary requirement of reducing failures or completion loss.

This is a useful negative result: logical reservations for active requests do not account for retained idle KV contents in llama.cpp. Both policies still filled the physical KV pool, and Phase 2 dispatched no backfills. The next development step should be slot-aware retained-cache accounting and validated cache eviction/recomputation, not parameter tuning of the current policy alone.

## Matched results

| Metric | Phase 1 FIFO | Phase 2 two-queue | Phase 2 change |
|---|---:|---:|---:|
| Completed tasks | 46/50 | 46/50 | no change |
| KV-context failures | 4 | 4 | no change |
| Completed tasks/min | 3.364 | 3.286 | -2.31% |
| Completion p50 | 390.8 s | 389.4 s | -0.37% |
| Completion p95 | 692.0 s | 727.2 s | +5.08% |
| Ready-queue p95 | 552.6 s | 589.0 s | +6.60% |
| Run span | 820.5 s | 839.9 s | +2.37% |
| Mean physical KV occupancy | 56.85% | 57.57% | +1.27% relative |
| Peak physical KV occupancy | 99.994% | 99.982% | effectively unchanged |
| Cache-hit ratio | 57.05% | 60.83% | +6.63% relative |
| Prefill tokens computed | 131,377 | 135,209 | +2.92% |

Phase 2 made 111 FIFO-head admissions, zero backfill admissions, and recorded 20 aged-head blocked decisions. Total measured scheduler decision overhead was 4.6 ms across 131 decisions, with a maximum individual decision cost of 0.068 ms; Python scheduler overhead is therefore not the cause of the performance difference.

## Validity checks

- Same T4 host, pinned instrumented llama.cpp b11780 binary, model, 32K unified KV pool, and `N=4`.
- Same 50 unique AgentTrace tasks in the same seeded replay setup.
- Same total task token budget: 240,304 tokens.
- Same burst arrivals, prompt-reuse setting, host-cache setting, tool delays, deadlines, and request timeouts.
- Both failures were exclusively llama.cpp context-capacity errors; neither run had timeouts.
- The matched FIFO result reproduces the historical Phase 1 S3 point: 46/50 and approximately 3.38 tasks/min.

The failed task identities differ because the two policies altered timing/order around tool returns, but the failure count and failure class are unchanged. Executed generated-token totals differ because failures occurred at different task steps; neither policy shortened successful responses.

## Interpretation

The current fit model subtracts logical reservations for active requests, a safety margin, and a bounded tool-return reserve. It does not subtract or explicitly own retained idle slot caches. Physical measurements show why this matters: Phase 2 still reached 32,762 of 32,768 occupied cells. Once the pool is physically full, a request can fail even when the active-reservation calculation says it fits.

The lack of backfill is also expected at this point. At `N=4`, workers normally remain occupied; when a head became logically blocked, it had already crossed the 30-second aging threshold, so the fairness rule prevented younger work from bypassing it. Backfill is more likely to matter at a higher slot count or with a longer aging threshold, but changing those parameters cannot solve untracked idle KV occupancy.

## Recommended next smoke

1. Add explicit task-to-llama.cpp-slot ownership.
2. Track retained idle cache cells separately from active reservations.
3. Validate that erasing a selected idle slot releases physical cells and that the evicted agent recomputes correctly.
4. Repeat this exact matched smoke with eviction disabled and enabled.
5. Only after the retained-cache path works, run `N=8` to expose genuine backfill opportunities.

Do not freeze the current policy based on this run.

## Artifacts

- `runs/smoke_phase1_fifo_n4_burst_50/`: raw FIFO tasks, requests, timeline, admission diagnostics, memory samples, logs, and metadata.
- `runs/smoke_phase2_two_queue_n4_burst_50/`: raw Phase 2 tasks, requests, timeline, scheduler decisions, memory samples, logs, and metadata.
- `summary.csv`: compact metric comparison.
- `smoke.log`: combined remote launcher output.
- `provenance.txt`: host, runtime/model paths, UTC timestamps, and estimator/trace checksums.
- `SMOKE_DESIGN.md`: predeclared design and decision rule.
