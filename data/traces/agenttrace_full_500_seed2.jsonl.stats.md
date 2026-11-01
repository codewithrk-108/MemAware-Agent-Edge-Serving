# Full-run AgentTrace dataset: 500 original problems

File: `agenttrace_full_500_seed2.jsonl`. Seed: 2. SHA-256: `9131e06eb8915b466e91a608d7f135634373d59dd9ccb84578385065ab30573b`.

Complete trajectories are unchanged. Each original problem occurs once, even when the source has multiple model variants.

## Task mix

| Steps | Tasks |
|---|---:|
| 1 | 165 |
| 2 | 230 |
| 3 | 60 |
| 4-5 | 28 |
| 6-11 | 17 |

Classes: {'mbpp': 350, 'nl2bash': 150}. Planned model requests: 1068. Tool waits: 568.

## Full logical input per model request

| Tokens | Requests | Share |
|---|---:|---:|
| 0-1023 | 0 | 0.00% |
| 1024-2047 | 646 | 60.49% |
| 2048-4095 | 192 | 17.98% |
| 4096-8191 | 178 | 16.67% |
| 8192-16383 | 52 | 4.87% |
| 16384+ | 0 | 0.00% |

Summary — min: 1077.00, median: 1561.50, p95: 8096.80, max: 16062.00, mean: 2741.54.

## Requested output per model request

| Tokens | Requests | Share |
|---|---:|---:|
| 0-255 | 23 | 2.15% |
| 256-511 | 220 | 20.60% |
| 512-1023 | 323 | 30.24% |
| 1024-2047 | 258 | 24.16% |
| 2048-4095 | 178 | 16.67% |
| 4096-8191 | 62 | 5.81% |
| 8192-16383 | 4 | 0.37% |
| 16384+ | 0 | 0.00% |

Summary — min: 35.00, median: 960.00, p95: 4466.75, max: 14875.00, mean: 1490.58.

## Interpretation and execution

First-step inputs remain mostly near 1.1K tokens; long inputs come from later context growth. No sub-1K prompt stratum exists in this source. Input/output percentages describe model requests, not tasks.

Tool delays are copied from the controlled source (150×, capped at 10s): min: 0.005, median: 0.202, p95: 4.460, max: 10.000, mean: 1.315 seconds. Keep runner delay scale at 1.

Use this file with `phase1.runner` and explicitly set `--n-tasks 500 --seed 2`, plus the chosen arrival rate/concurrency/KV settings. Reuse the same file and arrival seed across policies. The default runner task count is smaller than 500.

Set full-run timeouts explicitly rather than inheriting the short CLI defaults. The smoke configurations used `RunConfig(deadline_s=14400, request_timeout_s=900)` and `require_kv_metrics=True`; validate those settings against the intended full-run load before launch.

Existing instrumentation writes `requests.csv` (TTFT, TPOT, request duration, budgets, computed/reused tokens, errors), `tasks.csv` (completion, accumulated queueing, tool time, steps, outcome), `timeline.csv`, `admission_checks.csv`, process/GPU memory CSVs, and `kv_memory.csv` when the instrumented runtime is used. Require KV metrics for full runs.

This builder creates a workload only; it does not launch inference. Semantic task-quality evaluation remains separate.
