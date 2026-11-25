# Solo task latency (SLO ground truth)

How long each agent task takes when it runs **alone** on the GPU: no queueing, no batching with other requests.
This is the reference for SLO targets (`SLO(task) = α × solo latency`) and for slowdown under load.

## Setup

- **Tasks:** the 50-task smoke trace `data/traces/agenttrace_50_seed2.jsonl` (35 MBPP, 15 NL2Bash; 113 model
  requests, 63 tool waits). Tasks recorded from both Qwen3-0.6B and Qwen3-1.7B runs are included: the server
  always runs Qwen3-0.6B, and the two models share a tokenizer, so every task replays the same way.
- **Server:** the Phase 1 N4 configuration: Qwen3-0.6B Q8_0, f16 KV, `-c 32768 -np 4 -kvu --cache-ram 0`,
  no mmap, instrumented b11780 runtime, plus `--slot-save-path` only to enable the slot-erase endpoint.
- **Replay:** greedy decoding, `ignore_eos`, fixed output budgets from the trace, recorded tool delays at scale 1
  (150× the source, capped at 10 s), prompt reuse on, 1,800 s request timeout. Every request is pinned to slot 0.
- **Two KV modes**, interleaved on the same server so drift affects both equally:
  - **retain:** the task's KV stays on the GPU through every tool wait; the next step reuses it.
  - **evict:** the slot is erased right after every request, before the tool wait; the next step recomputes
    its whole prompt.
- **3 repetitions**, repetition-major (all tasks of rep 0, then rep 1, ...), task order shuffled per rep with
  seed 2 + rep, and the mode order alternated per task. 300 task-runs in total.

## Assumptions

- **Latency** = first request sent → last request finished, including tool waits. Nothing else is running.
- **Every task-run starts from an empty KV pool** (all slots erased), so repetitions are independent. This also
  means the shared ~1,048-token system prompt is recomputed at every task's first request; under load in
  Phase 1 it was usually reused. Solo latency is therefore slightly pessimistic, by a few hundred ms per task.
- Tool waits are sleeps of the recorded (scaled) duration; tools are never executed and answers are not scored.

## Results

All 300 task-runs completed. Files: `results/solo_SLO/smoke50_v1/{retain,evict}/` and `compare.csv`.

### Task latency

| | Retain | Evict |
|---|---:|---:|
| p50 / p90 / p95 / max (s) | 15.7 / 64.9 / 116.7 / 120.6 | 16.0 / 67.4 / 119.7 / 134.7 |
| Mean (s) | 27.1 | 28.0 |
| All 50 tasks back to back | 22.5 min | 23.3 min |
| Share in model / tool waits | 94.9% / 5.1% | 95.0% / 5.0% |

**Repetitions are very stable:** per-task standard deviation median 0.03 s (max 0.25 s), coefficient of variation
median 0.15–0.17% (max 1.3%). One repetition per task is enough for a ground truth.

### By step count (retain, mean over reps)

| Steps | Tasks | Min / median / max (s) |
|---|---:|---:|
| 1 | 17 | 2.2 / 11.0 / 33.7 |
| 2 | 21 | 8.4 / 14.4 / 42.3 |
| 3 | 8 | 16.8 / 36.5 / 120.4 |
| 4–5 | 1 | 82.9 |
| 6–11 | 3 | 48.3 / 76.5 / 120.0 |

Step count explains only 47% of the variance in solo latency (R² 0.47); total output tokens plus tool time explain
98.8% (about 8.5 ms per output token). Use a per-task SLO, and keep step-count classes for reporting only.

### Requests and tool waits

| | Retain | Evict |
|---|---:|---:|
| Request time p50 / p95 / max (s) | 7.27 / 33.8 / 54.6 | 7.51 / 34.9 / 54.4 |
| Time to first token p50 / p95 (ms) | 36 / 139 | 202 / 1,834 |
| Time per output token p50 / p95 (ms) | 6.38 / 10.07 | 6.38 / 10.06 |
| Prompt tokens computed / reused | 200K / 961K | 1.16M / 0 |

- Tool waits: 207.7 s planned, 208.0 s measured; sleeps overshoot by about 1 ms (max 10 ms).
- KV held during a tool wait: retain median 4,300 cells (max 14,736); evict 0. Peak per task: 15,406 cells
  (47% of the 32K pool).

### Retain vs evict (33 multi-step tasks)

Evicting adds a median of **0.42 s (+2.3%)** per task, mean 1.45 s, max **14.6 s (+12%)**; the cost grows with
step count (the 11-step task: 120.0 → 134.6 s). Only time to first token changes; decoding speed is identical.
Alone on the GPU, dropping the cache during tool waits is cheap.

## Comparison with the FIFO N4 baseline

Recorded in the lab log: [`../README.md`, section 9](../README.md#9-solo-runs-vs-the-fifo-n4-baseline-2026-11-18).

## Limits

- 50 tasks only; just 4 have 4 or more steps. The 500-task workload has 45 such tasks.
- Solo latency on this T4 only; other hardware needs its own measurement.

## Run

```bash
export MODEL=/path/to/Qwen3-0.6B-Q8_0.gguf LLAMA_SERVER=/path/to/instrumented/llama-server
uv run python -m experiments.solo_SLO.run_solo --reps 3          # ~2 h; resumable
uv run python -m experiments.solo_SLO.analyze                    # tasks/requests/tool_waits/per_task CSVs
```

`run_solo.py` options: `--trace`, `--n-tasks`, `--modes retain evict`, `--source-model` (filter by the model that
recorded the trajectory; default `all`). Each finished task-run is one line in `<mode>/task_metadata.jsonl`
with every request's timings and every tool wait (planned, measured, KV cells held).
