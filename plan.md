# Implemented plan: ready-time request FIFO

## Outcome

The `main` branch supports two schedulers:

- `request` (default): one worker executes one LLM step, releases capacity during the tool call, and appends the next step to the FIFO tail only after the tool response is available.
- `task`: one worker owns a complete multi-step task, including tool waits.

The earlier policy—placing a pending placeholder in the FIFO when the tool begins—is preserved on the `fifo-improved` branch.

## Request-mode requirements

1. Initial model steps enter FIFO when their tasks arrive.
2. A worker executes exactly one model step per dequeue.
3. After a successful non-final step, generated tokens are appended to persistent task history and the worker is released.
4. The task is not present in the runnable FIFO while its tool is outstanding.
5. When the tool resolves, its response tokens are appended to task history.
6. The next model step is then appended to the FIFO tail as runnable work.
7. FIFO ordering is therefore based on when each model step becomes ready.
8. Tool-waiting tasks consume no inference worker or llama-server slot.
9. At most `concurrency` model requests execute simultaneously.
10. End-to-end task deadlines continue across queue, model, and tool time.

## Control flow

```text
model step completes
        |
        v
append generated tokens
        |
        v
release inference worker
        |
        v
await tool outside FIFO
        |
        v
append tool-response tokens
        |
        v
append next step to FIFO tail
```

## Configuration

```bash
uv run python -m phase1.runner --scheduler request ...
```

Omitting the flag selects request-level FIFO. Use `--scheduler task` for whole-task FIFO. `RunConfig.scheduler` and result metadata use the values `task` and `request`.

## Implementation surface

- `phase1/runner.py`: scheduler selection, persistent task history, ready FIFO, asynchronous tool waits, deadlines, measurements, and clean termination.
- `phase1/sweep.py`: matched N=8 task/request experiments for synthetic and AgentInstruct workloads.
- `analysis/plot_phase1.py`: scheduler comparisons, old-result compatibility, and tool-wait timeline rendering.
- `README.md`: behavior and commands.
- `tests/test_phase1_scheduler.py`: deterministic scheduler tests with fake completions.

## Completed checklist

- [x] Make request FIFO the default while retaining explicit task FIFO.
- [x] Add `--scheduler {task,request}` and metadata.
- [x] Execute one model step per dequeue in request mode.
- [x] Keep task histories across workers and steps.
- [x] Release workers during tool calls.
- [x] Append tool responses before enqueueing the next step.
- [x] Append next steps to the FIFO tail at tool-completion time.
- [x] Enforce request concurrency and task deadlines.
- [x] Track queue, running, and tool-waiting states.
- [x] Add matched scheduler sweep configurations.
- [x] Update analysis and README documentation.
- [x] Test task mode, interleaving, ready-time ordering, history, concurrency, errors, deadlines, duplicate IDs, and shutdown.

## Validation

Run:

```bash
.venv/bin/python -m compileall phase1 analysis tests
.venv/bin/python -m unittest tests.test_phase1_scheduler
.venv/bin/python -m phase1.sweep --dry-run --groups scheduler
```

Full llama.cpp performance experiments remain hardware-dependent and should be run on AC power.
