# Codebase research: Phase 1 fast llama.cpp baseline

## System purpose and high-level behavior

This project is an experimental harness for studying memory-aware serving of multi-step LLM agents on an Apple-silicon edge machine. It does not implement an LLM runtime itself. Instead, it launches `llama.cpp`'s `llama-server`, sends controlled token-level workloads to its HTTP API, samples process memory, and saves reproducible measurements.

The fixed reference setup is Llama 3.2 1B Instruct Q8_0 on Metal. The central research question is how concurrency, KV-cache capacity, prompt-prefix reuse, host-side prompt retention, and tool-induced idle time affect throughput, latency, failures, and unified-memory consumption.

There are two experiment layers:

1. Six microbenchmarks isolate individual runtime properties.
2. A Phase 1 FIFO baseline replays complete agent tasks under open-loop Poisson arrivals and fixed admission concurrency.

The repository currently contains complete harness and analysis code, generated workload traces, and partial microbenchmark results. It does not contain Phase 1 result directories.

## End-to-end architecture and data flow

```text
YAML workload specs / AgentInstruct Parquet
                    |
                    v
          workload builder scripts
                    |
                    v
       JSONL token-level task traces
                    |
          +---------+---------+
          |                   |
          v                   v
   microbenchmarks      Phase 1 FIFO runner
          |                   |
          +--------+----------+
                   |
                   v
       managed llama-server process
       (/completion, /tokenize, /metrics)
                   |
                   v
       CSV + JSON metadata + logs
                   |
                   v
       plotting and Markdown summaries
```

At request time, prompts are integer token IDs rather than text. The client asks for deterministic, fixed-length generations with `ignore_eos=true`. For multi-step tasks, the model's returned tokens and the next tool result are appended to the history, then the full history is resent. This makes context growth and prefix-cache reuse observable without confounding results with tokenizer cost.

## Runtime and request lifecycle

- `harness.server.ServerConfig` converts Python settings into `llama-server` arguments such as total context cells (`-c`), slots (`-np`), unified versus partitioned KV, RAM prompt-cache size, mmap/load mode, and Metal offload.
- `harness.server.Server` starts the process, waits for `/health`, samples memory in a background thread, exposes `/metrics`, parses verbose allocation logs, and shuts the process down on context-manager exit.
- `harness.client.complete` streams `/completion` server-sent events and returns client-side TTFT, TPOT, end-to-end latency, server timing fields, cache reuse counts, output tokens, and normalized error information. Exceptions become result rows rather than aborting an experiment.
- `harness.results` records machine, OS, power source, RAM, model, llama.cpp commit, and experiment metadata. Benchmarks normally refuse to run on battery because Metal performance becomes unstable.

## Workload representation

`workloads.trace` defines a task as a sequence of steps. Each step contains:

- `input`: token IDs newly appended before that call;
- `n_gen`: exact requested output-token count;
- `tool_delay_s`: simulated tool latency before the next step, or `null` for the last step.

The synthetic builder produces 400 deterministic tasks across chat, RAG, ReAct-agent, and long-generation classes. Tasks in a class share a fixed prefix, deliberately creating prefix-reuse opportunities. The checked-in synthetic trace has 1,180 total steps and a maximum final context of 7,127 tokens.

The AgentInstruct builder reads all six Parquet splits, converts conversations to Llama 3 chat-template segments, starts a temporary server to tokenize them, applies modeled per-environment tool delays, and drops traces over 12,000 tokens. The checked-in trace retains all 1,866 dataset tasks, has 9,772 total steps, and a maximum final context of 6,390 tokens.

## Phase 1 scheduling behavior

`phase1.runner` draws tasks from a trace with a seeded RNG and draws open-loop exponential inter-arrival gaps. Arrivals enter one `asyncio.Queue`; `N` workers admit tasks FIFO and keep a worker for the task's full lifetime, including tool sleeps. The server is configured with the same number of slots, so the Python FIFO is intended to be the only admission queue.

