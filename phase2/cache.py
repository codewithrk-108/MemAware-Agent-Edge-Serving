"""Validated idle-slot erasure for the staged Phase 2 eviction policy."""

import json
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Callable

from harness.kv_memory import kv_memory_sample


@dataclass(frozen=True)
class EvictionResult:
    slot_id: int
    used_cells_before: int
    used_cells_after: int

    @property
    def reclaimed_cells(self) -> int:
        return self.used_cells_before - self.used_cells_after


class EvictionVerificationError(RuntimeError):
    """The erase endpoint succeeded but aggregate occupancy did not visibly fall."""


@dataclass
class SlotCacheManager:
    concurrency: int
    owner_by_slot: dict[int, int] = field(default_factory=dict)
    slot_by_task: dict[int, int] = field(default_factory=dict)
    resident_cells_by_task: dict[int, int] = field(default_factory=dict)

    def preferred_slot(self, task_index: int) -> int | None:
        return self.slot_by_task.get(task_index)

    def owner(self, slot_id: int) -> int | None:
        return self.owner_by_slot.get(slot_id)

    def assign(self, slot_id: int, task_index: int) -> None:
        if not 0 <= slot_id < self.concurrency:
            raise ValueError(f"invalid slot {slot_id}")
        previous = self.owner_by_slot.get(slot_id)
        if previous is not None and previous != task_index:
            raise ValueError(f"slot {slot_id} is still owned by task {previous}")
        old_slot = self.slot_by_task.get(task_index)
        if old_slot is not None and old_slot != slot_id:
            raise ValueError(f"task {task_index} already owns slot {old_slot}")
        self.owner_by_slot[slot_id] = task_index
        self.slot_by_task[task_index] = slot_id

    def mark_resident(self, task_index: int, cells: int) -> None:
        if task_index not in self.slot_by_task:
            raise ValueError(f"task {task_index} has no owned slot")
        self.resident_cells_by_task[task_index] = cells

    def forget_slot(self, slot_id: int) -> tuple[int | None, int]:
        task_index = self.owner_by_slot.pop(slot_id, None)
        if task_index is None:
            return None, 0
        self.slot_by_task.pop(task_index, None)
        return task_index, self.resident_cells_by_task.pop(task_index, 0)

    def eviction_slot(self, free_slots: set[int], protected_task: int | None = None,
                      protected_slots: set[int] | None = None) -> int | None:
        protected_slots = protected_slots or set()
        candidates = [
            slot for slot in free_slots
            if slot not in protected_slots and slot in self.owner_by_slot
            and self.owner_by_slot[slot] != protected_task
        ]
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda slot: self.resident_cells_by_task.get(self.owner_by_slot[slot], 0),
        )


def erase_idle_slot(url: str, slot_id: int, active_slots: set[int],
                    sample: Callable = kv_memory_sample,
                    opener: Callable = urllib.request.urlopen,
                    verification_attempts: int = 3) -> EvictionResult:
    if slot_id < 0:
        raise ValueError("slot ID must be non-negative")
    if slot_id in active_slots:
        raise ValueError(f"refusing to erase active slot {slot_id}")
    before = sample(url)
    request = urllib.request.Request(
        f"{url.rstrip('/')}/slots/{slot_id}?action=erase",
        data=json.dumps({}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with opener(request, timeout=10) as response:
        if response.status != 200:
            raise RuntimeError(f"slot erase returned HTTP {response.status}")
        response.read()
    after = sample(url)
    result = EvictionResult(slot_id, before["used_cells"], after["used_cells"])
    for _ in range(max(0, verification_attempts - 1)):
        if result.reclaimed_cells > 0:
            return result
        time.sleep(0.05)
        after = sample(url)
        result = EvictionResult(slot_id, before["used_cells"], after["used_cells"])
    if result.reclaimed_cells <= 0:
        raise EvictionVerificationError(
            f"slot {slot_id} erase succeeded but aggregate KV release was not observed")
    return result
