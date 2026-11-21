"""Phase 2 predictive two-queue scheduler in front of llama-server."""

import argparse
import asyncio
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from harness.client import complete, new_session
from harness.kv_memory import KvMemSampler
from harness.memory import GpuMemSampler, MiB
from harness.results import RESULTS, check_power, environment
from harness.server import Server, ServerConfig
from phase1.runner import RequestTaskState, RunConfig, TaskRecord, Timeline, summarize
from phase2.cache import EvictionVerificationError, SlotCacheManager, erase_idle_slot
from phase2.estimator import RuntimeEstimator
from phase2.freeze import verify_frozen_policy
from phase2.scheduler import ReadyRequest, SchedulerConfig, Selection, TwoQueueScheduler
from workloads.trace import load

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Phase2Config:
    run: RunConfig
    aging_threshold_s: float = 30.0
    backfill_guard_s: float = 1.0
    kv_safety_margin_cells: int = 512
    reserve_per_waiting_task: int = 0
    max_tool_reserve_cells: int = 0
    reserve_horizon_s: float = 5.0
    estimated_tool_delay_s: float = 0.2
    timing_mode: str = "estimated"
    eviction_policy: str = "none"
    cache_grace_s: float = 0.0
    max_consecutive_resume_priorities: int = 0
    resume_priority_head_age_limit_s: float = 0.0

    def scheduler_config(self) -> SchedulerConfig:
        return SchedulerConfig(
            capacity_cells=self.run.server.ctx,
            concurrency=self.run.concurrency,
            aging_threshold_s=self.aging_threshold_s,
            backfill_guard_s=self.backfill_guard_s,
            safety_margin_cells=self.kv_safety_margin_cells,
            reserve_per_waiting_task=self.reserve_per_waiting_task,
            max_tool_reserve_cells=self.max_tool_reserve_cells,
            reserve_horizon_s=self.reserve_horizon_s,
        )

    def validate(self) -> None:
        self.scheduler_config().validate()
        if self.run.scheduler != "request":
            raise ValueError("Phase 2 supports request-level scheduling only")
        if self.timing_mode not in {"estimated", "oracle"}:
            raise ValueError("timing_mode must be estimated or oracle")
        if self.estimated_tool_delay_s < 0:
            raise ValueError("estimated tool delay must be non-negative")
        if self.cache_grace_s < 0:
            raise ValueError("cache grace must be non-negative")
        if self.max_consecutive_resume_priorities < 0:
            raise ValueError("maximum consecutive resume priorities must be non-negative")
        if self.resume_priority_head_age_limit_s < 0:
            raise ValueError("resume priority head age limit must be non-negative")
        if self.eviction_policy not in {"none", "slot_aware"}:
            raise ValueError("eviction_policy must be none or slot_aware")
        if self.cache_grace_s and self.eviction_policy != "slot_aware":
            raise ValueError("cache grace requires slot-aware eviction")
        if ((self.max_consecutive_resume_priorities or self.resume_priority_head_age_limit_s)
                and not self.cache_grace_s):
            raise ValueError("resume priority fairness requires cache grace")


def verify_evaluation_config(cfg: Phase2Config, estimator_path: Path, artifact: dict) -> None:
    policy = artifact["policy"]
    fields = (
        "aging_threshold_s", "backfill_guard_s", "kv_safety_margin_cells",
        "reserve_per_waiting_task", "max_tool_reserve_cells", "reserve_horizon_s",
        "estimated_tool_delay_s", "timing_mode", "cache_grace_s",
        "max_consecutive_resume_priorities", "resume_priority_head_age_limit_s",
    )
    optional_zero = {"cache_grace_s", "max_consecutive_resume_priorities",
                     "resume_priority_head_age_limit_s"}
    expected = {field: policy.get(field, 0 if field in optional_zero else None)
                for field in fields}
    mismatches = {field: (getattr(cfg, field), expected[field])
                  for field in fields if getattr(cfg, field) != expected[field]}
    if mismatches:
        raise ValueError(f"evaluation configuration differs from frozen policy: {mismatches}")
    frozen_estimator = Path(artifact["runtime_estimator"]["path"])
    if estimator_path.resolve() != frozen_estimator.resolve():
        raise ValueError("evaluation runtime estimator differs from frozen policy")