Each task has a deadline measured from arrival. A task can end as `ok`, `failed`, `timeout_queued`, or `timeout_running`. The runner records task timing, every model request, queue/running timeline changes, sampled memory, server metrics, and a run summary. `phase1.sweep` defines concurrency, KV-budget, tool-delay, prompt-cache, and prefix-reuse comparisons and skips already completed configurations.

## Microbenchmarks

| Benchmark | What it varies | What it establishes |
|---|---|---|
| M1 memory | context capacity, slot count, mmap | startup/static allocation and filled-context footprint |
| M2 prefill | prompt length | TTFT and prefill throughput scaling |
| M3 concurrency | simultaneous requests and prompt length | continuous batching throughput versus per-request cost |
| M4 prefix reuse | cached versus recomputed second step | benefit of retaining an agent's prefix |
| M5 retention | sessions, cache modes, idle gaps | whether contexts survive interleaving and their RAM cost |
| M6 capacity | unified/partitioned KV and aggregate demand | runtime behavior when requests exceed KV capacity |

## File-by-file guide

### Repository root

- `README.md`: experiment overview, fixed hardware/model assumptions, reproduction commands, workload definitions, Phase 1 experiment groups, and conclusions expected from microbenchmarks.
- `pyproject.toml`: Python 3.12 package metadata and runtime dependencies (`aiohttp`, NumPy, pandas, matplotlib, psutil, PyArrow, and PyYAML). It defines a placeholder console script.
- `uv.lock`: generated, pinned dependency graph for reproducible `uv sync`/`uv run`; it is dependency metadata rather than experiment logic.
- `.python-version`: asks version managers and `uv` to use Python 3.12.
- `.gitignore`: excludes Python caches, build outputs, and `.venv`.
- `src/agent_edge_serving/__init__.py`: placeholder package entry point that only prints a greeting; none of the experiment commands use it.

### `harness/`

- `harness/__init__.py`: empty package marker.
- `harness/client.py`: async streamed completion client, deterministic random token generation, connection-session setup, latency calculation, server-timing extraction, returned-token collection, and error normalization.
- `harness/memory.py`: macOS-specific `proc_pid_rusage` binding for `phys_footprint` and lifetime peak, portable RSS fallback, and a background `MemSampler`.
- `harness/results.py`: AC-power guard, environment/provenance capture, and common `raw.csv`/`meta.json` writer.
- `harness/server.py`: server configuration, command construction, subprocess lifecycle, health polling, memory sampling, metrics parsing, verbose-log allocation parsing, and environment-variable path overrides.

### `workloads/`

- `workloads/__init__.py`: empty package marker.
- `workloads/trace.py`: `Step` and `Task` dataclasses, JSONL serialization, derived token/tool totals, lognormal latency helper, and workload summary formatting.
- `workloads/synthetic.yaml`: seeded four-class distribution and all ranges for prefix, context, step, generation, tool-result, and delay sizes.
- `workloads/build_synthetic.py`: samples that YAML deterministically, creates shared per-class prefixes and unique task content, and writes the synthetic JSONL trace.
- `workloads/agentinstruct.yaml`: 12k context cutoff plus assumed lognormal tool-latency parameters for the six environments.
- `workloads/build_agentinstruct.py`: parses dataset conversations, formats Llama 3 chat turns, batches `/tokenize` requests, maps agent/environment turns to trace steps, filters oversized tasks, and saves the trace.

### `micro/`

