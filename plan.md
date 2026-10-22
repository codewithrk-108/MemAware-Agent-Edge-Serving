# Plan: ready-time request FIFO without head-of-line blocking

> I want to build a new feature, ready-time request-level FIFO scheduling, that extends the system to keep inference workers processing ready model requests while other tasks wait for tools, eliminating tool-induced head-of-line blocking.

## Feature summary and business outcome

Change the default `request` scheduler on `main` so a task is absent from the runnable FIFO while its tool call is outstanding. When the tool resolves, append the tool-response tokens to the task history and place its next model step at the FIFO tail.

The intended outcome is better inference-slot utilization and throughput for multi-step agent workloads without changing task dependencies, Poisson arrivals, prompt caching, deadlines, workload traces, or llama.cpp request behavior.

The explicit `task` scheduler remains available and unchanged. No new scheduler name or CLI flag is required.

## Required behavior

### Core scheduling requirements

1. `request` remains the default scheduler in `RunConfig` and the CLI.
2. An initial task step enters the FIFO when its task arrives.
3. A worker removes the oldest ready step and performs exactly one `/completion` request.
4. After a successful non-final model step:
   - append generated tokens to persistent task history;
   - release the inference worker;
   - transition the task to `tool_waiting`;
   - do not leave or insert any placeholder in the runnable FIFO.
5. Other ready requests must continue decoding during the tool wait, subject only to the configured request concurrency.
6. When the tool resolves:
   - append the next step's tool-response/input tokens to task history;
   - transition the task from `tool_waiting` to ready;
   - append it to the FIFO tail;
   - notify an idle worker.
7. A resumed task receives FIFO priority based on tool-completion time, not original task arrival or tool-start time.
8. Steps belonging to one task remain strictly sequential.
9. A final model step completes the task without starting a tool waiter or re-entering FIFO.
10. No worker or llama-server slot is held during tool waiting.

### Example ordering

```text
Initial ready FIFO: [A1, B1, C1]

A1 decodes and starts a tool
Ready FIFO: [B1, C1]
Tool-waiting: [A]

B1 and C1 may decode while A waits

A's tool resolves
Append tool response to A history
Ready FIFO: [...already-ready work..., A2]
```

There must be no pending A placeholder at the FIFO head or tail before its tool completes.

## Pre-implementation current-state summary

The current `main` implementation in `phase1/runner.py` deliberately models head-of-line blocking:

- `RunnableFifo.put_pending_head()` inserts a non-runnable placeholder at index zero.
- `RunnableFifo.take()` examines only index zero and waits if that entry is pending.
- `wait_for_tool()` calls `resolve_to_tail()` after appending the tool response.
- Consequently, a pending tool at the head prevents every worker from dispatching later ready entries.

The surrounding infrastructure is already suitable for the proposed change:

- `RequestTaskState` persists history and the next-step cursor across workers.
- tool waits already execute in independent asyncio tasks;
- completion and deadline handling already use an exactly-once terminal transition;
- `Timeline` already distinguishes `queued`, `runnable`, `running`, and `tool_waiting`;
- request concurrency already equals the number of workers and llama-server slots;
- deterministic scheduler tests already use fake completions and tiny traces.

The relevant findings and historical scheduler variants are recorded in `research.md`.

## Proposed technical approach

### Ready-only FIFO

Keep `RunnableFifo` as the scheduler-owned ordered collection, but make it contain only runnable model steps.

Remove pending-entry operations:

```python
put_pending_head(state)
resolve_to_tail(state)
remove(state)  # if only used for queued tool placeholders
```

Restore ordinary FIFO removal:

```python
async def take(self):
    async with self.changed:
        while True:
            if self.entries:
                return self.entries.pop(0)
            if self.closed:
                return None
            await self.changed.wait()
```

All entries should be runnable by construction, so the scheduler does not need to scan, skip, or block on placeholder state.

### Tool-wait lifecycle

After a non-final completion:

```python
state.history += generated_tokens
state.next_step += 1
timeline.running -= 1
timeline.tool_waiting += 1
create_task(wait_for_tool(state, scaled_delay))
```

The state is not inserted into `scheduler.entries` at this point.

When the tool resolves:

```python
state.history += next_step.input
state.enqueued_at = now
state.runnable_at = now
await scheduler.put(state)
timeline.tool_waiting -= 1
timeline.queued += 1
timeline.runnable += 1
```

This preserves the existing trace interpretation: `next_step.input` represents the tool/environment response that becomes available after the modeled delay.

### Deadline behavior

Task deadlines remain end-to-end from original arrival:

