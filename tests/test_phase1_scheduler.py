import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from phase1.runner import RunConfig, run, run_request_fifo, run_task_fifo
from workloads.trace import Step, Task


def task(task_id, steps):
    return Task(task_id=task_id, cls="test", source="test", steps=steps)


class FakeComplete:
    def __init__(self, slow_tokens=None, error_tokens=None):
        self.calls = []
        self.active = 0
        self.max_active = 0
        self.slow_tokens = slow_tokens or {}
        self.error_tokens = error_tokens or set()

    async def __call__(self, session, url, prompt, n_predict, **kwargs):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.calls.append(list(prompt))
        await asyncio.sleep(self.slow_tokens.get(prompt[0], 0))
        self.active -= 1
        now = asyncio.get_running_loop().time()
        if prompt[0] in self.error_tokens:
            return {"t_send": now, "error": "fake failure", "tokens": []}
        return {"t_send": now, "error": None, "tokens": [900 + prompt[0]], "prompt_n": len(prompt),
                "cache_n": 0, "predicted_n": n_predict, "ttft_s": 0.001, "tpot_ms": 1.0}


class SchedulerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.srv = SimpleNamespace(url="http://test")

    def test_request_fifo_is_default(self):
        self.assertEqual(RunConfig(name="default").scheduler, "request")

    async def test_task_fifo_holds_worker_through_tool_wait(self):
        tasks = [
            task("a", [Step([10], 1, 0.02), Step([11], 1, None)]),
            task("b", [Step([20], 1, None)]),
        ]
        fake = FakeComplete()
        cfg = RunConfig(name="task", concurrency=1, arrival_rate=0, scheduler="task")
        with patch("phase1.runner.complete", fake):
            recs, _, _, _ = await run_task_fifo(cfg, self.srv, tasks, np.array([0.0, 0.001]))
        self.assertEqual([p[0] for p in fake.calls], [10, 10, 20])
        self.assertTrue(all(r.status == "ok" for r in recs))

    async def test_request_fifo_runs_other_task_during_tool_wait(self):
        tasks = [
            task("a", [Step([10], 1, 0.02), Step([11], 1, None)]),
            task("b", [Step([20], 1, None)]),
        ]
        fake = FakeComplete()
        cfg = RunConfig(name="request", concurrency=1, arrival_rate=0, scheduler="request")
        with patch("phase1.runner.complete", fake):
            recs, _, timeline, _ = await run_request_fifo(cfg, self.srv, tasks, np.array([0.0, 0.001]))
        self.assertEqual([p[0] for p in fake.calls], [10, 20, 10])
        self.assertEqual(fake.calls[2], [10, 910, 11])
        self.assertTrue(any(row["tool_waiting"] == 1 for row in timeline.rows))
        self.assertTrue(all(r.status == "ok" for r in recs))

    async def test_tool_completion_appends_step_to_fifo_tail(self):
        tasks = [
            task("a", [Step([10], 1, 0.015), Step([11], 1, None)]),
            task("b", [Step([20], 1, None)]),
            task("c", [Step([30], 1, None)]),
        ]
        fake = FakeComplete(slow_tokens={20: 0.03})
        cfg = RunConfig(name="position", concurrency=1, arrival_rate=0, scheduler="request")
        with patch("phase1.runner.complete", fake):
            await run_request_fifo(cfg, self.srv, tasks, np.array([0.0, 0.001, 0.005]))
        self.assertEqual([p[0] for p in fake.calls], [10, 20, 30, 10])

    async def test_tool_wait_deadline_finishes_without_next_request(self):
        tasks = [task("a", [Step([10], 1, 0.05), Step([11], 1, None)])]
        fake = FakeComplete()
        cfg = RunConfig(name="deadline", concurrency=1, arrival_rate=0, scheduler="request", deadline_s=0.01)
        with patch("phase1.runner.complete", fake):
            recs, _, timeline, _ = await run_request_fifo(cfg, self.srv, tasks, np.array([0.0]))
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(recs[0].status, "timeout_queued")
        self.assertEqual(recs[0].error, "deadline while waiting for tool")
        self.assertEqual(timeline.queued, 0)
        self.assertEqual(timeline.tool_waiting, 0)

    async def test_duplicate_task_ids_use_independent_state(self):
        tasks = [task("same", [Step([10], 1, None)]), task("same", [Step([20], 1, None)])]
        fake = FakeComplete()
        cfg = RunConfig(name="duplicates", concurrency=2, arrival_rate=0, scheduler="request")
        with patch("phase1.runner.complete", fake):
            recs, _, _, _ = await run_request_fifo(cfg, self.srv, tasks, np.array([0.0, 0.0]))
        self.assertEqual([r.status for r in recs], ["ok", "ok"])
        self.assertEqual([r.steps_done for r in recs], [1, 1])

    async def test_request_concurrency_is_bounded(self):
        tasks = [task(str(i), [Step([10 + i], 1, None)]) for i in range(6)]
        fake = FakeComplete(slow_tokens={10 + i: 0.005 for i in range(6)})
        cfg = RunConfig(name="bound", concurrency=2, arrival_rate=0, scheduler="request")
        with patch("phase1.runner.complete", fake):
            await run_request_fifo(cfg, self.srv, tasks, np.zeros(6))
        self.assertEqual(fake.max_active, 2)

    async def test_zero_delay_queues_then_activates_next_step(self):
        tasks = [
            task("a", [Step([10], 1, 0.0), Step([11], 1, None)]),
            task("b", [Step([20], 1, None)]),
        ]
        fake = FakeComplete()
        cfg = RunConfig(name="zero", concurrency=1, arrival_rate=0, scheduler="request")
        with patch("phase1.runner.complete", fake):
            recs, _, _, _ = await run_request_fifo(cfg, self.srv, tasks, np.zeros(2))
        self.assertEqual([p[0] for p in fake.calls], [10, 20, 10])
        self.assertEqual(fake.calls[2], [10, 910, 11])
        self.assertTrue(all(r.status == "ok" for r in recs))

    async def test_request_error_finalizes_without_next_step(self):
        tasks = [task("a", [Step([10], 1, 0.0), Step([11], 1, None)])]
        fake = FakeComplete(error_tokens={10})
        cfg = RunConfig(name="error", concurrency=1, arrival_rate=0, scheduler="request")
        with patch("phase1.runner.complete", fake):
            recs, _, timeline, _ = await run_request_fifo(cfg, self.srv, tasks, np.zeros(1))
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(recs[0].status, "failed")
        self.assertEqual(recs[0].error, "fake failure")
        self.assertEqual(timeline.queued, 0)

    async def test_deadline_during_request_is_running_timeout(self):
        tasks = [task("a", [Step([10], 1, None)])]
        fake = FakeComplete(slow_tokens={10: 0.05})
        cfg = RunConfig(name="running-deadline", concurrency=1, arrival_rate=0,
                        scheduler="request", deadline_s=0.01)
        with patch("phase1.runner.complete", fake):
            recs, _, _, _ = await run_request_fifo(cfg, self.srv, tasks, np.zeros(1))
        self.assertEqual(recs[0].status, "timeout_running")
        self.assertEqual(recs[0].error, "deadline during request")

    async def test_invalid_scheduler_rejected_before_loading_trace(self):
        cfg = RunConfig(name="invalid", scheduler="invalid")
        with patch("phase1.runner.load") as trace_load:
            with self.assertRaisesRegex(ValueError, "unknown scheduler"):
                await run(cfg, self.srv)
        trace_load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