- `micro/__init__.py`: empty package marker.
- `micro/common.py`: shared CLI flags, battery check, settling sleep, port, and monotonic clock helper.
- `micro/m1_memory.py`: restarts the server across context/slot/load configurations, nearly fills a context, and compares reported allocations, idle memory, and filled memory.
- `micro/m2_prefill.py`: disables prompt reuse and measures one-token completions over increasing unique prompt lengths after warmup.
- `micro/m3_concurrency.py`: launches synchronized request bursts, measures aggregate generation throughput, TTFT/TPOT, and peak memory while provisioning enough KV to isolate batching compute.
- `micro/m4_prefix_reuse.py`: performs a two-step agent exchange and compares cached second-step prefill with full recomputation.
- `micro/m5_retention.py`: round-robins multiple growing sessions through four slots under five cache/KV configurations, then separately tests wall-clock idle gaps.
- `micro/m6_capacity.py`: sends bursts whose combined contexts may exceed an 8,192-cell pool and classifies success, rejection, truncation, timeout, or other failure.
- `micro/run_all.sh`: sequentially runs M1–M6 and then regenerates the micro plots and summary.

### `phase1/`

- `phase1/__init__.py`: empty package marker.
- `phase1/runner.py`: FIFO arrival/admission simulation, task replay, deadline handling, detailed telemetry tables, summary metrics, result persistence, and ad hoc CLI.
- `phase1/sweep.py`: named experiment grid, resume/redo/dry-run behavior, execution loop, and cross-run summary CSV generation.

### `analysis/`

- `analysis/__init__.py`: empty package marker.
- `analysis/plot_micro.py`: loads available M1–M6 CSVs, creates PNGs, derives headline metrics, tolerates incomplete benchmark sets, and rewrites `results/micro/summary.md`.
- `analysis/plot_phase1.py`: creates concurrency curves, outcome bars, bottleneck breakdowns, memory/queue timelines, per-class completion CDFs, factor comparisons, and Phase 1 summary files.

### Raw and generated data

- `data/raw/AgentInstruct/README.md`: Hugging Face dataset card, schema, split sizes, provenance, task descriptions, and citation.
- `data/raw/AgentInstruct/.gitattributes`: standard Git LFS rules for binary dataset/model formats.
- Six `data/raw/AgentInstruct/data/*.parquet` files: one binary table per environment. Every table has `id` and nested `conversations` columns; row counts are alfworld 336, db 538, kg 324, mind2web 122, os 195, and webshop 351.
- `data/traces/synthetic.jsonl`: generated 400-task token-level synthetic trace; large numeric payload, consumed by the runner.
- `data/traces/agentinstruct.jsonl`: generated 1,866-task token-level real-trajectory trace; large numeric payload, consumed by the runner.

### Recorded result files

- `results/micro/*/meta.json`: reproducibility metadata, benchmark constants, server arguments, hardware/model identity, and timestamp for M1–M5.
- `results/micro/*/raw.csv`: request/config-level measurements. Present row counts are M1 10, M2 3, M3 42, M4 4, and M5 64. M2 has two error rows; the others report no row-level errors.
- `results/micro/m1_memory/logs/*.log`: verbose server startup/allocation and fill-request logs for each M1 configuration.
- `results/micro/m2_prefill/server.log`, `m3_concurrency/server.log`, and `m4_prefix_reuse/server.log`: runtime request and lifecycle logs for those benchmarks.
- `results/micro/m5_retention/logs/*.log`: server logs for each recorded cache/session/gap configuration.
- `results/micro/m6_capacity/logs/unified.log`: a partial/server-side M6 log; there is no M6 `raw.csv` or metadata, so the benchmark is not analyzable as a completed result.
- `results/micro/figures/m1_memory.png` through `m5_retention.png`: generated visual summaries of the available raw data.
- `results/micro/summary.md`: generated headline table. It reports 32 KiB of KV per token, 13x reuse speedup at 4,096 tokens, and the host-cache memory/latency tradeoff, among other partial results.
- `results/micro/m1_memory.stdout`: records a refused battery-powered invocation, not benchmark data.
- `results/_logs/tokenizer.log`: successful temporary tokenizer-server startup/shutdown; it also exposes the original author's absolute model path.
- `results/_smoke/server.log` and `results/_smoke/nommap.log`: large smoke-test server logs used during development; they are diagnostic artifacts, not structured result inputs.

