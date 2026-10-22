# Plan: optional request-level FIFO scheduler

> I want to build a new feature, an optional request-level FIFO scheduler, that extends the system to run other ready LLM requests while multi-step tasks are waiting for tools, improving inference-slot utilization while preserving the existing task-level FIFO baseline.

## Feature summary and business outcome

Add a scheduler selection with two modes:

- `task` (default): current behavior; a worker owns one complete task through all model steps and tool waits.
- `request`: a worker executes one model step, immediately places the next step at the FIFO tail, releases capacity during the tool wait, and makes that queued step runnable after appending the tool response.

The outcome is a controlled comparison of task-level admission against request-level multiplexing under identical traces, arrivals, concurrency, memory budgets, seeds, and deadlines.

## Requirements

### Functional requirements

1. Existing invocations without the flag must behave exactly as today.
2. `--scheduler request` must enable request-level FIFO from `phase1.runner`.
3. `RunConfig` must carry scheduler choice for programmatic sweeps and reproducible metadata.
4. Initial steps enter FIFO at task arrival. After a successful non-final model step, its next-step placeholder enters the FIFO tail immediately when the tool call begins—not when the tool finishes.
5. While the placeholder is queued, the asynchronous tool call/delay completes and its response tokens are appended to task history. Only then may that queued step run.
6. A tool-pending entry retains its queue position but does not block runnable entries behind it: workers select the oldest runnable entry, skipping pending entries without moving them.
7. At most `concurrency` LLM requests execute simultaneously; tool-waiting tasks consume no worker.
8. Task steps remain sequential, and every later prompt includes all prior input, generated tokens, and the completed tool response.
9. Prompt caching, request timeout, delay scaling, deterministic selection, and Poisson arrivals work in both modes.
10. The task deadline remains measured from original arrival across queueing, inference, and tool waits.
11. Every task reaches exactly one terminal state, and no tool waiter remains when the run returns.
12. Results identify scheduler mode and measure initial queueing, cumulative queue waiting, tool time, completion, throughput, cache reuse, and memory.

### Compatibility requirements

- Default scheduler is `task`.
- Existing status columns and aggregate outcomes remain readable by current analysis.
- Traces, harness HTTP APIs, and microbenchmarks remain unchanged.
- Comparison runs use unique result names.
- Old results lacking scheduler metadata are interpreted as `task`.

### Timeout classification

- Deadline during `/completion`: `timeout_running`.
- Deadline in the ready FIFO or during a tool wait: `timeout_queued` for aggregate compatibility.
- Store a diagnostic error reason distinguishing queue expiry from tool-wait expiry.

## Current-state summary

`phase1.runner.run` currently creates one queue of task indices and `N` workers. Each worker calls `run_task`, which stores history locally, executes all steps, and sleeps for tool latency without releasing the worker. Arrival completion appends sentinels because tasks never re-enter.

Request scheduling invalidates three assumptions: history cannot remain local to one worker call, tool sleep cannot run inside a worker, and sentinels cannot be emitted after initial arrivals. Full findings are in `research.md`.

## Proposed technical approach

### Configuration and dispatch

Add to `RunConfig`:

```python
scheduler: str = "task"
```

Expose:

```text
--scheduler {task,request}
```

Preserve the task implementation behind scheduler dispatch. Implement request behavior in a separate coroutine instead of scattering mode checks through `run_task`.

### Request-task state

Introduce a small internal state object:

```python
@dataclass
class RequestTaskState:
    task_index: int
    next_step: int = 0
    history: list = field(default_factory=list)
    enqueued_at: float = 0.0
    runnable_at: Optional[float] = None
```

Resolve the immutable task and record by index because trace sampling can duplicate task IDs. Extend `TaskRecord` with cumulative `ready_queue_s`; retain `admit` as first admission and `finish` as terminal time.

### Request-level flow

```python
async def request_worker(worker_id):
    while True:
        state = await scheduler.take_oldest_runnable()
        if state is sentinel:
            return
        if deadline_expired(state):
            finish_once(state, "timeout_queued", "deadline while queued")
            continue

        record_ready_wait(state)
        append_current_step_input(state)
        result = await run_one_completion(state, worker_id, remaining_deadline)

        if result failed:
            finish_once(state, "failed", result.error)
        elif task deadline expired during request:
            finish_once(state, "timeout_running", "deadline during request")
        else:
            append_generated_tokens(state)
            advance_step(state)
            if final_step:
                finish_once(state, "ok")
            else:
                scheduler.enqueue_pending_at_tail(state)
                create_tool_waiter_for_queued_state(state)
```

Tool waits run outside workers:

```python
async def wait_for_tool(state, delay):
    if delay >= remaining_deadline:
        await sleep(remaining_deadline)
        finish_once(state, "timeout_queued", "deadline while waiting for tool")
    else:
        await sleep(delay)
        append_tool_response_to_history(state)
        scheduler.mark_runnable_in_place(state)
```

An ordinary `asyncio.Queue` is insufficient because its head may be tool-pending while later entries are runnable. Use a small ordered collection with an `asyncio.Condition`: scan from the front for the first runnable entry, remove only that entry, and wait when none are runnable. Activating an existing entry notifies workers without changing its position.

After all arrivals occur and all tasks are terminal, notify workers to exit and await clean worker and timer completion.

### Timeline and output

- Preserve `queued` and `running`.
- In request mode, `queued` means all waiting placeholders and `running` means active LLM calls.
- Add `runnable` and `tool_waiting` to timeline rows.
- Add `scheduler` to run summaries.
- Add task-level `ready_queue_s` and per-request queue wait.
- Preserve `queue_s` as arrival-to-first-admission time.

### Comparison sweep

Add a focused scheduler group with matched configurations and distinct names:

```text
syn_N8_task
syn_N8_request
ai_N8_task
ai_N8_request
```

Compare completed tasks/min, completion latency, cache hit ratio, computed prefill tokens, ready wait, and memory before duplicating the full concurrency grid.

## Files to modify

- `phase1/runner.py`: configuration, scheduler dispatch, persistent request state, one-step execution, tool waits, deadlines, termination, metrics, and CLI.
- `phase1/sweep.py`: matched scheduler comparison group.
- `analysis/plot_phase1.py`: old-result compatibility, scheduler comparison, and tool-wait timeline support.
- `README.md`: semantics and command examples.
- `tests/test_phase1_scheduler.py` (new): deterministic async tests with fake completions and tiny traces.

## Files unchanged

- Harness client and server
- Workload schemas/builders and generated traces
- Microbenchmarks
- llama.cpp itself

## Error handling and validation

- Reject unsupported programmatic scheduler values before running.
- Use one terminal-state helper to prevent duplicate finalization.
- Bound model execution by both request timeout and remaining task deadline.
- Bound tool waits by the remaining deadline.
- Never create or activate a queued entry for a terminal task.
- Append generated tokens before inserting the next-step placeholder.
- Append the tool response while the placeholder is queued, then mark it runnable in place.
- Never dispatch a tool-pending entry or reposition it merely because it was skipped.
- Stop only after every task is terminal and no tool coroutine remains.

## Test and manual verification strategy

There is no configured typechecker or existing test suite. Validate continuously with:

```bash
uv run python -m compileall phase1 tests
uv run python -m unittest tests.test_phase1_scheduler
uv run python -m phase1.sweep --dry-run --groups scheduler
```

Tests should verify:

1. task mode retains a worker through a tool wait;
2. request mode runs another ready task during that wait;
3. a next-step placeholder is inserted when its tool begins, not when it finishes;
4. workers skip pending entries and run the oldest runnable entry without repositioning skipped entries;
5. tool-response tokens are appended before a queued step becomes runnable;
6. history grows correctly across steps;
7. active requests never exceed concurrency;
8. zero-delay activation does not recurse or starve;
9. errors finalize once without reactivation;
10. deadlines work in runnable, running, and tool-pending states;
11. duplicate task IDs do not collide;
12. workers and timers terminate cleanly.

When the external server/model are available, run matched smoke tests:

```bash
uv run python -m phase1.runner --name smoke_task --n-tasks 8 --rate 0 --concurrency 2 --scheduler task --allow-battery
uv run python -m phase1.runner --name smoke_request --n-tasks 8 --rate 0 --concurrency 2 --scheduler request --allow-battery
```

Inspect result ordering, histories, timeline counts, cache reuse, terminal outcomes, and metadata. Performance claims require AC-powered matched runs.

## Risks and tradeoffs

- Better slot utilization may come with worse prompt-cache locality.
- Numeric concurrency has different operational meaning between modes.
- Reusing timeout categories favors result compatibility; diagnostic reasons retain precision.
- Additive timing fields expand output schemas but are needed to explain behavior.
- Two scheduler paths duplicate limited orchestration but protect baseline behavior.

## Assumptions requiring review

1. Use `--scheduler {task,request}`, not Boolean `--request-fifo`.
2. Keep task FIFO as default.
3. A later step receives FIFO position when its tool call begins. It retains that position while pending; workers skip it until runnable without moving it.
4. Tool-wait deadline maps to `timeout_queued`, with a specific error reason.
5. Start with matched N=8 comparisons instead of duplicating every concurrency run.
6. Additive timeline and queue-wait fields are acceptable schema changes.