- deadline expires during an active model request: `timeout_running`;
- deadline expires in ready FIFO: `timeout_queued` with `deadline while queued`;
- deadline expires during tool wait: `timeout_queued` with `deadline while waiting for tool`.

Because a tool-waiting task is no longer in FIFO, tool timeout cleanup must not decrement `queued` or remove a scheduler entry. It should decrement only `tool_waiting` and finalize the task.

### Timing and result semantics

- `queued`: ready model steps only.
- `runnable`: same cardinality as `queued` in request mode.
- `running`: active llama.cpp requests.
- `tool_waiting`: tasks awaiting modeled tools outside FIFO.
- `queue_s`: arrival to first admission, unchanged.
- `ready_queue_s`: cumulative time spent runnable in FIFO; tool latency is excluded.
- `tool_s`: modeled tool latency, unchanged.

No CSV or JSON field needs to be removed or renamed.

## Files to modify

### Required

- `phase1/runner.py`
  - remove pending-head insertion and strict head blocking;
  - enqueue a state only after its tool resolves;
  - adjust timeline changes and tool-timeout cleanup;
  - update module/class documentation.
- `tests/test_phase1_scheduler.py`
  - replace head-blocking assertions with no-blocking assertions;
  - verify ready-time tail ordering and multi-worker utilization;
  - retain deadline, history, concurrency, duplicate-ID, and shutdown tests.
- `README.md`
  - describe tool-waiting tasks as outside FIFO;
  - remove head-of-line-blocking claims;
  - retain request mode as default and task mode as the explicit alternative.
- `research.md`
  - document the proposed main-branch transition and distinguish it from historical branch behavior.

### Not required

- `harness/client.py` and `harness/server.py`
- Qwen model configuration and AgentInstruct template
- workload trace schemas or builders
- `phase1/sweep.py` experiment names/configurations
- `analysis/plot_phase1.py`, because its existing timeline columns remain compatible
- microbenchmarks
- `fifo-improved` branch

## Error handling and correctness requirements

- Never enqueue a state before its tool response has been appended.
- Never enqueue a terminal or timed-out state.
- Finalize each task at most once.
- Track every tool coroutine and await it before scheduler shutdown.
- Do not decrement FIFO counters when a tool-waiting task times out outside FIFO.
- Preserve generated-token history before starting a tool wait.
- Preserve the next-step index across workers.
- Keep state keyed by selected-task index because sampled task IDs can repeat.
- Zero-delay tools must still yield and enqueue at the tail rather than recursively executing the next step in the same worker.

## Test and validation strategy

There is no configured static typechecker. Use compilation, deterministic unit tests, CLI dry-run, and manual result inspection.

### Automated validation

```bash
.venv/bin/python -m compileall phase1 analysis tests
.venv/bin/python -m unittest discover -s tests
.venv/bin/python -m phase1.sweep --dry-run --groups scheduler
git diff --check
```

Scheduler tests must demonstrate:

1. B starts before A's tool completes when B is already ready.
2. With multiple workers, an A tool wait does not idle workers while ready entries exist.
3. If B and C are ready before A's tool resolves, order is B, C, then A's next step.
4. A's next prompt contains A's earlier prompt, generated tokens, and tool-response tokens.
5. Zero-delay tool completion appends A at the tail behind work already ready.
6. Active model calls never exceed `concurrency`.
7. Tool timeout leaves no FIFO entry and produces `timeout_queued`.
8. Request errors do not start a tool waiter.
9. Duplicate task IDs maintain independent state.
10. Workers and tool coroutines terminate cleanly.

### Manual verification

Run small matched task/request experiments with distinct names and inspect:

- request ordering in `requests.csv`;
- `queued`, `running`, and `tool_waiting` in `timeline.csv`;
- task histories through prompt lengths and cache reuse;
- terminal states in `tasks.csv`;
- scheduler metadata in `meta.json`.

Do not claim performance improvement until matched AC-powered llama.cpp runs are collected.

## Risks and tradeoffs

- More task interleaving can reduce llama.cpp prompt-cache locality and increase prefill work.
- An individual task may complete later because its resumed step joins behind all already-ready work.
- System throughput and utilization may improve even when per-task latency worsens.
- Request-level FIFO changes fairness from original-task arrival order to step-ready order.
- Existing Phase 1 results produced by the head-blocking scheduler should not be mixed with new results under identical run names without clear provenance or regeneration.

## Assumptions for review

1. Tool-waiting tasks should be completely absent from FIFO, rather than represented by skippable placeholders.
2. A resumed step joins the tail at tool-completion time.
3. `request` remains the default scheduler; `task` remains unchanged.
4. Tool-wait deadline continues to map to `timeout_queued` for result compatibility.
5. Existing timeline/result field names remain unchanged.
6. `fifo-improved` is not modified as part of this work.