### Nested Git metadata

- `.git/HEAD`, `config`, refs, logs, index, packed refs, object pack, and `info/exclude` describe the nested repository and its single visible `phase I v1` commit.
- `.git/hooks/*.sample` are Git's stock disabled hook templates. They do not participate in the application or experiments.

## Existing patterns to preserve

- Use dataclasses for experiment and trace configuration.
- Use module CLIs (`uv run python -m ...`) rather than the placeholder package command.
- Fix RNG seeds and save full runtime/environment metadata.
- Represent operational errors as measured outcomes, while reserving process crashes for harness failures.
- Use context managers for server lifetime and `asyncio` for request concurrency.
- Store raw observations first; derive figures and summaries in separate analysis modules.
- Make partial result analysis non-fatal where practical.

## Constraints and assumptions

- The intended measurements are macOS/Apple-silicon specific; non-macOS memory sampling falls back to RSS and cannot reproduce Metal-aware `phys_footprint` semantics.
- The external `llama.cpp` checkout, compiled `llama-server`, and GGUF model are required but not included.
- Code depends on specific server flags and response/metric fields that may change across llama.cpp revisions.
- Random token IDs avoid tokenizer variance but do not reproduce natural-language token distributions.
- `ignore_eos` makes output length controlled but less representative of real stopping behavior.
- AgentInstruct tool latencies are invented modeling assumptions because the dataset has no timing.
- Phase 1 workers hold admission slots during tool waits; this is a deliberate baseline policy, not a general serving design.
- The host cache default is assumed to be 8 GiB and may depend on the pinned server version.
- Only partial/quick micro results are checked in, and Phase 1 results are absent.

## Risks and notable code issues

- `harness.client.complete` sets `t_first` with `rec["t_first"] or t`; a valid monotonic value is always truthy in practice, but explicit `None` handling would be clearer.
- `phase1.runner.run_task` removes `tokens_list`, although the client key is `tokens`; the real `tokens` field is removed later before CSV output, so this is harmless dead cleanup.
- `run_task` marks a request failure but does not set `finish`; the worker fills it in, as intended.
- `summarize` treats the last finish time as the run span. This is sensible, but failed/timed-out tasks influence the denominator while throughput counts only successful tasks.
- M2's checked-in metadata/result set appears to be a quick run despite the summary text being regenerated from only one successful length; conclusions about scaling are therefore incomplete.
- `src/agent_edge_serving` and the declared console script are scaffolding unrelated to the real module CLIs.
- Absolute paths embedded in old logs reduce portability and may reveal developer-specific filesystem details.
- There are no automated unit tests or CI configuration. Validation is experiment execution plus analysis generation.

## Recommended implementation surface area

For the current goal—understanding and documenting the project—no runtime source changes are recommended. The useful artifact surface is this `research.md` plus `plan.md`.

If the next goal is to complete Phase 1, the minimal operational surface is:

1. verify the external server/model paths and llama.cpp revision;
2. rerun incomplete M2/M6 measurements on AC power;
3. execute `phase1.sweep` to populate `results/phase1`;
4. run both analysis modules;
5. inspect failures and generated summaries without changing scheduler semantics mid-baseline.

## Open questions

- Should the checked-in quick micro results be replaced by full runs or retained as development evidence?
- Is the intended target still the original Apple M5/24 GiB machine, or should metadata/results be regenerated on the current machine?
- Should Phase 1 remain strictly a baseline reproduction, or is a new scheduling/admission feature planned after it?
- Should large traces, logs, figures, and result CSVs remain version-controlled, or move to an artifact/data store?

## Historical feature research: pending-placeholder FIFO (`fifo-improved` branch)

The design below is preserved on the `fifo-improved` branch. It is not the request-mode policy on `main`.

### Current task-level behavior

