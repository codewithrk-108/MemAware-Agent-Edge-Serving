# Memory-aware scheduling for multi-step agents — Phase 1

Agent tasks issue several dependent model requests, wait on tools in between, and keep growing context in a shared KV cache. This project asks whether a scheduler that anticipates memory demand and agents returning from tool calls can beat FIFO on throughput and task latency under a fixed memory budget.

Phase 1 builds the replay workload and harness, measures FIFO with fixed concurrency, and picks the baselines Phase 2 must beat. The writeup is [`report/phase1_writeup.pdf`](report/phase1_writeup.pdf).

**What is in this repository**

- **Dataset:** replayable agent tasks built from AgentTrace (MBPP and NL2Bash), with a 50-task sample for smoke and stress tests and a stratified 500-task sample for the full runs. They rebuild byte-for-byte from the committed source ([`data/README.md`](data/README.md)).
- **Harness:** an open-loop replay client and request-level FIFO scheduler in front of `llama-server`, recording per-request, per-task, queue, admission, GPU-memory and physical-KV-occupancy data.
- **Runtime instrumentation:** a read-only llama.cpp patch that exports occupied KV cells ([`runtime/`](runtime/README.md)).
- **Evaluation:** 51 runs on AWS GPUs cited by the writeup (S1–S3 smoke and stress tests, seven 500-task runs), plus a local smoke test, a KV validation run and runtime microbenchmarks ([`results/`](results/README.md)).
- **Result:** the 32K-cell KV pool, not GPU memory, limits FIFO. N=4 is the tuned FIFO baseline (94–96% completion, 4.2–4.3 tasks/min); N=1 is the zero-failure reference (2.70 tasks/min).

## Quickstart

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12. No GPU or model is needed to check the results.

```bash
uv sync
uv run pytest -q                              # unit tests + checks that the writeup numbers reproduce
uv run python -m analysis.reproduce_phase1     # recompute every table, figure and inline number in the writeup
```

`reproduce_phase1` prints the writeup's Table 1, Table 3 and all inline numbers next to the values recomputed from `results/`, and writes them with the figure to `results/reproduced/`. To rebuild the dataset as well: `./workloads/rebuild_workloads.sh` (downloads the tokenizer; takes a few seconds).

## Repository layout

| Path | Contents |
|---|---|
| `report/` | Phase 1 writeup (LaTeX source, PDF) and the script that draws its figure from the run data |
| `data/` | Source trajectories and the three replay traces, with manifests and checksums |
| `workloads/` | Trace format and builders: conversion, delay scaling, sampling, rebuild script |
| `harness/` | `llama-server` launcher, streaming client, memory, GPU and KV samplers, run metadata |
| `phase1/` | FIFO runner, experiment grid (`sweep.py`), auto-stopping launchers used on AWS |
| `runtime/` | llama.cpp b11780 KV-occupancy patch and its validation programs |
| `analysis/` | Per-run metrics, writeup reproduction, run inventory, microbenchmark plots |
| `results/` | Every run the writeup uses, plus `run_inventory.csv` |
| `micro/` | Runtime microbenchmarks M1–M6 (KV cost, prefill, batching, prefix reuse, retention, capacity) |
| `aws/` | Instance launch, bootstrap and sweep scripts |
| `docs/` | How the two-instance T4 sweeps and the 500-task runs were launched |
| `tests/` | Unit tests for scheduler, converters, launchers, samplers, and the writeup reproduction |

## Where each part of the writeup comes from

