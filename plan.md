# Implemented plan: head-of-line-blocking request FIFO

## Outcome

The `main` branch supports two schedulers:

- `request` (default): one worker executes one LLM step, places its next-step placeholder at the FIFO head during the tool call, and moves it to the tail after the response arrives.
- `task`: one worker owns a complete multi-step task, including tool waits.

The non-blocking policy—placing a pending placeholder at the FIFO tail and allowing workers to skip it—is preserved on the `fifo-improved` branch.

## Request-mode requirements

1. Initial model steps enter FIFO when their tasks arrive.
2. A worker executes exactly one model step per dequeue.
3. After a successful non-final step, generated tokens are appended to persistent task history and the worker is released.
4. A placeholder for the next step is inserted at the FIFO head while its tool is outstanding.
5. Workers must not skip a tool-pending head; later ready entries remain blocked.
6. When the tool resolves, its response tokens are appended to task history.
7. The placeholder is then removed from the head and appended to the FIFO tail as runnable work.
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
insert pending placeholder at FIFO head
        |
        v
release inference worker and await tool
        |
        v
append tool-response tokens
        |
        v
move placeholder to FIFO tail and mark runnable
```

## Configuration

```bash
uv run python -m phase1.runner --scheduler request ...
```

Omitting the flag selects request-level FIFO. Use `--scheduler task` for whole-task FIFO. `RunConfig.scheduler` and result metadata use the values `task` and `request`.

## Implementation surface

- `phase1/runner.py`: scheduler selection, persistent task history, blocking pending-head FIFO, asynchronous tool waits, deadlines, measurements, and clean termination.
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
- [x] Insert pending placeholders at the head and block later entries.
- [x] Move resolved placeholders to the FIFO tail.
- [x] Enforce request concurrency and task deadlines.
- [x] Track queue, running, and tool-waiting states.
- [x] Add matched scheduler sweep configurations.
- [x] Update analysis and README documentation.
- [x] Test task mode, head-of-line blocking, tail movement after resolution, history, concurrency, errors, deadlines, duplicate IDs, and shutdown.

## Validation

Run:

```bash
.venv/bin/python -m compileall phase1 analysis tests
.venv/bin/python -m unittest tests.test_phase1_scheduler
.venv/bin/python -m phase1.sweep --dry-run --groups scheduler
```

Full llama.cpp performance experiments remain hardware-dependent and should be run on AC power.
