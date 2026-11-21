import asyncio
import unittest
from types import SimpleNamespace

import numpy as np

from phase1.runner import RunConfig
from phase2.estimator import RuntimeEstimator
from phase2.cache import EvictionResult
from phase2.runner import Phase2Config, run_phase2
from phase2.scheduler import ReadyRequest, SchedulerConfig, TwoQueueScheduler
from workloads.trace import Step, Task


def request(task, ready, need, runtime=1, deadline=100, sequence=None):
    return ReadyRequest(
        task_index=task,
        step_index=0,
        ready_at=ready,
        enqueue_sequence=task if sequence is None else sequence,
        prompt_tokens=need - 1,
        output_budget_tokens=1,
        predicted_runtime_s=runtime,
        deadline_at=deadline,
    )


class TwoQueueSchedulerTests(unittest.TestCase):
    def scheduler(self, **overrides):
        values = dict(capacity_cells=10, concurrency=2, aging_threshold_s=10)
        values.update(overrides)
        return TwoQueueScheduler(SchedulerConfig(**values))

    def test_fifo_when_heads_fit(self):
        scheduler = self.scheduler()
        scheduler.put(request(0, 0, 4))
        scheduler.put(request(1, 1, 4))
        first, _ = scheduler.select(2)
        second, _ = scheduler.select(2)
        self.assertEqual([first.request.task_index, second.request.task_index], [0, 1])
        self.assertEqual([first.reason, second.reason], ["fifo_head", "fifo_head"])

    def test_backfills_oldest_eligible_candidate(self):
        scheduler = self.scheduler(concurrency=1)
        scheduler.put(request(9, -1, 7))
        running, _ = scheduler.select(0)
        self.assertEqual(running.request.task_index, 9)
        scheduler.put(request(0, 1, 5, runtime=2))
        scheduler.put(request(1, 2, 3, runtime=2))
        scheduler.put(request(2, 3, 2, runtime=2))
        scheduler.free_workers.add(1)

        selection, decision = scheduler.select(4)
        self.assertEqual(selection.request.task_index, 1)
        self.assertEqual(selection.reason, "backfill")
        self.assertEqual(decision["candidate_count"], 2)
        self.assertEqual(scheduler.entries[0].task_index, 0)
        self.assertEqual(scheduler.entries[0].bypass_count, 1)

    def test_runtime_allowance_rejects_long_candidate(self):
        scheduler = self.scheduler(concurrency=1, aging_threshold_s=5)
        scheduler.put(request(9, -1, 8))
        scheduler.select(0)
        scheduler.put(request(0, 0, 5))
        scheduler.put(request(1, 1, 2, runtime=4))
        scheduler.free_workers.add(1)
        selection, decision = scheduler.select(2)
        self.assertIsNone(selection)
        self.assertEqual(decision["reason"], "head_blocked")

    def test_aged_head_disables_backfill(self):
        scheduler = self.scheduler(concurrency=1, aging_threshold_s=5)
        scheduler.put(request(9, -1, 8))
        scheduler.select(0)
        scheduler.put(request(0, 0, 5))
        scheduler.put(request(1, 1, 2, runtime=1))
        scheduler.free_workers.add(1)
        selection, decision = scheduler.select(5)
        self.assertIsNone(selection)
        self.assertEqual(decision["reason"], "aged_head_blocked")

    def test_tool_reserve_blocks_then_releases_admission(self):
        scheduler = self.scheduler(concurrency=1, reserve_per_waiting_task=3,
                                   max_tool_reserve_cells=3)
        scheduler.mark_tool_waiting(8)
        scheduler.put(request(0, 0, 8))
        selection, _ = scheduler.select(1)
        self.assertIsNone(selection)
        scheduler.clear_tool_waiting(8)
        selection, _ = scheduler.select(1)
        self.assertEqual(selection.request.task_index, 0)

    def test_reserve_horizon_ignores_distant_return(self):
        scheduler = self.scheduler(concurrency=1, reserve_per_waiting_task=3,
                                   max_tool_reserve_cells=3, reserve_horizon_s=5)
        scheduler.mark_tool_waiting(8, predicted_return_at=20)
        scheduler.put(request(0, 0, 8))
        selection, _ = scheduler.select(1)
        self.assertEqual(selection.request.task_index, 0)

    def test_idle_cache_is_counted_without_double_counting_own_prefix(self):
        scheduler = self.scheduler(concurrency=2)
        scheduler.set_idle_cache(0, 4)
        scheduler.set_idle_cache(9, 3)
        returning = request(0, 0, 6)
        scheduler.put(returning)
        self.assertEqual(scheduler.projected_used_cells(returning, 1), 9)
        selection, decision = scheduler.select(1)
        self.assertEqual(selection.request.task_index, 0)
        self.assertEqual(decision["idle_cache_cells"], 7)
        self.assertNotIn(0, scheduler.idle_cache_cells_by_task)

    def test_returning_request_prefers_its_retained_slot(self):
        scheduler = self.scheduler(concurrency=2)
        returning = request(0, 0, 2)
        returning.preferred_worker = 1
        scheduler.put(returning)
        selection, _ = scheduler.select(1)
        self.assertEqual(selection.worker, 1)

    def test_selection_excludes_grace_protected_worker(self):
        scheduler = self.scheduler(concurrency=2)
        scheduler.put(request(0, 0, 2))
        selection, _ = scheduler.select(1, eligible_workers={1})
        self.assertEqual(selection.worker, 1)

    def test_evicted_ready_task_loses_stale_slot_preference(self):
        scheduler = self.scheduler(concurrency=2)
        returning = request(0, 0, 2)
        returning.preferred_worker = 1
        scheduler.put(returning)
        scheduler.clear_preferred_worker(0)
        selection, _ = scheduler.select(1)
        self.assertEqual(selection.worker, 0)

    def test_reservations_are_atomic_and_released_once(self):
        scheduler = self.scheduler(concurrency=1)
        scheduler.put(request(0, 0, 4))
        scheduler.put(request(1, 1, 4))
        scheduler.select(1)
        selection, _ = scheduler.select(1)
        self.assertIsNone(selection)
        released = scheduler.release(0)
        self.assertEqual(released.task_index, 0)
        with self.assertRaises(KeyError):
            scheduler.release(0)

    def test_expired_and_infeasible_requests(self):
        scheduler = self.scheduler(safety_margin_cells=1)
        expired = request(0, 0, 2, deadline=2)
        impossible = request(1, 1, 10)
        scheduler.put(expired)
        scheduler.put(impossible)
        self.assertEqual(scheduler.expired(2), [expired])
        self.assertTrue(scheduler.infeasible(impossible))

    def test_duplicate_task_cannot_be_ready_twice(self):
        scheduler = self.scheduler()
        scheduler.put(request(0, 0, 2))
        with self.assertRaisesRegex(ValueError, "already ready"):
            scheduler.put(request(0, 1, 2))

    def test_grace_return_can_receive_bounded_resume_priority(self):
        scheduler = self.scheduler()
        scheduler.put(request(0, 0, 2))
        scheduler.put(request(1, 1, 2), prioritize=True)
        selection, _ = scheduler.select(2)
        self.assertEqual(selection.request.task_index, 1)

    def test_rejects_configuration_that_can_reserve_entire_pool(self):
        with self.assertRaisesRegex(ValueError, "maximum reserve"):
            SchedulerConfig(capacity_cells=10, concurrency=1, aging_threshold_s=1,
                            safety_margin_cells=2, max_tool_reserve_cells=8).validate()