## Feedback resolution

The requested queue semantics have been incorporated:

- the next-step placeholder is inserted when the tool call begins;
- the tool response is appended while that placeholder remains queued;
- the placeholder becomes runnable only after the response is appended;
- pending entries retain FIFO position but do not head-of-line block runnable work.

## Implementation todo list

### Phase 1: Preserve and expose scheduler configuration

- [x] Add validated `scheduler="task"` configuration to `RunConfig`.
- [x] Add `--scheduler {task,request}` to the runner CLI with `task` as default.
- [x] Include scheduler mode in summaries and existing metadata serialization.
- [x] Confirm an unsupported programmatic scheduler fails before server execution.
- [x] Run Python compilation validation.

### Phase 2: Add request-level state and ordered runnable selection

- [x] Add minimal persistent request-task state keyed by selected-task index.
- [x] Add an ordered scheduler collection that stores both runnable and tool-pending entries.
- [x] Use `asyncio.Condition` to wake workers on arrivals, tool completion, timeout, and shutdown.
- [x] Implement selection of the oldest runnable entry while leaving skipped pending entries in place.
- [x] Preserve the existing task-level execution path without semantic changes.
- [x] Run Python compilation validation.

### Phase 3: Implement one-step execution and tool activation

- [x] Execute exactly one `/completion` call per worker dequeue in request mode.
- [x] Append current step input before its model request.
- [x] Append generated tokens after a successful response.
- [x] Insert the next-step placeholder at the FIFO tail immediately when its tool call begins.
- [x] Release the worker before awaiting the tool delay.
- [x] Append the next step's tool-response tokens while its placeholder is queued.
- [x] Mark the placeholder runnable in place and notify workers without repositioning it.
- [x] Make zero-delay tools activate their queued placeholders without recursion or starvation.
- [x] Run Python compilation validation.

### Phase 4: Deadlines, failures, and clean termination

- [x] Add one exactly-once task-finalization helper.
- [x] Bound model calls by request timeout and remaining task deadline.
- [x] Map active-call deadline expiry to `timeout_running`.
- [x] Map runnable-queue and tool-pending deadline expiry to `timeout_queued` with diagnostic reasons.
- [x] Prevent terminal entries from being activated or dispatched.
- [x] Track all tool-wait coroutines and ensure none remain when the run exits.
- [x] Stop workers only after arrivals finish and every task is terminal.
- [x] Run Python compilation validation.

### Phase 5: Measurement and result compatibility

- [x] Preserve `admit` as first admission and existing `queue_s` semantics.
- [x] Add cumulative runnable-queue time to task records.
- [x] Add per-request queue-wait timing.
- [x] Extend timeline state with `runnable` and `tool_waiting` counts.
- [x] Keep existing outcome columns compatible with old analysis.
- [x] Make analysis interpret missing scheduler metadata as `task`.
- [x] Add scheduler comparison analysis and pending/runnable timeline support.
- [x] Run Python compilation validation.

### Phase 6: Focused experiment configuration and documentation

- [x] Add matched N=8 task/request configurations for synthetic and AgentInstruct workloads.
- [x] Give comparison runs unique result names so resume behavior remains correct.
- [x] Document scheduler semantics, queue activation, CLI commands, and comparison sweep in `README.md`.
- [x] Verify the sweep group using `--dry-run` without launching llama.cpp.

### Phase 7: Deterministic scheduler tests

- [x] Add a small scheduler test module using fake completions and tiny traces.
- [x] Test that task mode holds a worker during tool wait.
- [x] Test that request mode runs other runnable work during tool wait.
- [x] Test immediate placeholder insertion and retained FIFO position.
- [x] Test pending-entry skipping without repositioning.
- [x] Test tool-response append before activation and correct history growth.
- [x] Test concurrency bounds and zero-delay activation.
- [x] Test request errors and exactly-once finalization.
- [x] Test deadlines in runnable, running, and tool-pending states.
- [x] Test duplicate task IDs and clean worker/timer termination.
- [x] Run the complete scheduler test module.

### Phase 8: Final validation and handoff

- [x] Run `uv run python -m compileall phase1 analysis tests`.
- [x] Run `uv run python -m unittest tests.test_phase1_scheduler`.
- [x] Run `uv run python -m phase1.sweep --dry-run --groups scheduler`.
- [x] Review the diff for unintended changes or baseline behavior drift.
- [x] Mark every completed todo and document any external-server validation that could not run.

## Validation note

All local deterministic validation passed. A real llama.cpp/model integration run was not launched because it is a long hardware-dependent benchmark; the README commands and scheduler dry-run are verified.
