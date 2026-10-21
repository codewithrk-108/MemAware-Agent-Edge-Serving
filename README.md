# Memory-aware agent edge serving: Phase 1 baseline

Runtime: llama.cpp `llama-server`, model fixed to Llama-3.2-1B-Instruct Q8_0 for all main comparisons.
**Run on AC power** - on battery laptops throttle the GPU and timings get 2-3x slower and noisy; the scripts
refuse to run on battery unless `--allow-battery` is passed.

## Platforms

Every entry point takes `--platform` (or `AES_PLATFORM=`), default `macos`. Results go to `results/<platform>/`,
so runs from different machines never mix.

| | `macos` (default) | `linux-cuda` |
|---|---|---|
| hardware | Apple Silicon, Metal (tested: M5, 24 GB) | Ubuntu, NVIDIA GPU |
| llama.cpp build | `cmake -B build` | `cmake -B build -DGGML_CUDA=ON` |
| memory reported | `phys_footprint`: unified memory incl. Metal buffers, kernel-tracked peak | host RSS (peak: `VmHWM`) + the server's GPU memory from NVML, and their total |
| extra setup | - | `uv sync --extra cuda` (NVML bindings); falls back to `nvidia-smi` |
| power check | `pmset` | `/sys/class/power_supply` (servers: always AC) |

On `linux-cuda`, KV cache and weights live in GPU memory while the host prompt cache (`--cache-ram`) lives in
host RAM, so check `peak_gpu_mib` and `peak_host_mib` separately. Pick the GPU with `CUDA_VISIBLE_DEVICES`.
If NVML cannot see the server's process (containers with their own pid namespace), whole-GPU usage is used and
`gpu_memory_scope: device` is recorded in `meta.json` - keep the GPU otherwise idle then.
The linux-cuda path is written against NVML and llama.cpp's CUDA log format but has not been run on hardware yet:
check `results/linux-cuda/micro/m1_memory` (KV and weights should appear as GPU memory) before trusting the rest.

## Layout

| path | content |
|---|---|
| `harness/` | llama-server launcher, streaming client, per-platform memory probe, `--platform` handling |
| `micro/` | microbenchmarks M1-M6: isolate one runtime property each |
| `workloads/` | workload definitions and trace builders (synthetic mix, AgentInstruct) |
| `phase1/` | FIFO runner with fixed concurrency, experiment grid |
| `analysis/` | figures and summary tables |
| `results/` | raw CSVs, per-run metadata (machine, power, llama.cpp commit, server args), figures |

## Reproduce

```bash
uv sync
# llama.cpp next to this folder, built with: cmake -B build && cmake --build build -j --target llama-server
hf download bartowski/Llama-3.2-1B-Instruct-GGUF Llama-3.2-1B-Instruct-Q8_0.gguf --local-dir ../models

./micro/run_all.sh                                   # microbenchmarks, ~40 min

uv run python -m workloads.build_synthetic           # data/traces/synthetic.jsonl
hf download zai-org/AgentInstruct --repo-type dataset --local-dir data/raw/AgentInstruct
uv run python -m workloads.build_agentinstruct       # data/traces/agentinstruct.jsonl
uv run python -m phase1.sweep                        # 20 runs, ~2 h, resumes if interrupted
uv run python -m analysis.plot_phase1                # results/macos/phase1/figures, summary.md
```

On Ubuntu + NVIDIA add `--platform linux-cuda` to every command (and `--extra cuda` to `uv sync`):

```bash
uv sync --extra cuda
./micro/run_all.sh --platform linux-cuda
uv run python -m workloads.build_agentinstruct --platform linux-cuda
uv run python -m phase1.sweep --platform linux-cuda
uv run python -m analysis.plot_phase1 --platform linux-cuda   # plotting also works on another machine
```

Paths can be overridden with `LLAMA_SERVER=` and `MODEL=`.

## Workloads

**Synthetic** (`workloads/synthetic.yaml`): four task classes with shared per-class system prompts.

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

Prompts are sent as token ids, responses have a fixed length (`ignore_eos`), and each step appends the model's
own output to the history, so context growth and prefix reuse behave as in a real agent loop.

## Phase 1 baseline

- Tasks arrive open-loop (Poisson) into one FIFO queue; N workers each run the oldest task to completion.
- An admitted task keeps its slot through tool waits (the idle periods between steps).
- llama-server gets `-np N`, so the server never queues internally: admission is decided only by the FIFO.
- A task not finished 240 s after arrival is abandoned and counted as a timeout (queued or running).
- Memory budget: KV cache `-c` (default 16384 tokens = 512 MiB) + host prompt cache (`--cache-ram`, server default 8 GiB).

Per request: TTFT, TPOT (client side), prompt tokens computed vs reused, errors.
Per task: queueing time, completion time, service time, tool time, outcome.
Per run: throughput (tasks/min, tokens/s), p50/p95/p99, peak footprint, timeline of queue, running tasks and memory.

| group | varies | question |
|---|---|---|
| `concurrency_syn`, `concurrency_ai` | N = 1..32 | where does completed work peak, what does more admission cost |
| `budget_syn` | KV budget 8k / 16k / 32k at N = 16 | memory pressure vs failures |
| `idle_syn` | tool delay x0 / x1 / x4 at N = 8 | cost of idle periods between steps |
| `retention_syn`, `retention_ai` | host cache off, prefix reuse off | do retained contexts and reusable prefixes matter |

## Runtime facts the baseline depends on (from the microbenchmarks)

- KV memory is allocated at startup for the whole `-c` (32 KiB/token for this model), not as contexts grow.
- When the shared KV pool runs out mid-generation, llama-server fails **every request in progress** with
  "Context size has been exceeded." (`tools/server/server-context.cpp`, see the TODO there) - over-admission
  fails collectively, not gracefully.
- Idle slots are moved to the host prompt cache when another task needs a slot; that cache grows host memory.
# phase-1-fastllamacpp
