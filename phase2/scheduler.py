"""Pure scheduling state for memory-aware FIFO, bounded backfill, and aging."""

from dataclasses import asdict, dataclass
from typing import Optional


@dataclass(frozen=True)
class SchedulerConfig:
    capacity_cells: int
    concurrency: int
    aging_threshold_s: float
    backfill_guard_s: float = 0.0
    safety_margin_cells: int = 0
    reserve_per_waiting_task: int = 0
    max_tool_reserve_cells: int = 0
    reserve_horizon_s: float = 0.0

    def validate(self) -> None:
        if self.capacity_cells <= 0 or self.concurrency <= 0:
            raise ValueError("capacity and concurrency must be positive")
        if self.aging_threshold_s < 0 or self.backfill_guard_s < 0:
            raise ValueError("aging threshold and backfill guard must be non-negative")
        if self.safety_margin_cells < 0 or self.reserve_per_waiting_task < 0:
            raise ValueError("KV margins and reserves must be non-negative")
        if self.max_tool_reserve_cells < 0:
            raise ValueError("maximum tool reserve must be non-negative")
        if self.reserve_horizon_s < 0:
            raise ValueError("reserve horizon must be non-negative")
        if self.safety_margin_cells >= self.capacity_cells:
            raise ValueError("safety margin must be smaller than KV capacity")
        if self.safety_margin_cells + self.max_tool_reserve_cells >= self.capacity_cells:
            raise ValueError("maximum reserve plus safety margin must be smaller than KV capacity")


@dataclass
class ReadyRequest:
    task_index: int
    step_index: int
    ready_at: float
    enqueue_sequence: int
    prompt_tokens: int
    output_budget_tokens: int
    predicted_runtime_s: float
    deadline_at: float
    runtime_fallback: bool = False
    bypass_count: int = 0
    preferred_worker: Optional[int] = None

    @property
    def predicted_kv_cells(self) -> int:
        return self.prompt_tokens + self.output_budget_tokens


@dataclass(frozen=True)
class ActiveReservation:
    worker: int
    task_index: int
    step_index: int
    reserved_kv_cells: int
    admitted_at: float


@dataclass
class Selection:
    request: ReadyRequest
    worker: int
    reason: str
    decision: dict