def _ready_request(state: RequestTaskState, task, rec: TaskRecord, ready_at: float,
                   sequence: int, estimator: RuntimeEstimator, deadline_s: float,
                   preferred_worker: Optional[int] = None,
                   resume_priority: bool = False) -> ReadyRequest:
    step = task.steps[state.next_step]
    runtime, fallback = estimator.predict(len(state.history), step.n_gen)
    return ReadyRequest(
        task_index=state.task_index,
        step_index=state.next_step,
        ready_at=ready_at,
        enqueue_sequence=sequence,
        prompt_tokens=len(state.history),
        output_budget_tokens=step.n_gen,
        predicted_runtime_s=runtime,
        deadline_at=rec.arrival + deadline_s,
        runtime_fallback=fallback,
        preferred_worker=preferred_worker,
        resume_priority=resume_priority,
    )


async def run_phase2(cfg: Phase2Config, srv, tasks: list, offsets,
                     estimator: RuntimeEstimator, complete_fn=complete,
                     erase_fn=erase_idle_slot) -> tuple:
    cfg.validate()
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    scheduler = TwoQueueScheduler(cfg.scheduler_config())
    states = [RequestTaskState(i) for i in range(len(tasks))]
    recs = [TaskRecord(task.task_id, task.cls, task.n_steps, task.total_tokens) for task in tasks]
    requests: list[dict] = []
    decisions: list[dict] = []
    timeline = Timeline(t0)
    condition = asyncio.Condition()
    completed = 0
    enqueue_sequence = 0
    request_jobs: list[asyncio.Task] = []
    tool_jobs: list[asyncio.Task] = []
    slot_manager = SlotCacheManager(cfg.run.concurrency) if cfg.eviction_policy == "slot_aware" else None
    evicted_tasks: set[int] = set()
    evictions: list[dict] = []
    grace_deadline_by_task: dict[int, float] = {}
    grace_hit_tasks: set[int] = set()
    resume_priority_streak = 0
    pending_resume_priorities = 0

    def finish(state: RequestTaskState, status: str, error: Optional[str] = None) -> bool:
        nonlocal completed
        if state.terminal:
            return False
        state.terminal = True
        rec = recs[state.task_index]
        rec.status, rec.error, rec.finish = status, error, loop.time()
        completed += 1
        return True

    async def notify() -> None:
        async with condition:
            condition.notify_all()

    async def evict_slot(slot_id: int, reason: str) -> None:
        if slot_manager is None:
            return
        owner = slot_manager.owner(slot_id)
        if owner is None:
            return
        estimated_cells = slot_manager.resident_cells_by_task.get(owner, 0)
        grace_deadline = grace_deadline_by_task.pop(owner, None)
        error = None
        result = None
        try:
            result = await asyncio.to_thread(
                erase_fn, srv.url, slot_id, set(scheduler.active))
        except EvictionVerificationError as exc:
            error = str(exc)
        forgotten_owner, _ = slot_manager.forget_slot(slot_id)
        scheduler.evict_idle_cache(owner)
        scheduler.clear_preferred_worker(owner)
        if forgotten_owner is not None and not states[forgotten_owner].terminal:
            evicted_tasks.add(forgotten_owner)
        evictions.append({
            "t": loop.time() - t0,
            "slot_id": slot_id,
            "task_index": owner,
            "task_id": tasks[owner].task_id,
            "reason": reason,
            "estimated_resident_cells": estimated_cells,
            "used_cells_before": result.used_cells_before if result else None,
            "used_cells_after": result.used_cells_after if result else None,
            "reclaimed_cells": result.reclaimed_cells if result else None,
            "verification_error": error,
            "grace_expired": grace_deadline is not None and loop.time() >= grace_deadline,
        })

    async def cleanup_terminal_slot(task_index: int, reason: str) -> None:
        if slot_manager is None:
            return
        slot_id = slot_manager.preferred_slot(task_index)
        if slot_id is not None:
            await evict_slot(slot_id, reason)

    async def wait_for_tool(state: RequestTaskState, delay: float) -> None:
        nonlocal enqueue_sequence, pending_resume_priorities
        rec = recs[state.task_index]
        left = rec.arrival + cfg.run.deadline_s - loop.time()
        if left <= 0 or delay >= left:
            if left > 0:
                await asyncio.sleep(left)
            async with condition:
                grace_deadline_by_task.pop(state.task_index, None)
                scheduler.clear_tool_waiting(state.task_index)
                timeline.mark(loop.time(), dtool=-1)
                finish(state, "timeout_queued", "deadline while waiting for tool")
                condition.notify_all()
            return
        await asyncio.sleep(delay)
        async with condition:
            if state.terminal:
                return
            grace_deadline = grace_deadline_by_task.pop(state.task_index, None)
            grace_hit = (grace_deadline is not None and loop.time() <= grace_deadline
                         and slot_manager is not None
                         and slot_manager.preferred_slot(state.task_index) is not None)
            if grace_hit:
                grace_hit_tasks.add(state.task_index)
            scheduler.clear_tool_waiting(state.task_index)
            step = tasks[state.task_index].steps[state.next_step]
            state.history = state.history + step.input
            now = loop.time()
            state.enqueued_at = state.runnable_at = now
            state.runnable = True
            enqueue_sequence += 1
            head_age = max(0.0, now - scheduler.entries[0].ready_at) if scheduler.entries else 0.0
            quota_blocked = bool(scheduler.entries) and (
                pending_resume_priorities > 0
                or (cfg.max_consecutive_resume_priorities > 0
                    and resume_priority_streak >= cfg.max_consecutive_resume_priorities)
            )
            age_blocked = (
                bool(scheduler.entries) and cfg.resume_priority_head_age_limit_s > 0
                and head_age >= cfg.resume_priority_head_age_limit_s
            )
            grant_priority = grace_hit and not quota_blocked and not age_blocked
            if grant_priority:
                pending_resume_priorities += 1
            if grace_hit:
                decisions.append({
                    "decision_sequence": None,
                    "t": now - t0,
                    "reason": ("resume_priority_granted" if grant_priority else
                               "resume_priority_denied_age" if age_blocked else
                               "resume_priority_denied_quota"),
                    "selected_task_index": None,
                    "returning_task_index": state.task_index,
                    "queue_length": len(scheduler.entries),
                    "head_age_s": head_age,
                    "resume_priority_streak": resume_priority_streak,
                })
            scheduler.put(_ready_request(state, tasks[state.task_index], rec, now,
                                         enqueue_sequence, estimator, cfg.run.deadline_s,
                                         slot_manager.preferred_slot(state.task_index)
                                         if slot_manager else None,
                                         resume_priority=grant_priority),
                          prioritize=grant_priority)
            timeline.mark(now, dq=+1, dready=+1, dtool=-1)
            condition.notify_all()

    async with new_session(limit=cfg.run.concurrency + 8) as session:
        async def execute_request(selection: Selection) -> None:
            state = states[selection.request.task_index]
            rec = recs[state.task_index]
            task = tasks[state.task_index]
            step = task.steps[state.next_step]
            started = loop.time()
            left = rec.arrival + cfg.run.deadline_s - started
            outcome = "ok"
            response = None
            cache_was_evicted = state.task_index in evicted_tasks
            cache_grace_return = state.task_index in grace_hit_tasks
            cache_grace_hit = (
                cache_grace_return and not cache_was_evicted and slot_manager is not None
                and slot_manager.preferred_slot(state.task_index) == selection.worker
            )
            grace_hit_tasks.discard(state.task_index)
            if slot_manager:
                owner = slot_manager.owner(selection.worker)
                if owner is not None and owner != state.task_index:
                    raise RuntimeError(f"slot {selection.worker} still owned by task {owner}")
                slot_manager.assign(selection.worker, state.task_index)
                evicted_tasks.discard(state.task_index)
            try:
                response = await asyncio.wait_for(
                    complete_fn(
                        session, srv.url, state.history, step.n_gen,
                        cache_prompt=cfg.run.cache_prompt, return_tokens=True,
                        id_slot=selection.worker if slot_manager else -1,
                        timeout=min(cfg.run.request_timeout_s, max(left, 1e-3)),
                    ),
                    timeout=max(left, 1e-3),
                )
            except asyncio.TimeoutError:
                outcome = "timeout_running"

            async with condition:
                scheduler.release(selection.worker)
                timeline.mark(loop.time(), dr=-1)
                selection.decision["actual_runtime_s"] = loop.time() - started
                selection.decision["request_outcome"] = outcome

                if outcome == "timeout_running":
                    if slot_manager:
                        slot_manager.mark_resident(state.task_index, selection.request.prompt_tokens)
                        scheduler.set_idle_cache(state.task_index, selection.request.prompt_tokens)
                        await cleanup_terminal_slot(state.task_index, "terminal_timeout")
                    finish(state, "timeout_running", "deadline during request")
                    condition.notify_all()
                    return

                selection.decision["request_outcome"] = "error" if response["error"] else "ok"
                response.update(
                    task_id=task.task_id,
                    task_index=state.task_index,
                    cls=task.cls,
                    step=state.next_step,
                    worker=selection.worker,
                    t_rel=response["t_send"] - t0,
                    ready_queue_s=started - selection.request.ready_at,
                    scheduler_reason=selection.reason,
                    scheduler_decision_sequence=selection.decision["decision_sequence"],
                    predicted_runtime_s=selection.request.predicted_runtime_s,
                    cache_was_evicted=cache_was_evicted,
                    cache_grace_return=cache_grace_return,
                    cache_grace_hit=cache_grace_hit,
                    resume_priority=selection.request.resume_priority,
                )
                requests.append(response)
                if response["error"]:
                    if slot_manager:
                        slot_manager.mark_resident(state.task_index, selection.request.prompt_tokens)
                        scheduler.set_idle_cache(state.task_index, selection.request.prompt_tokens)
                        await cleanup_terminal_slot(state.task_index, "terminal_error")
                    finish(state, "failed", response["error"])
                    condition.notify_all()
                    return

                state.history = state.history + response["tokens"]
                if slot_manager:
                    slot_manager.mark_resident(state.task_index, len(state.history))
                    scheduler.set_idle_cache(state.task_index, len(state.history))
                rec.steps_done = state.next_step + 1
                state.next_step += 1
                if state.next_step == len(task.steps):
                    await cleanup_terminal_slot(state.task_index, "terminal_complete")
                    finish(state, "ok")
                    condition.notify_all()
                    return

                delay = (step.tool_delay_s or 0.0) * cfg.run.delay_scale
                rec.tool_s += delay
                state.enqueued_at, state.runnable_at, state.runnable = 0.0, None, False
                predicted_delay = delay if cfg.timing_mode == "oracle" else cfg.estimated_tool_delay_s
                scheduler.mark_tool_waiting(state.task_index, loop.time() + predicted_delay)
                if slot_manager and cfg.cache_grace_s > 0:
                    grace_deadline_by_task[state.task_index] = loop.time() + cfg.cache_grace_s
                timeline.mark(loop.time(), dtool=+1)
                job = asyncio.create_task(wait_for_tool(state, delay))
                tool_jobs.append(job)
                job.add_done_callback(lambda _: asyncio.create_task(notify()))
                condition.notify_all()

        async def arrivals() -> None:
            nonlocal enqueue_sequence
            for index, offset in enumerate(offsets):
                await asyncio.sleep(max(0.0, t0 + offset - loop.time()))
                async with condition:
                    now = loop.time()
                    state, rec = states[index], recs[index]
                    rec.arrival = now
                    state.history = list(tasks[index].steps[0].input)
                    state.enqueued_at = state.runnable_at = now
                    state.runnable = True
                    enqueue_sequence += 1
                    scheduler.put(_ready_request(state, tasks[index], rec, now,
                                                 enqueue_sequence, estimator, cfg.run.deadline_s,
                                                 slot_manager.preferred_slot(index)
                                                 if slot_manager else None))
                    timeline.mark(now, dq=+1, dready=+1)
                    condition.notify_all()

        arrival_job = asyncio.create_task(arrivals())
        async with condition:
            while completed < len(states):
                failed_jobs = [job for job in [*request_jobs, *tool_jobs]
                               if job.done() and not job.cancelled() and job.exception()]
                if failed_jobs:
                    raise failed_jobs[0].exception()
                now = loop.time()
                for entry in scheduler.expired(now):
                    if entry.resume_priority:
                        pending_resume_priorities = max(0, pending_resume_priorities - 1)
                    state = states[entry.task_index]
                    timeline.mark(now, dq=-1, dready=-1)
                    finish(state, "timeout_queued", "deadline while queued")

                for entry in list(scheduler.entries):
                    if scheduler.infeasible(entry):
                        scheduler.remove_task(entry.task_index)
                        if entry.resume_priority:
                            pending_resume_priorities = max(0, pending_resume_priorities - 1)
                        timeline.mark(now, dq=-1, dready=-1)
                        finish(states[entry.task_index], "infeasible",
                               "request exceeds idle KV capacity")

                dispatched = False
                while scheduler.entries and scheduler.free_workers:
                    protected_slots: set[int] = set()
                    if slot_manager:
                        current = loop.time()
                        protected_slots = {
                            slot for slot in scheduler.free_workers
                            if (owner := slot_manager.owner(slot)) is not None
                            and owner in scheduler.waiting_tasks
                            and current < grace_deadline_by_task.get(owner, 0.0)
                        }
                        head = scheduler.entries[0]
                        preferred = slot_manager.preferred_slot(head.task_index)
                        unowned = [slot for slot in scheduler.free_workers
                                   if slot_manager.owner(slot) is None]
                        needs_slot = preferred not in scheduler.free_workers and not unowned
                        over_capacity = (
                            scheduler.projected_used_cells(head, loop.time())
                            > scheduler.config.capacity_cells
                        )
                        if needs_slot or over_capacity:
                            slot_id = slot_manager.eviction_slot(
                                scheduler.free_workers, protected_task=head.task_index,
                                protected_slots=protected_slots)
                            if slot_id is not None:
                                await evict_slot(slot_id, "slot_reassignment" if needs_slot
                                                 else "memory_pressure")
                                continue
                            if protected_slots:
                                decisions.append({
                                    "decision_sequence": None,
                                    "t": loop.time() - t0,
                                    "reason": "adaptive_grace_hold",
                                    "selected_task_index": None,
                                    "queue_length": len(scheduler.entries),
                                    "free_slots": len(scheduler.free_workers),
                                    "active_count": len(scheduler.active),
                                    "protected_slots": len(protected_slots),
                                })
                                break
                    decision_started = time.perf_counter()
                    eligible_workers = scheduler.free_workers - protected_slots
                    selection, decision = scheduler.select(loop.time(), eligible_workers)
                    if decision:
                        decision["scheduler_overhead_s"] = time.perf_counter() - decision_started
                        decision["t"] -= t0
                        decisions.append(decision)
                    if selection is None:
                        break
                    if selection.request.resume_priority:
                        pending_resume_priorities = max(0, pending_resume_priorities - 1)
                        resume_priority_streak += 1
                    else:
                        resume_priority_streak = 0
                    dispatched = True
                    state = states[selection.request.task_index]
                    rec = recs[state.task_index]
                    rec.worker = selection.worker
                    if rec.admit is None:
                        rec.admit = loop.time()
                    rec.ready_queue_s += loop.time() - selection.request.ready_at
                    state.runnable = False
                    timeline.mark(loop.time(), dq=-1, dready=-1, dr=+1)
                    job = asyncio.create_task(execute_request(selection))
                    request_jobs.append(job)
                    job.add_done_callback(lambda _: asyncio.create_task(notify()))

                if completed >= len(states):
                    break
                if dispatched:
                    continue

                wakeups = [entry.deadline_at for entry in scheduler.entries]
                if scheduler.entries:
                    wakeups.append(scheduler.entries[0].ready_at + cfg.aging_threshold_s)
                wakeups.extend(deadline for deadline in grace_deadline_by_task.values()
                               if deadline > loop.time())
                future = [wake for wake in wakeups if wake > loop.time() + 1e-6]
                timeout = max(1e-3, min(future) - loop.time()) if future else None
                try:
                    if timeout is None:
                        await condition.wait()
                    else:
                        await asyncio.wait_for(condition.wait(), timeout=timeout)
                except asyncio.TimeoutError:
                    pass

        await arrival_job
        if request_jobs:
            await asyncio.gather(*request_jobs)
        if tool_jobs:
            await asyncio.gather(*tool_jobs)
    return recs, requests, timeline, decisions, evictions, t0


