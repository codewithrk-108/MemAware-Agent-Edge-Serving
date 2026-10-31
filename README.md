# Memory-aware agent edge serving: Phase 1 baseline

Runtime: llama.cpp `llama-server` (Metal), model fixed to Qwen3-0.6B Q8_0 for all main comparisons.
Recorded machines include Apple M5 and Apple M4 Pro, each with 24 GB unified memory; see each run's metadata.
**Run on AC power** - on battery macOS throttles the GPU and timings can be slower and noisy; the scripts refuse to run on battery unless `--allow-battery` is passed.

## Layout

| path | content |
|---|---|
| `harness/` | llama-server launcher, streaming client, macOS memory probe (`phys_footprint` incl. Metal + kernel-tracked peak) |
| `micro/` | microbenchmarks M1-M6: isolate one runtime property each |
| `workloads/` | workload definitions and trace builders (synthetic mix, AgentInstruct) |
| `phase1/` | FIFO runner with fixed concurrency, experiment grid |
| `analysis/` | figures and summary tables |
| `results/` | raw CSVs, per-run metadata (machine, power, llama.cpp commit, server args), figures |

## Reproduce

```bash
uv sync
# llama.cpp next to this folder, built with: cmake -B build && cmake --build build -j --target llama-server
hf download Qwen/Qwen3-0.6B-GGUF Qwen3-0.6B-Q8_0.gguf --local-dir ../models

./micro/run_all.sh                                   # microbenchmarks, ~40 min

uv run python -m workloads.build_synthetic           # data/traces/synthetic.jsonl
hf download zai-org/AgentInstruct --repo-type dataset --local-dir data/raw/AgentInstruct
uv run python -m workloads.build_agentinstruct       # data/traces/agentinstruct.jsonl
uv run python -m phase1.sweep --dry-run              # preview the default AgentTrace concurrency sweep
uv run python -m phase1.sweep                        # run that sweep; resumes if interrupted
uv run python -m analysis.plot_phase1                # results/phase1/figures, summary.md
```

Paths can be overridden with `LLAMA_SERVER=` and `MODEL=`.

The checked-in AgentInstruct trace may have been generated with a different tokenizer. Re-run
`workloads.build_agentinstruct` after downloading Qwen3 before collecting Qwen results; token IDs are
model-specific. Existing result directories are also model-specific and should be regenerated or kept under
distinct run names.

## Workloads

**Synthetic** (`workloads/synthetic.yaml`): four task classes with shared per-class random token prefixes.

| class | share | steps | first context | tokens/step | tool result | tool delay (median) |
|---|---|---|---|---|---|---|
| chat | 35% | 1-2 | 256 + 64-512 | 32-128 | 32-128 | 0.5 s |
| rag | 20% | 1-2 | 256 + 2k-6k | 64-256 | 512-1536 | 1.0 s |
| react_agent | 30% | 3-8 | 1024 + 128-512 | 32-128 | 64-512 | 2.0 s |
| long_gen | 15% | 1-3 | 256 + 256-1024 | 384-768 | 64-256 | 3.0 s |

**AgentInstruct** (`workloads/agentinstruct.yaml`), public dataset, 1,866 real trajectories in 6 environments
(os, db, alfworld, webshop, kg, mind2web). Step structure and token counts come from the data, tokenized with the
model's chat template; every task of an environment shares the instruction + demonstration prefix.
Tool latency is **not** in the dataset and is modeled per environment (stated in the yaml).

**AgentTrace** (`data/source/agenttrace_readable.jsonl`): MBPP and NL2Bash task runs from Qwen3-0.6B and
Qwen3-1.7B. Convert the readable source to runner traces with `python -m workloads.build_agenttrace` and the
served model's `/tokenize` endpoint. The converter prepends the pinned tool-calling system instructions to
the first request, appends fixed tool results at later steps, and excludes contexts that exceed the configured
limit. Preconverted development and evaluation files are in `data/traces/`; they use the pinned official Qwen3
tokenizer JSON. See [the source workload notes](data/source/README.md) for regeneration commands and provenance.

Prompts are sent as token ids, responses have a fixed length (`ignore_eos`), and each step appends the model's
own output to the history, so context growth and prefix reuse behave as in a real agent loop.

## Phase 1 baseline

- Tasks arrive open-loop (Poisson); each ready model request enters one FIFO queue.
- N workers each run one model request. A task releases its worker during a tool wait and its continuation
  joins the FIFO tail when the fixed tool result arrives.
- llama-server gets `-np N`, so the server never queues internally: admission is decided only by the FIFO.
- A task not finished 240 s after arrival is abandoned and counted as a timeout (queued or running).
- Memory budget: KV cache `-c` (for Qwen3-0.6B with f16 KV, 16384 tokens = 1792 MiB) + host prompt cache (`--cache-ram`, server default 8 GiB).

Per request: TTFT, TPOT (client side), prompt tokens computed vs reused, errors.
Per task: queueing time, completion time, service time, tool time, outcome.

Physical KV occupancy can be sampled with the pinned runtime instrumentation in
[runtime/README.md](runtime/README.md). The runner writes `kv_memory.csv` separately
from `gpu_memory.csv`; missing occupancy telemetry is reported as unavailable.
Use `--require-kv-metrics` on new runs to require this measurement before replay.
Per run: throughput (tasks/min, tokens/s), p50/p95/p99, peak footprint, timeline of queue, running tasks and memory.
Request-FIFO runs also write `admission_checks.csv`. Before each dispatch, this read-only diagnostic estimates
the remaining KV budget as `-c` minus the full prompt plus output budgets of active requests. It records whether
the oldest ready request would exceed that budget and how many later ready requests would fit individually. The `meta.json`
summary counts those events and size-only backfill opportunities. This is not a live GPU KV measurement: it
reserves future output tokens for active requests but does not account for retained idle cache or runtime overhead.
The summary separately counts server errors containing `Context size has been exceeded` as `kv_context_errors`.
Compare its estimates with request errors and server metrics. Each diagnostic row records whether the dispatched
request succeeded, returned an error, or timed out. For requests that returned, `task_index` and `step` link the
diagnostic to `requests.csv`, even when a task was sampled more than once; a deadline timeout has no request row.