class FakeComplete:
    def __init__(self, delays=None):
        self.delays = delays or {}
        self.calls = []
        self.slots = []

    async def __call__(self, session, url, prompt, n_predict, **kwargs):
        self.calls.append(prompt[0])
        self.slots.append(kwargs.get("id_slot"))
        await asyncio.sleep(self.delays.get(prompt[0], 0))
        now = asyncio.get_running_loop().time()
        return {
            "t_send": now,
            "error": None,
            "tokens": [900 + prompt[0]],
            "prompt_n": len(prompt),
            "cache_n": 0,
            "predicted_n": n_predict,
            "ttft_s": 0.001,
            "tpot_ms": 1.0,
        }


class Phase2RunnerTests(unittest.IsolatedAsyncioTestCase):
    def make_task(self, task_id, first, steps=1, delay=0):
        values = [Step([first], 1, delay if steps > 1 else None)]
        if steps > 1:
            values.append(Step([first + 1], 1, None))
        return Task(task_id, "test", "test", values)

    def config(self, **values):
        run = RunConfig(name="test", n_tasks=2, arrival_rate=0, concurrency=1,
                        deadline_s=1, request_timeout_s=1)
        run.server.ctx = values.pop("ctx", 20)
        return Phase2Config(run=run, aging_threshold_s=values.pop("aging", 0.2),
                            backfill_guard_s=0, kv_safety_margin_cells=0, **values)

    async def test_runner_preserves_fifo_when_requests_fit(self):
        tasks = [self.make_task("a", 10), self.make_task("b", 20)]
        fake = FakeComplete()
        recs, requests, _, decisions, _, _ = await run_phase2(
            self.config(), SimpleNamespace(url="http://test"), tasks, np.zeros(2),
            RuntimeEstimator(0.001, 0, 0, 0.001), fake)
        self.assertEqual(fake.calls, [10, 20])
        self.assertEqual([record.status for record in recs], ["ok", "ok"])
        self.assertEqual([row["scheduler_reason"] for row in requests], ["fifo_head", "fifo_head"])
        self.assertEqual([row["reason"] for row in decisions if row.get("selected_task_index") is not None],
                         ["fifo_head", "fifo_head"])

    async def test_runner_requeues_after_tool_wait(self):
        tasks = [self.make_task("a", 10, steps=2, delay=0.01), self.make_task("b", 20)]
        fake = FakeComplete()
        recs, _, timeline, _, _, _ = await run_phase2(
            self.config(), SimpleNamespace(url="http://test"), tasks, np.zeros(2),
            RuntimeEstimator(0.001, 0, 0, 0.001), fake)
        self.assertEqual(fake.calls, [10, 20, 10])
        self.assertTrue(all(record.status == "ok" for record in recs))
        self.assertTrue(any(row["tool_waiting"] == 1 for row in timeline.rows))

    async def test_runner_marks_infeasible_without_calling_server(self):
        tasks = [Task("large", "test", "test", [Step([10] * 20, 1, None)])]
        fake = FakeComplete()
        recs, _, _, _, _, _ = await run_phase2(
            self.config(ctx=10), SimpleNamespace(url="http://test"), tasks, np.zeros(1),
            RuntimeEstimator(0.001, 0, 0, 0.001), fake)
        self.assertEqual(fake.calls, [])
        self.assertEqual(recs[0].status, "infeasible")

    async def test_slot_aware_runner_evicts_waiting_cache_and_recomputes(self):
        tasks = [self.make_task("a", 10, steps=2, delay=0.01), self.make_task("b", 20)]
        fake = FakeComplete()
        config = self.config()
        config.eviction_policy = "slot_aware"

        def fake_erase(url, slot_id, active_slots):
            self.assertNotIn(slot_id, active_slots)
            return EvictionResult(slot_id, 100, 50)

        recs, requests, _, _, evictions, _ = await run_phase2(
            config, SimpleNamespace(url="http://test"), tasks, np.zeros(2),
            RuntimeEstimator(0.001, 0, 0, 0.001), fake, fake_erase)
        self.assertEqual(fake.calls, [10, 20, 10])
        self.assertEqual(fake.slots, [0, 0, 0])
        self.assertTrue(all(record.status == "ok" for record in recs))
        self.assertTrue(requests[-1]["cache_was_evicted"])
        self.assertTrue(any(row["reason"] == "slot_reassignment" for row in evictions))

    async def test_adaptive_grace_preserves_slot_until_tool_returns(self):
        tasks = [self.make_task("a", 10, steps=2, delay=0.01), self.make_task("b", 20)]
        fake = FakeComplete()
        config = self.config()
        config.eviction_policy = "slot_aware"
        config.cache_grace_s = 0.05

        def fake_erase(url, slot_id, active_slots):
            self.assertNotIn(slot_id, active_slots)
            return EvictionResult(slot_id, 100, 50)

        recs, requests, _, decisions, evictions, _ = await run_phase2(
            config, SimpleNamespace(url="http://test"), tasks, np.zeros(2),
            RuntimeEstimator(0.001, 0, 0, 0.001), fake, fake_erase)
        self.assertTrue(all(record.status == "ok" for record in recs))
        self.assertEqual(fake.calls, [10, 10, 20])
        self.assertFalse(any(row["reason"] == "slot_reassignment" for row in evictions))
        self.assertTrue(requests[1]["cache_grace_hit"])
        self.assertTrue(any(row["reason"] == "adaptive_grace_hold" for row in decisions))


if __name__ == "__main__":
    unittest.main()