def _task_frame(recs: list[TaskRecord], t0: float) -> pd.DataFrame:
    tasks = pd.DataFrame([asdict(record) for record in recs])
    tasks["queue_s"] = tasks.admit - tasks.arrival
    tasks["completion_s"] = tasks.finish - tasks.arrival
    tasks["service_s"] = tasks.finish - tasks.admit
    for column in ("arrival", "admit", "finish"):
        tasks[column] = tasks[column] - t0
    return tasks


def summarize_phase2(cfg: Phase2Config, tasks: pd.DataFrame, requests: pd.DataFrame,
                     memory: pd.DataFrame, metrics: dict, decisions: list[dict],
                     evictions: list[dict]) -> dict:
    summary = summarize(cfg.run, tasks, requests, memory, metrics, [])
    summary["scheduler"] = "phase2_two_queue"
    reasons = pd.Series([row["reason"] for row in decisions], dtype=str)
    selected = [row for row in decisions if row.get("selected_task_index") is not None]
    waits = tasks.ready_queue_s.dropna()
    summary.update({
        "scheduler_decisions": len(decisions),
        "fifo_head_admissions": int((reasons == "fifo_head").sum()),
        "backfill_admissions": int((reasons == "backfill").sum()),
        "blocked_head_decisions": int(reasons.isin(["head_blocked", "aged_head_blocked"]).sum()),
        "aged_head_decisions": int((reasons == "aged_head_blocked").sum()),
        "infeasible": int((tasks.status == "infeasible").sum()),
        "max_ready_queue_s": float(waits.max()) if len(waits) else float("nan"),
        "ready_queue_p99_s": float(np.percentile(waits, 99)) if len(waits) else float("nan"),
        "runtime_prediction_fallbacks": sum(bool(row.get("runtime_fallback")) for row in selected),
        "timing_mode": cfg.timing_mode,
        "eviction_policy": cfg.eviction_policy,
        "cache_grace_s": cfg.cache_grace_s,
        "max_consecutive_resume_priorities": cfg.max_consecutive_resume_priorities,
        "resume_priority_head_age_limit_s": cfg.resume_priority_head_age_limit_s,
        "adaptive_grace_holds": int((reasons == "adaptive_grace_hold").sum()),
        "cache_grace_returns": int(requests.get(
            "cache_grace_return", pd.Series(dtype=bool)).fillna(False).sum()),
        "cache_grace_hits": int(requests.get(
            "cache_grace_hit", pd.Series(dtype=bool)).fillna(False).sum()),
        "resume_priority_grants": int((reasons == "resume_priority_granted").sum()),
        "resume_priority_denied_quota": int((reasons == "resume_priority_denied_quota").sum()),
        "resume_priority_denied_age": int((reasons == "resume_priority_denied_age").sum()),
        "grace_expired_evictions": sum(bool(row.get("grace_expired")) for row in evictions),
        "cache_evictions": len(evictions),
        "verified_reclaimed_cells": int(sum(row.get("reclaimed_cells") or 0 for row in evictions)),
        "eviction_verification_errors": sum(bool(row.get("verification_error")) for row in evictions),
        "recomputed_requests": int(requests.get("cache_was_evicted", pd.Series(dtype=bool)).fillna(False).sum()),
        "recomputed_prompt_tokens": int(requests.loc[
            requests.get("cache_was_evicted", pd.Series(False, index=requests.index)).fillna(False),
            "prompt_n",
        ].fillna(0).sum()) if len(requests) and "prompt_n" in requests else 0,
    })
    return summary