The runner uses the `request` scheduler by default. In request mode, one worker executes one model step and is
released while the following tool call runs outside the FIFO. Other ready requests continue decoding. When the
tool finishes, its response is appended to task history and the next step joins the FIFO tail as runnable work.
FIFO priority is therefore based on when each model step becomes ready, with no tool-induced head-of-line
blocking. The `fifo-improved` branch contains an alternative scheduler that reserves a skippable tail placeholder
while a tool is pending.

Run one configuration in request mode:

```bash
uv run python -m phase1.runner \
  --name syn_request_N8 \
  --trace data/traces/synthetic.jsonl \
  --n-tasks 100 \
  --rate 0.4 \
  --concurrency 8 \
  --scheduler request
```

Run only the matched task-versus-request comparison sweep:

```bash
uv run python -m phase1.sweep --groups scheduler
```

Use `--scheduler task` explicitly to select the original whole-task FIFO behavior. If the flag is omitted,
`request` is used. In request mode, `concurrency` is the maximum number of active LLM calls; tool-waiting tasks
do not consume that capacity.

The AgentTrace sweep uses the fixed 50-task `agenttrace_50_seed2.jsonl` dataset, selected without replacement
from `agenttrace_all_tool_delay_150x_max10s.jsonl` using seed 2. Its adjacent manifest records source and
dataset checksums and task IDs. The runner deterministically permutes those 50 tasks using its run seed.
It uses a 32768-token shared KV pool, request timeout 900 s, task deadline 14400 s and, unless a group changes
it, a Poisson arrival rate of 0.025 tasks/s (40 s mean gap) and disabled host prompt cache. With seed 2 the
50 arrivals span about 31.75 minutes; this is random and is not a fixed-duration guarantee. The concurrency
comparison uses N = 1, 2, 4 and 8 with the same selected tasks and arrival schedule. The optional arrival
group tests mean gaps of 38, 40 and 42 seconds; under the fixed seed, each of those schedules spans between
30 and 35 minutes. Selection is random, not the proposed stratified 500-task
sample. These runs can take hours. On Linux `peak_footprint_mib` is CPU RSS, not GPU memory. The runner
also samples device-wide NVIDIA GPU 0 memory once per second into `gpu_memory.csv` and reports the sampled
peak in `gpu_peak_used_mib`; samples cover startup and may miss brief peaks. If `nvidia-smi` is unavailable,
the GPU peak is null and the sampling error is recorded in the summary. The optional cache-retention group enables an
8192 MiB host cache for one comparison and may increase system RAM use.

Pass `--deduplicate` with the desired `--groups` to reuse identical runs across groups. The sweep records
each requested name and its equivalent run in `results/phase1/sweep_aliases.json`. A run is skipped on
resume only when its `meta.json` exists.

The separate `smoke_t4` group contains three pending T4 smoke runs. All tasks arrive at once; each run uses
seed 2, request FIFO, prompt reuse on, host prompt cache off, the saved tool delays, a 900-second request
timeout, and a 14400-second task deadline. The two eight-task runs select the same tasks. The 20-task run
uses a separately selected seed-2 sample from the same 50-task trace, so it is not a matched comparison
of concurrency against the eight-task runs.

| Tasks | Concurrency | Shared KV pool |
|---:|---:|---:|
| 8 | 8 | 32768 tokens |
| 8 | 8 | 65536 tokens |
| 20 | 16 | 65536 tokens |

Preview the configurations without running inference using
`python -m phase1.sweep --groups smoke_t4 --dry-run`. On the T4 instance,
`phase1/run_t4_smoke_and_stop.sh` runs the group and requests shutdown on completion or error. Set the
instance shutdown behavior to Stop first. Running `phase1.sweep` directly does not stop the instance.

| group | varies | question |
|---|---|---|
| `concurrency_syn`, `concurrency_ai` | N = 1..32 | where does completed work peak, what does more admission cost |
| `budget_syn` | KV budget 8k / 16k / 32k at N = 16 | memory pressure vs failures |
| `idle_syn` | tool delay x0 / x1 / x4 at N = 8 | cost of idle periods between steps |
| `retention_syn`, `retention_ai` | host cache off, prefix reuse off | do retained contexts and reusable prefixes matter |
| `scheduler` | task FIFO vs request FIFO at N = 8 | does releasing workers during tool waits improve utilization |

## Runtime facts the baseline depends on (from the microbenchmarks)

- KV memory is allocated at startup for the whole `-c`, not as contexts grow. The Qwen3-0.6B trial's f16 KV
  uses 112 KiB/token (1792 MiB at `-c 16384`); the earlier 32 KiB/token microbenchmark used Llama-3.2-1B.
- When the shared KV pool runs out mid-generation, llama-server fails **every request in progress** with
  "Context size has been exceeded." (`tools/server/server-context.cpp`, see the TODO there) - over-admission
  fails collectively, not gracefully.
- Idle slots are moved to the host prompt cache when another task needs a slot; that cache grows host memory.
# phase-1-fastllamacpp