## Feedback requested

The user confirmed the ready-time FIFO design after reviewing its difference from `fifo-improved`: tool-waiting
tasks must remain completely outside FIFO and receive a new tail position only when their tools resolve.

## Implementation todo list

### Phase 1: Simplify the scheduler to a ready-only FIFO

- [x] Update `RunnableFifo` documentation to state that every stored entry is runnable.
- [x] Remove `put_pending_head`.
- [x] Remove `resolve_to_tail`.
- [x] Remove pending-entry `remove` behavior if it has no remaining ready-queue use.
- [x] Change `take` to remove the oldest entry directly without checking a runnable flag.
- [x] Retain condition-based worker notification and clean shutdown.
- [x] Run Python compilation validation.

### Phase 2: Move tool waiting completely outside FIFO

- [x] After a successful non-final model step, append generated tokens and advance the step cursor.
- [x] Release the worker and increment `tool_waiting` without incrementing `queued` or inserting a placeholder.
- [x] Preserve task history and state while the asynchronous tool coroutine runs.
- [x] On tool completion, append the next step's input/tool-response tokens to history.
- [x] Set new enqueue and runnable timestamps at tool-completion time.
- [x] Append the resumed task to the FIFO tail as a runnable entry.
- [x] Update timeline counters for tool-wait exit and ready-queue entry.
- [x] Ensure zero-delay tools use the same asynchronous tail-enqueue path.
- [x] Run Python compilation validation.

### Phase 3: Correct deadlines and terminal transitions

- [x] Keep active-request deadline expiry mapped to `timeout_running`.
- [x] Keep ready-FIFO expiry mapped to `timeout_queued` with `deadline while queued`.
- [x] Keep tool-wait expiry mapped to `timeout_queued` with `deadline while waiting for tool`.
- [x] Remove scheduler-entry deletion and queue decrement from tool-wait timeout handling.
- [x] Prevent timed-out or terminal tasks from entering FIFO after tool completion.
- [x] Preserve exactly-once finalization and all-done signaling.
- [x] Await every tool coroutine before scheduler shutdown.
- [x] Run Python compilation validation.

### Phase 4: Preserve metrics and result compatibility

- [x] Define `queued` and `runnable` as ready model steps only.
- [x] Keep `tool_waiting` as tasks outside FIFO awaiting tools.
- [x] Preserve `running` as active llama.cpp requests.
- [x] Preserve `queue_s`, `ready_queue_s`, `tool_s`, and existing task/request output schemas.
- [x] Verify tool latency is excluded from ready-queue waiting time.
- [x] Confirm scheduler metadata and default `request` mode remain unchanged.
- [x] Run Python compilation validation.

### Phase 5: Replace head-blocking tests with ready-time FIFO tests

- [x] Replace the single-worker head-blocking timing assertion with proof that B starts before A's tool resolves.
- [x] Replace the all-workers-blocked test with proof that ready work continues while A waits.
- [x] Verify B and C execute before A2 when already ready at A's tool completion.
- [x] Verify a request arriving during A's tool wait remains ahead of A2 if ready before tool completion.
- [x] Verify A2 history contains A1 input, A1 generated tokens, and the tool response.
- [x] Verify zero-delay A2 joins behind already-ready work.
- [x] Retain concurrency-bound, request-error, deadline, duplicate-ID, default-mode, and shutdown coverage.
- [x] Run the complete test suite.

### Phase 6: Update documentation and research artifacts

- [x] Update the runner module docstring to describe ready-time FIFO.
- [x] Update `README.md` to show tool-waiting tasks outside FIFO and no head-of-line blocking.
- [x] Remove claims that `main` inserts pending placeholders at the head.
- [x] Preserve the documented distinction from `fifo-improved`, which reserves a skippable tail placeholder.
- [x] Update `research.md` if implementation findings differ from the planned design.

### Phase 7: Final validation and review handoff

- [x] Run `.venv/bin/python -m compileall phase1 analysis tests`.
- [x] Run `.venv/bin/python -m unittest discover -s tests`.
- [x] Run `.venv/bin/python -m phase1.sweep --dry-run --groups scheduler`.
- [x] Run `git diff --check`.
- [x] Review the diff for unintended changes to Qwen configuration, task scheduling, result schemas, or `fifo-improved`.
- [x] Mark completed checklist items in this plan.
- [x] Leave all changes uncommitted for local review.

## Implementation status

Implemented and locally validated. All changes remain uncommitted for review.