def execute(cfg: Phase2Config, estimator: RuntimeEstimator, group: str = "phase2") -> dict:
    cfg.validate()
    out = RESULTS / group / cfg.run.name
    out.mkdir(parents=True, exist_ok=True)
    if cfg.eviction_policy == "slot_aware" and "--slot-save-path" not in cfg.run.server.extra:
        slot_path = out / "slots"
        slot_path.mkdir(parents=True, exist_ok=True)
        cfg.run.server.extra += ["--slot-save-path", str(slot_path)]
    cfg.run.server.parallel = cfg.run.concurrency
    rng = np.random.default_rng(cfg.run.seed)
    pool = load(ROOT / cfg.run.trace)
    indices = rng.choice(len(pool), size=cfg.run.n_tasks, replace=cfg.run.n_tasks > len(pool))
    tasks_to_run = [pool[index] for index in indices]
    gaps = (rng.exponential(1.0 / cfg.run.arrival_rate, size=len(tasks_to_run))
            if cfg.run.arrival_rate > 0 else np.zeros(len(tasks_to_run)))
    if len(gaps):
        gaps[0] = 0.0
    offsets = np.cumsum(gaps)

    with GpuMemSampler() as gpu, Server(cfg.run.server, out / "server.log", sample_interval=0.1) as srv:
        with KvMemSampler(srv.url) as kv:
            if cfg.run.require_kv_metrics and not kv.samples:
                raise RuntimeError(kv.error or "physical KV occupancy metrics unavailable")
            recs, request_rows, timeline, decisions, evictions, t0 = asyncio.run(
                run_phase2(cfg, srv, tasks_to_run, offsets, estimator))
        metrics = srv.metrics()
        memory = pd.DataFrame([
            {"t": sample.t - t0, "footprint_mib": sample.footprint / MiB,
             "rss_mib": sample.rss / MiB}
            for sample in srv.mem.samples
        ])

    tasks = _task_frame(recs, t0)
    requests = pd.DataFrame(request_rows).drop(columns=["tokens"], errors="ignore")
    summary = summarize_phase2(cfg, tasks, requests, memory, metrics, decisions, evictions)
    summary.update(kv.summary())
    gpu_rows = [{**sample, "t": sample["t"] - t0} for sample in gpu.samples]
    summary.update({
        "gpu_peak_used_mib": max((sample["used_mib"] for sample in gpu_rows), default=None),
        "gpu_total_mib": gpu_rows[0]["total_mib"] if gpu_rows else None,
        "gpu_sampling_error": gpu.error or None,
    })

    tasks.to_csv(out / "tasks.csv", index=False)
    requests.to_csv(out / "requests.csv", index=False)
    pd.DataFrame(timeline.rows).to_csv(out / "timeline.csv", index=False)
    pd.DataFrame(decisions).to_csv(out / "scheduler_decisions.csv", index=False)
    pd.DataFrame(evictions).to_csv(out / "cache_evictions.csv", index=False)
    memory.to_csv(out / "memory.csv", index=False)
    pd.DataFrame(gpu_rows).to_csv(out / "gpu_memory.csv", index=False)
    kv_rows = [{**sample, "t": sample["t"] - t0} for sample in kv.samples]
    pd.DataFrame(kv_rows).to_csv(out / "kv_memory.csv", index=False)
    metadata = {
        "env": environment(),
        "phase2": asdict(cfg),
        "estimator": asdict(estimator),
        "summary": summary,
        "server_args": " ".join(cfg.run.server.args()),
    }
    (out / "meta.json").write_text(json.dumps(metadata, indent=2, default=str) + "\n")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--runtime-model", type=Path, required=True)
    parser.add_argument("--trace", default="data/traces/agenttrace_50_seed2.jsonl")
    parser.add_argument("--n-tasks", type=int, default=50)
    parser.add_argument("--rate", type=float, default=0.5)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--ctx", type=int, default=32768)
    parser.add_argument("--aging-threshold", type=float, default=30)
    parser.add_argument("--backfill-guard", type=float, default=1)
    parser.add_argument("--safety-margin", type=int, default=512)
    parser.add_argument("--reserve-per-waiting-task", type=int, default=0)
    parser.add_argument("--max-tool-reserve", type=int, default=0)
    parser.add_argument("--reserve-horizon", type=float, default=5)
    parser.add_argument("--estimated-tool-delay", type=float, default=0.2)
    parser.add_argument("--timing-mode", choices=("estimated", "oracle"), default="estimated")
    parser.add_argument("--eviction-policy", choices=("none", "slot_aware"), default="none")
    parser.add_argument("--cache-grace", type=float, default=0.0)
    parser.add_argument("--max-consecutive-resume-priorities", type=int, default=0)
    parser.add_argument("--resume-priority-head-age-limit", type=float, default=0.0)
    parser.add_argument("--deadline", type=float, default=21600)
    parser.add_argument("--request-timeout", type=float, default=1800)
    parser.add_argument("--seed", type=int, default=2)
    parser.add_argument("--require-kv-metrics", action="store_true")
    parser.add_argument("--allow-battery", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--evaluation", action="store_true")
    parser.add_argument("--frozen-policy", type=Path)
    args = parser.parse_args()
    run = RunConfig(
        name=args.name, trace=args.trace, n_tasks=args.n_tasks, arrival_rate=args.rate,
        concurrency=args.concurrency, deadline_s=args.deadline,
        request_timeout_s=args.request_timeout, require_kv_metrics=args.require_kv_metrics,
        seed=args.seed, server=ServerConfig(ctx=args.ctx, cache_ram=0, mmap=False),
    )
    config = Phase2Config(
        run=run, aging_threshold_s=args.aging_threshold,
        backfill_guard_s=args.backfill_guard, kv_safety_margin_cells=args.safety_margin,
        reserve_per_waiting_task=args.reserve_per_waiting_task,
        max_tool_reserve_cells=args.max_tool_reserve, reserve_horizon_s=args.reserve_horizon,
        estimated_tool_delay_s=args.estimated_tool_delay, timing_mode=args.timing_mode,
        eviction_policy=args.eviction_policy,
        cache_grace_s=args.cache_grace,
        max_consecutive_resume_priorities=args.max_consecutive_resume_priorities,
        resume_priority_head_age_limit_s=args.resume_priority_head_age_limit,
    )
    estimator = RuntimeEstimator.load(args.runtime_model)
    config.validate()
    if args.evaluation:
        if args.frozen_policy is None:
            parser.error("--evaluation requires --frozen-policy")
        verify_evaluation_config(config, args.runtime_model,
                                 verify_frozen_policy(args.frozen_policy))
    if args.dry_run:
        tasks = load(ROOT / args.trace)
        print(json.dumps({
            "valid": True,
            "available_tasks": len(tasks),
            "requested_tasks": args.n_tasks,
            "phase2": asdict(config),
            "estimator": asdict(estimator),
        }, indent=2, default=str))
        return
    check_power(args.allow_battery)
    print(json.dumps(execute(config, estimator), indent=2))


if __name__ == "__main__":
    main()