class TwoQueueScheduler:
    """Authoritative main queue with a computed backfill candidate view."""

    def __init__(self, config: SchedulerConfig):
        config.validate()
        self.config = config
        self.entries: list[ReadyRequest] = []
        self.active: dict[int, ActiveReservation] = {}
        self.free_workers = set(range(config.concurrency))
        self.waiting_tasks: dict[int, Optional[float]] = {}
        self.idle_cache_cells_by_task: dict[int, int] = {}
        self.decision_sequence = 0

    @property
    def active_reserved_cells(self) -> int:
        return sum(reservation.reserved_kv_cells for reservation in self.active.values())

    def tool_return_reserve_cells(self, now: float) -> int:
        waiting = sum(
            predicted_return is None or self.config.reserve_horizon_s == 0
            or predicted_return <= now + self.config.reserve_horizon_s
            for predicted_return in self.waiting_tasks.values()
        )
        return min(
            self.config.max_tool_reserve_cells,
            self.config.reserve_per_waiting_task * waiting,
        )

    @property
    def idle_cache_cells(self) -> int:
        return sum(self.idle_cache_cells_by_task.values())

    def projected_used_cells(self, request: ReadyRequest, now: float) -> int:
        own_idle = self.idle_cache_cells_by_task.get(request.task_index, 0)
        return (self.active_reserved_cells + self.idle_cache_cells - own_idle
                + request.predicted_kv_cells + self.tool_return_reserve_cells(now)
                + self.config.safety_margin_cells)

    def effective_available_cells(self, request: ReadyRequest, now: float) -> int:
        own_idle = self.idle_cache_cells_by_task.get(request.task_index, 0)
        return max(0, self.config.capacity_cells - self.active_reserved_cells
                   - self.idle_cache_cells + own_idle - self.tool_return_reserve_cells(now)
                   - self.config.safety_margin_cells)

    def put(self, request: ReadyRequest, prioritize: bool = False) -> None:
        if any(entry.task_index == request.task_index for entry in self.entries):
            raise ValueError(f"task {request.task_index} is already ready")
        self.entries.append(request)
        self.entries.sort(key=lambda entry: (entry.ready_at, entry.enqueue_sequence))
        if prioritize:
            self.entries.remove(request)
            self.entries.insert(0, request)

    def mark_tool_waiting(self, task_index: int, predicted_return_at: Optional[float] = None) -> None:
        self.waiting_tasks[task_index] = predicted_return_at

    def clear_tool_waiting(self, task_index: int) -> None:
        self.waiting_tasks.pop(task_index, None)

    def set_idle_cache(self, task_index: int, cells: int) -> None:
        if cells < 0:
            raise ValueError("idle cache cells must be non-negative")
        self.idle_cache_cells_by_task[task_index] = cells

    def evict_idle_cache(self, task_index: int) -> int:
        return self.idle_cache_cells_by_task.pop(task_index, 0)

    def clear_preferred_worker(self, task_index: int) -> None:
        for entry in self.entries:
            if entry.task_index == task_index:
                entry.preferred_worker = None

    def release(self, worker: int) -> ActiveReservation:
        reservation = self.active.pop(worker)
        self.free_workers.add(worker)
        return reservation

    def remove_task(self, task_index: int) -> Optional[ReadyRequest]:
        for index, entry in enumerate(self.entries):
            if entry.task_index == task_index:
                return self.entries.pop(index)
        return None

    def expired(self, now: float) -> list[ReadyRequest]:
        expired = [entry for entry in self.entries if now >= entry.deadline_at]
        if expired:
            expired_ids = {id(entry) for entry in expired}
            self.entries = [entry for entry in self.entries if id(entry) not in expired_ids]
        return expired

    def infeasible(self, request: ReadyRequest) -> bool:
        return request.predicted_kv_cells + self.config.safety_margin_cells > self.config.capacity_cells

    def _fits(self, request: ReadyRequest, now: float) -> bool:
        return self.projected_used_cells(request, now) <= self.config.capacity_cells

    def _decision(self, now: float, head: ReadyRequest, selected: Optional[ReadyRequest],
                  worker: Optional[int], reason: str, candidate_count: int = 0,
                  allowance_s: Optional[float] = None) -> dict:
        self.decision_sequence += 1
        return {
            "decision_sequence": self.decision_sequence,
            "t": now,
            "reason": reason,
            "selected_task_index": selected.task_index if selected else None,
            "selected_step": selected.step_index if selected else None,
            "worker": worker,
            "queue_length": len(self.entries),
            "free_slots": len(self.free_workers),
            "active_count": len(self.active),
            "head_task_index": head.task_index,
            "head_step": head.step_index,
            "head_age_s": max(0.0, now - head.ready_at),
            "head_bypass_count": head.bypass_count,
            "head_need_cells": head.predicted_kv_cells,
            "selected_need_cells": selected.predicted_kv_cells if selected else None,
            "selected_predicted_runtime_s": selected.predicted_runtime_s if selected else None,
            "runtime_fallback": selected.runtime_fallback if selected else None,
            "active_reserved_cells": self.active_reserved_cells,
            "idle_cache_cells": self.idle_cache_cells,
            "tool_return_reserve_cells": self.tool_return_reserve_cells(now),
            "safety_margin_cells": self.config.safety_margin_cells,
            "effective_available_cells": self.effective_available_cells(head, now),
            "projected_used_cells": self.projected_used_cells(selected or head, now),
            "candidate_count": candidate_count,
            "backfill_allowance_s": allowance_s,
        }

    def select(self, now: float, eligible_workers: Optional[set[int]] = None
               ) -> tuple[Optional[Selection], Optional[dict]]:
        workers = self.free_workers if eligible_workers is None else self.free_workers & eligible_workers
        if not self.entries or not workers:
            return None, None
        head = self.entries[0]
        if self._fits(head, now):
            selected = self.entries.pop(0)
            worker = self._worker_for(selected, workers)
            decision = self._decision(now, head, selected, worker, "fifo_head")
            return self._reserve(selected, worker, now, "fifo_head", decision), decision

        head_age = max(0.0, now - head.ready_at)
        allowance = self.config.aging_threshold_s - head_age - self.config.backfill_guard_s
        candidates = []
        if allowance > 0:
            candidates = [entry for entry in self.entries[1:]
                          if self._fits(entry, now)
                          and entry.predicted_runtime_s <= allowance
                          and now + entry.predicted_runtime_s < entry.deadline_at]
        if candidates:
            selected = min(candidates, key=lambda entry: (entry.ready_at, entry.enqueue_sequence))
            self.entries.remove(selected)
            worker = self._worker_for(selected, workers)
            head.bypass_count += 1
            decision = self._decision(now, head, selected, worker, "backfill",
                                      candidate_count=len(candidates), allowance_s=allowance)
            return self._reserve(selected, worker, now, "backfill", decision), decision

        reason = "aged_head_blocked" if head_age >= self.config.aging_threshold_s else "head_blocked"
        decision = self._decision(now, head, None, None, reason,
                                  candidate_count=len(candidates), allowance_s=max(0.0, allowance))
        return None, decision

    def _worker_for(self, request: ReadyRequest, workers: set[int]) -> int:
        if request.preferred_worker in workers:
            return request.preferred_worker
        return min(workers)

    def _reserve(self, request: ReadyRequest, worker: int, now: float,
                 reason: str, decision: dict) -> Selection:
        self.free_workers.remove(worker)
        self.idle_cache_cells_by_task.pop(request.task_index, None)
        self.active[worker] = ActiveReservation(
            worker=worker,
            task_index=request.task_index,
            step_index=request.step_index,
            reserved_kv_cells=request.predicted_kv_cells,
            admitted_at=now,
        )
        return Selection(request=request, worker=worker, reason=reason, decision=decision)

    def as_config_dict(self) -> dict:
        return asdict(self.config)