The FIFO queue in `phase1/runner.py` stores task indices. A worker dequeues one task and awaits `run_task`, which executes every LLM step and every intervening tool sleep before returning. Consequently, `concurrency` means the number of admitted whole tasks, and a task holds a Python worker while it is waiting for a tool.

The current queue can append worker sentinels immediately after the final initial arrival because tasks never return to the queue. That termination mechanism cannot be reused unchanged for request-level scheduling: a sentinel could be consumed before a tool timer re-enqueues a later step.

### Required request-level behavior

The new mode should be opt-in and preserve the existing scheduler as the default. Its queue should hold ready task steps, represented by a task index whose persistent state identifies the next step. Each worker should execute at most one `/completion` request per dequeue.

After a successful non-final step:

1. append the returned model tokens to that task's persistent history;
2. release the worker immediately;
3. immediately place a placeholder for the next step at the FIFO tail;
4. run the scaled tool delay in a separate asynchronous coroutine;
5. append the tool-response tokens to the task history while its placeholder is queued;
6. mark that queued step runnable only after appending the tool response.

The queue therefore contains runnable and tool-pending entries. A pending entry retains the position assigned when its tool call began, but it must not block runnable entries behind it. Workers select the oldest runnable entry, skipping pending entries without moving them. Once a skipped entry becomes runnable, it retains its earlier position and has priority over entries inserted after it. A zero or disabled delay makes the placeholder runnable immediately. The final step completes without another placeholder.

### Proposed configuration contract

- Add `scheduler: str = "task"` to `RunConfig`.
- Add CLI option `--scheduler {task,request}` with default `task`.
- Persist the scheduler in `meta.json` through the existing `asdict(cfg)` path and add it to run summaries.
- Keep the meaning of `concurrency` as the maximum simultaneous active LLM calls in request mode. `execute` should continue setting `llama-server -np` to the same value.
- Do not change workload trace format or the `/completion` request body.

This named choice is preferable to a Boolean flag because result metadata remains self-describing and future scheduler names can be added without multiple negative flags.

### State required per task

The current local `history` variable must become persistent in request mode. Minimal state consists of task index, next step index, accumulated token history, queue sequence, existing `TaskRecord` fields, and whether the task is runnable, queued pending a tool, running, or terminal. State must be keyed by selected-task index because sampling with replacement can produce duplicate `task_id` values.

The output schema should remain compatible. `admit` should continue to mean first admission, `finish` terminal time, and `worker` can retain the most recent worker. Individual request rows already record the worker for each step.

### Deadline semantics

The deadline remains end-to-end from task arrival. Request-level tasks may expire in the ready FIFO, during an LLM request, or during a tool wait. For aggregate compatibility, the recommended mapping is:

- `timeout_running` during an active LLM call;
- `timeout_queued` while not using a worker, including ready-queue and tool-wait time.

The task `error` should distinguish queue expiry from tool-wait expiry. Tool sleep must be bounded by the remaining task deadline.

### Termination and coordination

Initial-arrival completion is no longer enough to stop workers. Request mode needs a terminal-task counter or completion event, exactly-once terminal transitions, tool-wait coroutines that activate existing entries or time out, shutdown only after all tasks are terminal, and no orphaned timer when `run` returns. Since `asyncio.Queue` cannot skip a pending head and select the oldest runnable item, use a small scheduler-owned ordered collection with an `asyncio.Condition` notification mechanism. All mutations remain on one asyncio loop.

### Timeline and measurement implications

In task mode, `running` counts admitted whole tasks. In request mode it should count active LLM calls. `queued` should count all waiting placeholders. Add `runnable` and `tool_waiting` timeline counts so utilization and pending-entry skipping are interpretable.

`queue_s = first_admit - arrival` remains initial queueing delay but misses later ready waits. Add cumulative task-level `ready_queue_s` and per-request ready-queue wait so the modes can be compared fairly.

### Integration surface