| Writeup | Data | Computed in |
|---|---|---|
| S1 feasibility (10 burst runs) | `results/phase1/aws_*`, `results/phase1/t4_smoke_*` | `writeup_numbers()` |
| S1 matched sweep (T4 550/550 vs L4 468/550), S2 light load | `results/phase1/t4_agenttrace_sweep_v5/`, `results/phase1/agenttrace_sweep_v5/` | `writeup_numbers()` |
| S3 grid, Table 1 | `results/t4_load_v1_part_a/`, `results/t4_load_v1_part_b/` | `table1()` |
| Full runs, Table 3, Figure 1 | `results/t4_full500_v1_part_a/` (R1–R3), `results/t4_full500_v1_part_b/` (R4–R7) | `table3()`, `report/fig_tradeoff.py` |
| Calibration (TPOT, tool waits, estimator, backfill, slot erase) | S3 and full runs, `data/traces/agenttrace_50_seed2.jsonl`, `results/t4_load_v1_part_*/runtime_validation.log` | `writeup_numbers()` |
| Appendix A timeline | `results/micro/`, `results/phase1/trial_fifo_6_ctx16k_n4_seed2` | – |

All functions are in `analysis/reproduce_phase1.py`; folder lists live in `analysis/metrics.py`.

## How a run works

- Tasks arrive open-loop (Poisson with a fixed seed, or all at once for burst). Each ready model request enters one FIFO queue.
- With concurrency N, whenever fewer than N requests are running the queue head is dispatched. A task releases its slot during a tool wait (its KV cache may stay resident); when the scripted tool result arrives, its next request joins the tail.
- Requests are sent as token IDs with greedy decoding, a fixed output budget and `ignore_eos`; each step appends the model's own output to the history, so context growth and prefix reuse behave as in a real agent loop.
- `llama-server` gets `-np N` and a unified KV pool (`-kvu`), so admission is decided only by the FIFO. Host prompt cache is off (`--cache-ram 0`) in all writeup runs.
- An observational admission diagnostic logs, before each dispatch, whether the head would fit the estimated remaining KV budget and whether a later request would. It never changes the FIFO order.

## Rerunning the experiments

1. Build llama.cpp b11780 with the KV-occupancy patch (CUDA for the T4: `-DCMAKE_CUDA_ARCHITECTURES=75`); see [`runtime/README.md`](runtime/README.md).
2. Download the model: `uvx --from huggingface_hub hf download Qwen/Qwen3-0.6B-GGUF Qwen3-0.6B-Q8_0.gguf --local-dir ../models`.
3. Point the harness at them if they are not next to the repo: `export LLAMA_SERVER=/path/to/llama-server MODEL=/path/to/Qwen3-0.6B-Q8_0.gguf`.
4. Run one configuration, e.g. R2:

   ```bash
   uv run python -m phase1.runner --name my_r2 --trace data/traces/agenttrace_full_500_seed2.jsonl \
     --n-tasks 500 --rate 0.2 --concurrency 4 --ctx 32768 --cache-ram 0 --deadline 21600 \
     --request-timeout 1800 --seed 2 --require-kv-metrics
   ```

   or a whole group from the grid, e.g. `uv run python -m phase1.sweep --groups full500_t4_part_a --output-group my_full500_a --require-kv-metrics` (`--dry-run` lists the runs; finished runs are skipped on resume).
5. On AWS, `aws/` launches and bootstraps an instance, and `phase1/run_split_sweep_and_stop.sh` runs a group, commits the results and stops the instance ([`docs/t4_split_sweeps.md`](docs/t4_split_sweeps.md), [`docs/full500_runs_v1.md`](docs/full500_runs_v1.md)).

On macOS the harness refuses to benchmark on battery power (`--allow-battery` overrides it for smoke tests).

## Deviations from the project plan

- **GPU instead of CPU.** Final runs use an AWS NVIDIA T4. The constraint studied is a fixed KV-cell budget in a preallocated buffer, which behaves the same way on either device; results do not cover OS memory pressure or swapping.
- **One workload source.** AgentTrace only; Exgentic traces are not yet included, and tool waits are short even after scaling.
- **Single runs.** Each configuration ran once; R1–R3 and R4–R7 ran on different T4 hosts.

## Team

<!-- TODO: add team member names before submitting -->
