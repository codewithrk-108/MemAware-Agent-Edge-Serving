# Results

Every run here is cited by the Phase 1 writeup (`report/phase1_writeup.pdf`). `run_inventory.csv` lists all 53 runs: configuration, outcome, GPU, and where the writeup uses it. Regenerate it with `uv run python -m analysis.run_inventory`.

To recompute every table, figure and inline number in the writeup from these files:

```bash
uv run python -m analysis.reproduce_phase1      # writes results/reproduced/ (not committed)
```

## Where each writeup section's data lives

| Writeup | Folder | Runs | GPU |
|---|---|---|---|
| S1 feasibility (10 burst runs) | `phase1/aws_g6f2_*`, `phase1/aws_smoke_fifo_6_ctx12k_n4_seed2`, `phase1/t4_smoke_8_burst_ctx32k_n4_seed2`, `phase1/t4_smoke_sweep_v1/` | 10 | L4 slices, T4 |
| S1 matched Poisson-40 s sweep, S2 light load | `phase1/agenttrace_sweep_v5/` (L4), `phase1/t4_agenttrace_sweep_v5/` (T4) | 11 + 11 | L4 slice, T4 |
| S3 instrumented grid, Table 1 | `t4_load_v1_part_a/` (N = 2, 8), `t4_load_v1_part_b/` (N = 4, 16) | 12 | T4 |
| Full runs R1–R7, Table 3, Figure 1 | `t4_full500_v1_part_a/` (R1–R3 = `c1`–`c3`), `t4_full500_v1_part_b/` (R4–R7 = `c4`–`c7`) | 7 | T4 |
| Calibration: slot erase frees KV cells | `t4_load_v1_part_*/runtime_validation.log` (T4), `validation/kv_occupancy_local/` (local) | 1 | T4, Apple M4 Pro |
| Appendix A: local smoke test | `phase1/trial_fifo_6_ctx16k_n4_seed2` | 1 | Apple M4 Pro |
| Appendix A: runtime microbenchmarks | `micro/` (M1–M5, Llama 3.2 1B on an Apple M5; see `micro/summary.md`) | – | Apple M5 |

The two T4 hosts ran different parts of each experiment: `part_a` and `part_b` folders come from separate instances with identical model, runtime and task files (`provenance.txt` records the commit and file hashes).

## Files in each run folder

| File | One row per | Used for |
|---|---|---|
| `meta.json` | run | Configuration (`run`), machine and runtime (`env`), llama-server arguments, and the run summary: completed/failed tasks, latency quantiles, throughput, cache-hit ratio, prefill tokens, KV-context errors, admission diagnostics, peak KV cells, peak GPU memory, peak host footprint |
| `tasks.csv` | task | Arrival, admission and finish times, status, ready-queue, tool and service time |
| `requests.csv` | model request | Prompt and output tokens, TTFT, TPOT, reused prompt tokens, and the server error text for failed requests (e.g. `Context size has been exceeded.`) |
| `timeline.csv` | scheduler event | Number of queued, runnable, running and tool-waiting tasks; time-weighted averages come from this file |
| `kv_memory.csv` | 1 s sample | Physical occupied KV cells from the patched llama.cpp exporter (`runtime/`) |
| `gpu_memory.csv` | 1 s sample | Device-wide allocated GPU memory from `nvidia-smi` |
| `admission_checks.csv` | dispatch | Read-only reservation diagnostic: would the queue head fit the estimated remaining KV budget, could a later request fit, and what happened to the dispatched request |

Times are seconds from the start of the workload. Server logs and process-memory samples were not kept: failure causes are in `requests.csv` and the peak host footprint is in `meta.json`. Sweep folders also hold `summary.csv` (one row per run) and launcher status files.

## Notes

- `phase1/aws_smoke_fifo_6_ctx12k_n4_seed2` stopped before writing `meta.json`; its inventory row is built from `tasks.csv`.
- The three 50-task L4 burst runs in S1 did not sample GPU memory.
- Latency quantiles in `meta.json` cover successful tasks only; read them together with the completion count.