The minimal code surface is:

- `phase1/runner.py`: option, request state/loop, deadline/termination logic, metrics, CLI.
- `phase1/sweep.py`: a focused matched scheduler-comparison group.
- `analysis/plot_phase1.py`: scheduler comparison and optional tool-waiting visualization.
- `README.md`: scheduler semantics and example commands.
- a small scheduler test module using a fake async completion call, without a real model server.

No changes are needed in the harness client/server, trace schema, workload builders, or microbenchmarks.

### Key risks

- Duplicate re-enqueue or finalization can execute a step twice or prevent shutdown.
- A deadline/tool-timer race can activate an already timed-out entry.
- A plain `asyncio.Queue` would either head-of-line block on a pending tool or lose the pending entry's required FIFO position.
- Greater interleaving may reduce prompt-cache hits and increase prefill work.
- The same concurrency value means admitted tasks in task mode but active requests in request mode.
- Integration tests depend on an external llama.cpp build/model, so scheduler correctness needs deterministic fake-completion tests.

## Main-branch revision: head-of-line-blocking request FIFO

On `main`, this is the default scheduler. After a model step makes a tool call, a pending placeholder for its next
step is inserted at the FIFO head. It consumes no worker, but workers cannot skip it, so later ready entries are
head-of-line blocked. When the tool finishes, the scheduler appends the tool-response tokens to persistent task
history, removes the placeholder from its blocking position, and appends it to the FIFO tail as runnable work.

This makes ordering conventional ready-time FIFO:

```text
step A1 completes -> A-pending enters FIFO head
B and C remain behind A and cannot run
A's tool completes -> append response -> move A2 to FIFO tail
B and C can run before A2
```

The `fifo-improved` branch instead puts the pending placeholder at the tail and permits workers to skip it, avoiding
head-of-line blocking.

## Proposed main-branch change: ready-time FIFO without placeholders

The requested next revision differs from both historical variants above. A task should be completely absent from
the runnable FIFO while its tool is outstanding. Its asynchronous tool coroutine owns the waiting state. Once the
tool resolves, the coroutine appends the next step's input tokens to persistent history and adds the task to the
FIFO tail as runnable work.

```text
A1 decodes -> A enters tool_waiting outside FIFO
ready FIFO remains [B1, C1, ...]
B1 and C1 can decode
A tool resolves -> append response -> enqueue A2 at FIFO tail
```

### Repository evidence

- `RequestTaskState.history` and `next_step` already preserve task state outside a worker.
- `wait_for_tool` already executes as a separately tracked asyncio task.
- `Timeline.tool_waiting` already represents the required outside-FIFO state.
- The current blocking behavior comes only from `put_pending_head`, head-only `take`, `resolve_to_tail`, and their
  queue/timeline updates.
- Existing deterministic scheduler tests provide the correct integration point for ordering, concurrency,
  history, timeout, and shutdown validation.

### Recommended minimal surface

Modify `phase1/runner.py`, `tests/test_phase1_scheduler.py`, `README.md`, and these planning artifacts. Keep the
harness, model configuration, workloads, sweep grid, result schemas, analysis code, microbenchmarks, and
`fifo-improved` branch unchanged.

### Constraints and risks

- Tool timeout occurs outside FIFO and must not remove/decrement a queue entry.
- A resolved task must receive a new tail position based on tool-completion time.
- Zero-delay tools should still enqueue through the asynchronous path so already-ready work keeps FIFO priority.
- Increased interleaving can improve utilization while reducing prompt-cache reuse.
- Old head-blocking results and new ready-time results require separate provenance or regeneration.

### Implementation outcome

The ready-only FIFO design was implemented as planned. Pending-head insertion, in-place activation, and queue
removal during tool timeout were removed. Tool completion now appends the response and performs the only resumed
step enqueue. Deterministic tests confirm that ready requests continue while tools run and resumed steps join the
tail based on tool-completion time.
