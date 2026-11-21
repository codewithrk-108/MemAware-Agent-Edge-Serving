# Phase 2 matched T4 smoke design

## Question

Does the Phase 2 memory-aware two-queue scheduler reduce KV-context failures and improve completed work without sacrificing most of the tuned FIFO throughput?

## Matched configuration

- Host: AWS NVIDIA Tesla T4, 15,360 MiB.
- Runtime: pinned instrumented llama.cpp b11780.
- Model: Qwen3-0.6B Q8_0 with f16 KV.
- Workload: the fixed 50-task AgentTrace seed-2 trace used in Phase 1 S3.
- Arrivals: burst; all tasks ready at time zero.
- Concurrency: `N=4`.
- KV pool: 32,768 unified cells.
- Prompt reuse: enabled; host prompt cache: disabled.
- Tool delays, output budgets, task seed, deadline, and request timeout: identical across policies.

The test reruns Phase 1 FIFO and Phase 2 on the same host and binary. The historical Phase 1 S3 reference for this point is 46/50 completed at about 3.38 completed tasks/min.

## Phase 2 smoke policy

- Logical need: prompt tokens plus output-token budget.
- KV safety margin: 512 cells.
- Aging threshold: 30 seconds.
- Backfill guard: 8 seconds, based on the development estimator's p95 underprediction.
- Tool-return reserve: 256 cells per predicted near-term return, capped at 4,096 cells over a five-second horizon.
- Tool timing: estimated headline mode, not exact/oracle trace delay.
- Cache eviction: disabled for this smoke.

These are development smoke settings, not the final frozen evaluation policy.

## Metrics and decision rule

Primary metrics:

- completed tasks and completion rate;
- completed tasks/min;
- KV-context failures;
- successful-task p50/p95 completion latency;
- p95 and maximum ready-queue wait;
- physical KV peak and mean occupancy.

Secondary diagnostics:

- FIFO-head and backfill admissions;
- blocked/aged-head decisions;
- runtime-prediction error and scheduler overhead;
- generated-token equality and identical task/output budgets.

Phase 2 is promising in this smoke if it reduces FIFO KV failures/completion loss and retains at least 90% of matched FIFO completed-task throughput. The result is not a final performance claim; it decides whether to proceed to development ablations and policy freezing.
