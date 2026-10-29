# FIFO KV Admission Diagnostic Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Record, without changing FIFO order, when the oldest ready request exceeds an estimated KV-token budget while a later request fits.

**Architecture:** At each request-worker dequeue, calculate full prompt length plus output budget for the head and queued candidates. Track the same budget for active requests, write one diagnostic row per dispatch, and summarize head-over-budget and size-only backfill-opportunity counts in run metadata.

**Tech Stack:** Python 3.12, asyncio, pandas, unittest.

**Spec:** Discussion in this thread on 2026-10-28: configured KV capacity minus active request budgets; compare the oldest ready request with later ready requests; keep scheduling unchanged.

## Global Constraints

- Run only the diagnostic on request FIFO; task FIFO behavior remains unchanged.
- Treat token counts as budget estimates, not direct GPU occupancy measurements; retained idle cache can make the estimate optimistic.
- Preserve the existing `run_request_fifo` return shape and FIFO dispatch order.

## Review Focus

- A second worker can become free while another request remains active; the diagnostic must include that active request's full budget.
- Later candidates are read from the queue without removing or reordering them.
- Duplicate task IDs remain distinguishable through task indices.
- Empty or single-item queues produce zero backfill candidates.
- Request errors and timeouts remove active reservations.

---

### Task 1: Record KV fit at FIFO dispatch

**Files:** Modify `phase1/runner.py`; test `tests/test_phase1_scheduler.py`.

- [x] Add a failing asynchronous test with two active requests followed by a blocked head and a fitting later request; assert the diagnostic row and unchanged FIFO call order.
- [x] Run the test and confirm it fails because admission diagnostics are absent.
- [x] Track per-worker active token budgets and record one row at each request dequeue, using `len(state.history) + step.n_gen` for request need.
- [x] Run the focused test and all scheduler tests.

### Task 2: Persist and summarize the diagnostic

**Files:** Modify `phase1/runner.py`; test `tests/test_phase1_scheduler.py`; update `README.md`.

- [x] Add a failing test for the summary counts derived from recorded decisions.
- [x] Write `admission_checks.csv` and add decision, head-over-budget, and backfill-opportunity counts to `meta.json` summary for request FIFO runs.
- [x] Document the estimate and its limits, then run the full test suite.
