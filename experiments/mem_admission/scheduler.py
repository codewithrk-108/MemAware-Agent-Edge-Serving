"""Experiment 4: memory-aware admission with max-count backfill and no concurrency cap, fixed output cap per request.

Approved design (lab log, experiments/README.md, sections 12-14):
  - No concurrency limit: as many requests run as their KV reservations fit in the 32K pool (server -np 32).
  - A request generates at most `cap` output tokens per admission ("chunk"). Its reservation is
    prompt (current history) + cap. The true remaining output is used only to stop generation where the recorded
    step ended (a real server stops at end-of-sequence); it never affects admission, ordering or reservations.
    (--reserve min_true reproduces the first runs, which reserved history + min(cap, true remaining output).)
  - Continuation (overrun rule): if a step still has output left after a chunk, its cache stays in its slot (counted
    as reserved), and it waits for another reservation of `cap` extra cells.
  - Dispatch on every event (arrival, chunk finished, tool returned), with
        free = 32,768 - 512 - reserved,  reserved = running reservations + cells held by waiting continuations
      1. continuations first, smallest extra need first; while any continuation is waiting and does not fit,
         no new request is admitted (continuations have first claim on freed space);
      2. then new requests (first steps and steps returning from tools), smallest need first (max-count packing:
         ascending need maximizes the number admitted), ties by ready time;
      3. deadlock breaker: if nothing is running and no waiting continuation fits, the waiting continuation holding
         the fewest cells is discarded (its slot erased); it becomes a new request that recomputes its history.
  - No starvation guard (Exp 4). Experiment 5 (--starve-s T) adds one, see below.
  - At a tool call the slot is erased (discard); the next step is a new request at the back (by ready time) that
    re-prefills its whole history.
Same workload, arrivals (Poisson mean 5 s, seed 2), deadlines and server settings as Phase 1 and experiments 1-3.

Experiment 5 (approved 2026-11-19): starvation guard, `--starve-s T` (E5a T = 60 s, E5b T = 120 s, cap 2,048).
  - A new request whose current queue wait (now - ready time; ready = task arrival for step 1, tool return for later
    steps) is >= T is "starving". Tool delays and running time do not count; the clock restarts for every step.
  - Dispatch order: 1. continuations (unchanged, still first, still block everything below while one waits);
    2. starving new requests, oldest ready time first; if the oldest starving request does not fit, no other new
       request is admitted (head-of-line block, no backfill); a starving request whose need exceeds the whole pool
       never blocks; 3. only when no request is starving: other new requests, smallest need first; 4. deadlock
       breaker (unchanged).
  - Starvation is checked at dispatch events (arrival, chunk finished, tool returned), so a request is promoted at the
    first event after it crosses T.
  - Task time is unchanged: finish of the last step minus task arrival (end to end).

Experiment 10 (split pool, `--alpha A --fifo-conc C`; proposed 2026-11-25): E7b plus a FIFO partition.
  - The usable pool (32,768 - 512 cells) is split in the scheduler's accounting only (one llama.cpp pool, all
    running requests batched together): partition P = round(A x usable) cells, partition F = the rest.
  - Each admitted request belongs to the partition that admitted it; its reservation (and, while it waits as a
    continuation, its resident cells) count against that partition.
  - Oversized (user rule, 2026-11-26): a waiting request whose reservation (history + cap for a new request, resident
    + cap for a continuation) exceeds the smaller partition is failed, and with it its task (3 of 1,068 requests at
    A = 0.5 are that large when they arrive).
  - Dispatch: 1. continuations first, each in its own partition; a waiting continuation that does not fit blocks new
    admissions in its own partition only. 2. P: new requests in the E7b order (online model, shortest predicted
    output first), while they fit in P (backfill, as E7b). 3. F: remaining new requests in ascending ready time
    (task arrival for a first step, tool return for later steps; same as FIFO N4); at most C F requests in flight
    (running + waiting F continuations); strict: if the oldest does not fit, F admits nothing else.
    4. deadlock breaker. `--split-breaker global` (E10, E10b): only when nothing runs in the whole server, as Exp 4.
       That let P freeze for 164 min in E10b while F kept running. `--split-breaker partition` (E10c, 2026-11-26):
       per partition, if nothing runs in that partition and none of its waiting continuations fits, discard its
       continuation holding the fewest cells (slot erased, it becomes a new request that recomputes its history).
  - No starvation guard. The true output length is never used.

Experiment 11 (`--slot-reuse`; approved 2026-11-26): E7b, but finished steps keep their cache in the slot
("leftover") instead of erasing it, so later requests reuse the matching prompt start (the ~1,048-token shared prefix,
or a task's whole history when it returns to its own untouched slot), as FIFO N4 gets from llama.cpp's cache_prompt.
  - Admission and ordering unchanged (E7b): free = pool - margin - running reservations - waiting continuations.
    Leftovers do not count against admission; they are reclaimable.
  - The scheduler picks the slot (id_slot) itself, never a slot held by a running request or a waiting continuation:
    the idle slot whose leftover shares the longest common prefix with the new prompt (a returning step finds its own
    slot), ties to leftovers of finished tasks, then the least recently used; with no shared prefix, an empty slot first, then the least recently used.
  - Physical guard: before a start, if running reservations + continuations + leftovers (except the chosen slot's)
    exceed pool - margin, leftovers are erased least recently used first until it fits. So llama.cpp never runs out of
    KV and never purges an idle slot itself (its purge could hit a waiting continuation, which it sees as idle).
  - Deadlock breaker: the discarded continuation's cache becomes a leftover (not erased), so its re-admission can reuse
    it. Error: slot erased.
  - E11b (`--slot-order own_first`, approved 2026-11-26): E11 sent new requests to the few slots already holding the
    shared prefix (9 of 32 used) and overwrote live tasks' histories while empty slots sat idle. Slot order becomes:
    own leftover > finished task's leftover with a shared prefix > empty slot > LRU leftover of a live task.
  - E11c (`--guard-order finished_first`, approved 2026-11-26): E11b + the memory guard erases finished tasks'
    leftovers first (LRU among them), then LRU leftovers of live tasks (E11b: 52 of 63 returning steps lost their
    history to the plain LRU guard).

Experiment 12 (`--max-running 4`, user, 2026-11-27): E11b with at most 4 requests running at once (as FIFO N4).
  - Same as E11b otherwise: memory-aware admission (history + cap must fit), continuations first and new requests
    blocked while one waits, online GBM order, slot reuse with own-first slot order, LRU memory guard, deadlock breaker.
  - A continuation or new request is admitted only if it fits in memory and fewer than 4 requests are running.
  - Why: decode slows with concurrency on llama.cpp (TPOT 26.4 ms at 5.6 running vs 16.6 ms at 4 for FIFO N4);
    fewer reservations also leave room for leftovers (tool-call histories) to survive.
  - E12b (`--slots 4 --max-running 4`, user, 2026-11-27): E12 kept -np 32, so up to 28 idle slots held leftover caches
    (mean 21.4K cells occupied vs FIFO N4's 15.8K) and decode slowed. With -np 4 the server itself has 4 slots, as FIFO
    N4: at most 4 caches exist, and a waiting continuation holds one of the 4.

Experiment 13, the "beat FIFO N4" algorithm (`--slots 4 --max-running 4 --slot-reuse --slot-order own_first
--order online_gbm --deadline-k K`; user, 2026-11-27):
  1. Engine shaped like FIFO N4: llama.cpp -np 4, at most 4 running, so at most 4 caches exist.
  2. Memory-aware admission as E7b/E11b: history + cap must fit; continuations first; deadlock breaker.
  3. Order: earliest deadline = task arrival + K * min(predicted step tokens, cap). A returning step keeps its task's
     arrival time (started tasks first); a misprediction moves a step by at most K * cap. K = 0: task-level FIFO.
  4. Slots: own leftover > finished task's leftover with the shared prefix > empty > LRU live leftover (E11b).
"""

import argparse
import asyncio
import json
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd

from experiments.kv_policy.runner import make_workload
from harness.client import complete, new_session
from harness.kv_memory import KvMemSampler
from harness.memory import GpuMemSampler, MiB
from harness.results import RESULTS, environment
from harness.server import Server, ServerConfig
from phase1.runner import TaskRecord, Timeline, summarize


@dataclass
class Config:
    name: str
    cap: int = 2048
    reserve: str = "cap"               # "cap": reserve history + cap (approved design, no output-length knowledge)
                                       # "min_true": history + min(cap, true remaining) (first runs; oracle below cap)
    trace: str = "data/traces/agenttrace_50_seed2.jsonl"
    n_tasks: int = 50
    arrival_rate: float = 0.2
    slots: int = 32
    ctx: int = 32768
    margin: int = 512
    deadline_s: float = 21600
    request_timeout_s: float = 1800
    seed: int = 2
    starve_s: Optional[float] = None   # experiment 5: starvation threshold on per-request queue wait (None = off)
    order: str = "need"                # "need": new requests smallest history + cap first (Exp 4/5)
                                       # "lookup": shortest predicted output first, prediction = median output of
                                       # (task class, step 0 / 1 / 2+) from the lookup file (Exp 6), ties by need
    lookup: str = "length_lookup.json"  # in experiments/mem_admission/; fitted on problems not in the trace used
    alpha: Optional[float] = None      # experiment 10: share of the usable pool for partition P (None = no split)
    fifo_conc: int = 2                 # experiment 10: max F requests in flight (running + waiting F continuations)
    split_breaker: str = "global"      # experiment 10: deadlock breaker scope, "global" (E10/E10b) or "partition" (E10c)
    slot_reuse: bool = False           # experiment 11: keep finished caches as leftovers, scheduler-chosen slot reuse
    slot_order: str = "lcp"            # experiment 11 slot choice: "lcp" (E11, longest shared prefix first) or
                                       # "own_first" (E11b: own leftover > finished task's > empty > LRU live leftover)
    deadline_k: Optional[float] = None  # experiment 13 ("beat FIFO N4"): order new requests by the deadline
                                       # task arrival + k * min(predicted step tokens, cap); None = E7b order
    max_running: Optional[int] = None  # experiment 12: at most this many requests running at once (None = no cap);
                                       # continuations count, admission is otherwise unchanged (memory-aware)
    guard_order: str = "lru"           # experiment 11 memory guard: "lru" (E11/E11b) or "finished_first" (E11c:
                                       # erase finished tasks' leftovers first, LRU among them, then LRU live ones)


@dataclass(eq=False)
class Job:
    index: int
    step: int = 0
    history: list = field(default_factory=list)
    remaining: int = 0
    slot: Optional[int] = None
    resident: int = 0                  # cells held while waiting as a continuation
    reserved: int = 0                  # cells reserved while running
    ready_at: float = 0.0
    chunks: int = 0
    terminal: bool = False
    promoted: bool = False             # current step has been marked starving (experiment 5)
    feat: Optional[dict] = None        # experiment 7: features of the current step when it became ready
    pred: Optional[float] = None       # experiment 7: predicted output length of the current step
    pred_n: int = 0                    # experiment 7: completed requests the model had learned from
    gen_step: int = 0                  # tokens generated so far for the current step
    gen_lens: list = field(default_factory=list)   # generated length of each completed step of this task
    part: Optional[str] = None         # experiment 10: "P", "F" or "B" (oversized, uses both) while admitted
    path: Optional[str] = None         # experiment 10: which admission path took it ("P" or "F")
    cP: int = 0                        # experiment 10: cells charged to partition P
    cF: int = 0                        # experiment 10: cells charged to partition F


class Run:
    def __init__(self, cfg: Config, url: str, tasks, offsets):
        self.cfg, self.url, self.tasks, self.offsets = cfg, url, tasks, offsets
        self.jobs = [Job(i) for i in range(len(tasks))]
        self.recs = [TaskRecord(t.task_id, t.cls, t.n_steps, t.total_tokens) for t in tasks]
        self.new_q, self.cont_q = [], []
        self.running = set()
        self.free_slots = list(range(cfg.slots))
        self.lock = asyncio.Lock()
        self.loop = asyncio.get_running_loop()
        self.t0 = self.loop.time()
        self.tl = Timeline(self.t0)
        self.requests, self.dispatches, self.events = [], [], []
        self.res_rows = []
        self.completed = 0
        self.all_done = asyncio.Event()
        self.bg = []
        self.starve_blocked = False
        self.pending_erase = []
        self.leftover = {}                 # experiment 11: idle slot -> {"tokens": [...], "t": last use, "task": index}
        self.slot_choice = {}              # experiment 11: job -> (kind, predicted reused tokens) for its next start
        self.leftover_erases = 0
        self.lookup = json.loads(Path(__file__).with_name(cfg.lookup).read_text()) if cfg.order == "lookup" else {}
        self.online = None
        if cfg.order in ("online_median", "online_gbm"):
            from experiments.mem_admission.online_estimator import OnlineEstimator
            self.online = OnlineEstimator(cfg.order)
        self.model_log = []

    # ---------- experiment 7: online length estimation ----------
    def became_ready(self, j: Job):
        """A new step is ready (arrival or tool return): record its features and score it with the current model."""
        if self.online is None:
            return
        task = self.tasks[j.index]
        j.feat = self.online.features(task.task_id, task.cls, j.step, j.gen_lens, len(j.history))
        self.score(j)

    def score(self, j: Job):
        if self.online is not None and self.online.ready and j.feat is not None:
            j.pred, j.pred_n = self.online.predict(j.feat), self.online.fitted_n

    def step_completed(self, j: Job):
        j.gen_lens.append(j.gen_step)
        if self.online is not None and j.feat is not None:
            if self.online.observe(j.feat, j.gen_step):
                for w in self.new_q:                     # rescore everyone waiting with the new model
                    self.score(w)
                self.model_log.append({"t": self.now(), "trained_on": self.online.fitted_n, "refit": self.online.refits,
                                       "overall_median": self.online.overall})
        j.gen_step = 0

    def now(self):
        return self.loop.time() - self.t0

    def reserved(self) -> int:
        return sum(j.reserved for j in self.running) + sum(j.resident for j in self.cont_q)

    def below_cap(self) -> bool:
        """Experiment 12: room for one more running request under --max-running (always true without a cap)."""
        return self.cfg.max_running is None or len(self.running) < self.cfg.max_running

    def free(self) -> int:
        return self.cfg.ctx - self.cfg.margin - self.reserved()

    # ---------- experiment 11: slot reuse ----------
    @staticmethod
    def lcp(a, b) -> int:
        n = min(len(a), len(b))
        i = 0
        step = 256
        while i + step <= n and a[i:i + step] == b[i:i + step]:
            i += step
        while i < n and a[i] == b[i]:
            i += 1
        return i

    def leftover_cells(self, skip=None) -> int:
        return sum(len(v["tokens"]) for k, v in self.leftover.items() if k != skip)

    def choose_slot(self, j: Job):
        """Pick an idle slot for a new request: longest shared prefix with a leftover, else empty, else LRU."""
        if self.cfg.slot_order == "own_first":
            return self.choose_slot_own_first(j)
        best, best_key, best_l = None, None, 0
        for s in self.free_slots:
            v = self.leftover.get(s)
            if v is None:
                continue
            l = self.lcp(v["tokens"], j.history)
            if l == 0:
                continue
            # longest prefix; ties: leftovers of finished tasks first (keep live tasks' histories), then oldest
            key = (l, self.jobs[v["task"]].terminal, -v["t"])
            if best_key is None or key > best_key:
                best, best_key, best_l = s, key, l
        if best is not None:
            kind = "own" if self.leftover[best]["task"] == j.index else "other"
            return best, kind, best_l
        empty = [s for s in self.free_slots if s not in self.leftover]
        if empty:
            return empty[-1], "empty", 0
        s = min(self.free_slots, key=lambda x: (not self.jobs[self.leftover[x]["task"]].terminal, self.leftover[x]["t"]))
        return s, "other", 0

    def choose_slot_own_first(self, j: Job):
        """E11b: own leftover > finished task's leftover with a shared prefix > empty slot > finished task's leftover
        without one > least recently used leftover of a live task (so live tasks keep their histories when possible)."""
        free = self.free_slots
        own = [s for s in free if s in self.leftover and self.leftover[s]["task"] == j.index]
        if own:
            s = own[0]
            return s, "own", self.lcp(self.leftover[s]["tokens"], j.history)
        fin = [(self.lcp(self.leftover[s]["tokens"], j.history), self.leftover[s]["t"], s)
               for s in free if s in self.leftover and self.jobs[self.leftover[s]["task"]].terminal]
        best = max(fin, default=None)                 # longest shared prefix, ties most recent
        if best is not None and best[0] > 0:
            return best[2], "finished", best[0]
        empty = [s for s in free if s not in self.leftover]
        if empty:
            return empty[-1], "empty", 0
        if best is not None:
            return best[2], "finished", 0
        s = min(free, key=lambda x: self.leftover[x]["t"])
        return s, "other", self.lcp(self.leftover[s]["tokens"], j.history)

    def take_slot(self, j: Job) -> int:
        if not self.cfg.slot_reuse:
            return self.free_slots.pop()
        s, kind, l = self.choose_slot(j)
        self.free_slots.remove(s)
        self.slot_choice[j] = (kind, l)
        return s

    async def make_room(self, session, j: Job):
        """Physical guard: erase LRU leftovers (never j's slot, never a continuation) until everything fits."""
        lim = self.cfg.ctx - self.cfg.margin
        while self.reserved() + self.leftover_cells(skip=j.slot) > lim:
            cand = [k for k in self.leftover if k != j.slot]
            if not cand:
                break
            if self.cfg.guard_order == "finished_first":
                s = min(cand, key=lambda k: (not self.jobs[self.leftover[k]["task"]].terminal, self.leftover[k]["t"]))
            else:
                s = min(cand, key=lambda k: self.leftover[k]["t"])
            fin = self.jobs[self.leftover[s]["task"]].terminal
            cells = len(self.leftover.pop(s)["tokens"])
            await self.erase(session, s)
            self.leftover_erases += 1
            self.events.append({"t": self.now(), "event": "leftover_erase" if not fin else "leftover_erase_finished",
                                "task_id": None, "step": None, "cells": cells})

    def to_leftover(self, j: Job, tokens):
        self.leftover[j.slot] = {"tokens": list(tokens), "t": self.loop.time(), "task": j.index}
        self.free_slots.append(j.slot)
        j.slot = None

    # ---------- experiment 10: split pool ----------
    def pool(self, part: str) -> int:
        usable = self.cfg.ctx - self.cfg.margin
        p = round(self.cfg.alpha * usable)
        return p if part == "P" else usable - p

    def pfree(self, part: str) -> int:
        holders = list(self.running) + self.cont_q
        used = sum((j.cP if part == "P" else j.cF) for j in holders)
        return self.pool(part) - used

    def f_in_flight(self) -> int:
        return sum(j.path == "F" for j in self.running) + sum(j.path == "F" for j in self.cont_q)

    def fits(self, j: Job, amount: int) -> bool:
        """Can `amount` more cells be charged to j's partition (P/F), or to both partitions for an oversized job (B)?"""
        if j.part == "B":
            return amount <= self.pfree("P") + self.pfree("F")
        return amount <= self.pfree(j.part)

    def charge(self, j: Job, amount: int):
        if j.part == "P":
            j.cP += amount
        elif j.part == "F":
            j.cF += amount
        else:                                            # B: own path's partition first, rest from the other
            own, other = (("P", "F") if j.path == "P" else ("F", "P"))
            a = min(amount, max(self.pfree(own), 0))
            setattr(j, "c" + own, getattr(j, "c" + own) + a)
            setattr(j, "c" + other, getattr(j, "c" + other) + amount - a)

    def release(self, j: Job):
        j.part = j.path = None
        j.cP = j.cF = 0

    def shrink_to_resident(self, j: Job):
        """After a chunk: a continuation keeps only its resident cells, charged to the same partition(s)."""
        if j.part == "P":
            j.cP = j.resident
        elif j.part == "F":
            j.cF = j.resident
        else:
            own = "P" if j.path == "P" else "F"
            o = min(getattr(j, "c" + own), j.resident)
            if own == "P":
                j.cP, j.cF = o, j.resident - o
            else:
                j.cF, j.cP = o, j.resident - o

    def admit_new(self, session, j: Job, part: str, path: str, overtook: int = 0):
        fb = self.free()
        self.new_q.remove(j)
        j.slot = self.free_slots.pop()
        j.reserved = self.new_need(j)
        j.part, j.path = part, path
        self.charge(j, j.reserved)
        self.start(session, j, cont=False, free_before=fb, overtook=overtook)

    def dispatch_split(self, session) -> tuple:
        """One pass of the experiment 10 dispatch. Returns (progress, admitted_cont, admitted_new, blocked)."""
        progress, ac, an = False, 0, 0
        limit = min(self.pool("P"), self.pool("F"))
        for j in [x for x in self.new_q if self.new_need(x) > limit] + \
                 [x for x in self.cont_q if x.resident + self.reserve_len(x) > limit]:
            cells = self.new_need(j) if j in self.new_q else j.resident + self.reserve_len(j)
            if j in self.new_q:
                self.new_q.remove(j)
            else:
                self.cont_q.remove(j)
                await_erase = j.slot
                self.free_slots.append(j.slot)
                j.slot = None
                self.pending_erase.append(await_erase)
            self.release(j)
            self.tl.mark(self.loop.time(), dq=-1, dready=-1)
            self.events.append({"t": self.now(), "event": "oversized_fail", "task_id": self.tasks[j.index].task_id,
                                "step": j.step, "cells": cells})
            self.finish(j, "failed", f"exceeds partition ({cells} > {limit} cells)")
            progress = True
        for j in sorted(self.cont_q, key=lambda x: (self.reserve_len(x), x.ready_at)):
            extra = self.reserve_len(j)
            if self.fits(j, extra):
                fb = self.free()
                self.cont_q.remove(j)
                self.charge(j, extra)
                j.reserved = j.resident + extra
                j.resident = 0
                self.start(session, j, cont=True, free_before=fb)
                ac += 1
                progress = True
        blockP = any(j.path == "P" for j in self.cont_q)
        blockF = any(j.path == "F" for j in self.cont_q)
        if not blockP:                                   # P: E7b order, backfill
            older = sorted(self.new_q, key=lambda x: x.ready_at)
            for j in sorted(self.new_q, key=self.new_key):
                if not self.free_slots:
                    break
                need = self.new_need(j)
                if need <= self.pool("P"):
                    if need <= self.pfree("P"):
                        ov = sum(1 for o in older if o.ready_at < j.ready_at and o in self.new_q)
                        self.admit_new(session, j, "P", "P", ov)
                        an += 1
                        progress = True
                elif need <= self.pfree("P") + self.pfree("F"):
                    self.admit_new(session, j, "B", "P")
                    an += 1
                    progress = True
        if not blockF:                                   # F: oldest task arrival first, strict, at most fifo_conc
            for j in sorted(self.new_q, key=lambda x: x.ready_at):
                if self.f_in_flight() >= self.cfg.fifo_conc or not self.free_slots:
                    break
                need = self.new_need(j)
                if need <= self.pool("F") and need <= self.pfree("F"):
                    self.admit_new(session, j, "F", "F")
                elif need > self.pool("F") and need <= self.pfree("F") + self.pfree("P"):
                    self.admit_new(session, j, "B", "F")
                else:
                    break                                # strict FIFO: the oldest does not fit, F waits
                an += 1
                progress = True
        return progress, ac, an, (blockP, blockF)

    def chunk_len(self, j: Job) -> int:
        return min(self.cfg.cap, j.remaining)

    def reserve_len(self, j: Job) -> int:
        """Output cells reserved for the next chunk. With reserve="cap" the true output length is never used here."""
        return self.cfg.cap if self.cfg.reserve == "cap" else self.chunk_len(j)

    def new_need(self, j: Job) -> int:
        return len(j.history) + self.reserve_len(j)

    def predicted_len(self, j: Job) -> float:
        """Experiment 6: median output length of (task class, step bucket) on calibration tasks (other problems than
        the 50 test tasks). Uses only what a server knows: the task's class and the step index."""
        return self.lookup[f"{self.tasks[j.index].cls}|{min(j.step, 2)}"]

    def deadline(self, j: Job) -> float:
        """Experiment 13: task arrival time + k * predicted output tokens of this step, capped at one chunk. Before the
        first model exists (or with k = 0) this is the task's arrival time: task-level FIFO. A step returning from a
        tool call keeps its task's arrival time, so started tasks go before newer ones. The prediction comes from the
        online model (features known when the step became ready); the true length is never used."""
        p = j.pred if (self.online is not None and self.online.ready and j.pred is not None) else 0.0
        return self.recs[j.index].arrival + self.cfg.deadline_k * min(p, self.cfg.cap)

    def new_key(self, j: Job):
        if self.cfg.deadline_k is not None:
            return (self.deadline(j), j.ready_at)
        if self.online is not None:                      # experiment 7: need order until the first model exists
            if self.online.ready and j.pred is not None:
                return (0, j.pred, self.new_need(j), j.ready_at)
            return (1 if self.online.ready else 0, self.new_need(j), self.new_need(j), j.ready_at)
        if self.cfg.order == "lookup":
            return (self.predicted_len(j), self.new_need(j), j.ready_at)
        return (self.new_need(j), j.ready_at)

    def mark(self):
        self.res_rows.append({"t": self.now(), "running": len(self.running), "waiting_new": len(self.new_q),
                              "waiting_cont": len(self.cont_q), "reserved": self.reserved(),
                              "starving": sum(j.promoted for j in self.new_q),
                              "blocked_by_starving": self.starve_blocked,
                              **({"reserved_P": self.pool("P") - self.pfree("P"),
                                  "reserved_F": self.pool("F") - self.pfree("F"),
                                  "running_P": sum(j.path == "P" for j in self.running),
                                  "running_F": sum(j.path == "F" for j in self.running),
                                  "waiting_cont_P": sum(j.path == "P" for j in self.cont_q),
                                  "waiting_cont_F": sum(j.path == "F" for j in self.cont_q)}
                                 if self.cfg.alpha is not None else {})})

    async def erase(self, session, slot):
        async with session.post(f"{self.url}/slots/{slot}?action=erase", json={}) as r:
            await r.read()

    def finish(self, j: Job, status: str, error=None):
        if j.terminal:
            return
        j.terminal = True
        rec = self.recs[j.index]
        rec.status, rec.error, rec.finish = status, error, self.loop.time()
        self.completed += 1
        if self.completed == len(self.jobs):
            self.all_done.set()

    # ---------- dispatch ----------
    async def dispatch(self, session):
        async with self.lock:
            admitted_cont = admitted_new = 0
            free_before = self.free()
            blocked = False
            while self.cfg.alpha is not None:
                progress, ac, an, blocked = self.dispatch_split(session)
                while self.pending_erase:
                    await self.erase(session, self.pending_erase.pop())
                admitted_cont, admitted_new = admitted_cont + ac, admitted_new + an
                if not progress and self.cont_q and self.cfg.split_breaker == "partition":
                    stuck = [q for q in ("P", "F")
                             if not any(r.path == q for r in self.running) and any(c.path == q for c in self.cont_q)]
                    if stuck:
                        q = stuck[0]
                        v = min((c for c in self.cont_q if c.path == q), key=lambda x: x.resident)
                        self.cont_q.remove(v)
                        await self.erase(session, v.slot)
                        self.free_slots.append(v.slot)
                        self.events.append({"t": self.now(), "event": "deadlock_discard", "partition": q,
                                            "task_id": self.tasks[v.index].task_id, "step": v.step,
                                            "cells": v.resident})
                        v.slot, v.resident = None, 0
                        self.release(v)
                        self.new_q.append(v)
                        continue
                if not self.running and self.cont_q and not progress:
                    v = min(self.cont_q, key=lambda x: x.resident)
                    self.cont_q.remove(v)
                    await self.erase(session, v.slot)
                    self.free_slots.append(v.slot)
                    self.events.append({"t": self.now(), "event": "deadlock_discard",
                                        "task_id": self.tasks[v.index].task_id, "step": v.step, "cells": v.resident})
                    v.slot, v.resident = None, 0
                    self.release(v)
                    self.new_q.append(v)
                    continue
                if not progress:
                    break
            while self.cfg.alpha is None:
                progress = False
                # 1. continuations, smallest extra first
                for j in sorted(self.cont_q, key=lambda x: (self.reserve_len(x), x.ready_at)):
                    extra = self.reserve_len(j)
                    if extra <= self.free() and self.below_cap():
                        fb = self.free()
                        self.cont_q.remove(j)
                        j.reserved = j.resident + extra
                        j.resident = 0
                        self.start(session, j, cont=True, free_before=fb)
                        admitted_cont += 1
                        progress = True
                blocked = bool(self.cont_q)
                # 2. (experiment 5) starving new requests, oldest first; the oldest one that does not fit blocks
                #    every other new request
                starve_block = False
                if not blocked and self.cfg.starve_s is not None:
                    t = self.loop.time()
                    for j in self.new_q:
                        if not j.promoted and t - j.ready_at >= self.cfg.starve_s:
                            j.promoted = True
                            self.events.append({"t": self.now(), "event": "starving",
                                                "task_id": self.tasks[j.index].task_id, "step": j.step,
                                                "cells": self.new_need(j)})
                    for j in sorted([x for x in self.new_q if x.promoted], key=lambda x: x.ready_at):
                        need = self.new_need(j)
                        if need > self.cfg.ctx - self.cfg.margin:
                            continue                                  # can never fit: does not block
                        if need <= self.free() and self.free_slots and self.below_cap():
                            fb = self.free()
                            self.new_q.remove(j)
                            j.slot = self.take_slot(j)
                            j.reserved = need
                            self.start(session, j, cont=False, free_before=fb, starving=True)
                            admitted_new += 1
                            progress = True
                        else:
                            starve_block = True
                            break
                self.starve_blocked = starve_block
                # 3. new requests, smallest need first, only if no continuation is waiting and nobody starves
                if not blocked and not starve_block:
                    older = sorted(self.new_q, key=lambda x: x.ready_at)
                    for j in sorted(self.new_q, key=self.new_key):
                        need = self.new_need(j)
                        if need <= self.free() and self.free_slots and self.below_cap():
                            overtook = sum(1 for o in older if o.ready_at < j.ready_at and o in self.new_q)
                            fb = self.free()
                            self.new_q.remove(j)
                            j.slot = self.take_slot(j)
                            j.reserved = need
                            self.start(session, j, cont=False, free_before=fb, overtook=overtook)
                            admitted_new += 1
                            progress = True
                # 3. deadlock breaker
                if not self.running and self.cont_q and not progress:
                    v = min(self.cont_q, key=lambda x: x.resident)
                    self.cont_q.remove(v)
                    self.events.append({"t": self.now(), "event": "deadlock_discard",
                                        "task_id": self.tasks[v.index].task_id, "step": v.step, "cells": v.resident})
                    if self.cfg.slot_reuse:
                        self.to_leftover(v, v.history)
                    else:
                        await self.erase(session, v.slot)
                        self.free_slots.append(v.slot)
                        v.slot = None
                    v.resident = 0
                    self.new_q.append(v)
                    continue
                if not progress:
                    break
            if admitted_cont or admitted_new or self.new_q or self.cont_q:
                self.dispatches.append({"t": self.now(), "free_before": free_before, "free_after": self.free(),
                                        "admitted_cont": admitted_cont, "admitted_new": admitted_new,
                                        "waiting_new": len(self.new_q), "waiting_cont": len(self.cont_q),
                                        "blocked_by_cont": blocked, "blocked_by_starving": self.starve_blocked,
                                        "running": len(self.running)})
            self.mark()

    def start(self, session, j: Job, cont: bool, free_before: int, overtook: int = 0, starving: bool = False):
        rec = self.recs[j.index]
        now = self.loop.time()
        if rec.admit is None:
            rec.admit = now
        rec.ready_queue_s += now - j.ready_at
        self.running.add(j)
        self.tl.mark(now, dq=-1, dready=-1, dr=+1)
        meta = {"continuation": cont, "reserved": j.reserved, "free_before": free_before,
                "running_at_admit": len(self.running), "wait_s": now - j.ready_at, "overtook": overtook,
                "starving": starving, "pred": j.pred if not cont else None, "pred_n": j.pred_n if not cont else None,
                "part": j.part, "path": j.path}
        if self.cfg.slot_reuse:
            kind, l = ("cont", j.resident) if cont else self.slot_choice.pop(j, ("empty", 0))
            if not cont:
                self.leftover.pop(j.slot, None)   # the chosen slot's leftover now belongs to this request
            meta.update(slot_kind=kind, lcp_pred=l, slot_used=j.slot)
        self.bg.append(asyncio.create_task(self.run_chunk(session, j, meta)))

    # ---------- execution ----------
    async def run_chunk(self, session, j: Job, meta: dict):
        cfg, task, rec = self.cfg, self.tasks[j.index], self.recs[j.index]
        n = self.chunk_len(j)
        left = cfg.deadline_s - (self.loop.time() - rec.arrival)
        if cfg.slot_reuse:
            async with self.lock:
                await self.make_room(session, j)
        try:
            r = await asyncio.wait_for(complete(session, self.url, j.history, n, cache_prompt=True, return_tokens=True,
                                                id_slot=j.slot, timeout=cfg.request_timeout_s), timeout=max(left, 0.001))
        except asyncio.TimeoutError:
            r = {"error": "deadline during request", "tokens": []}
        tokens = r.pop("tokens", [])
        r.update(task_id=task.task_id, task_index=j.index, cls=task.cls, step=j.step, chunk=j.chunks, cap=cfg.cap,
                 n_chunk=n, t_rel=r.get("t_send", self.loop.time()) - self.t0, **meta)
        j.chunks += 1
        self.requests.append(r)
        async with self.lock:
            self.running.discard(j)
            self.tl.mark(self.loop.time(), dr=-1)
            j.reserved = 0
            if r["error"]:
                await self.erase(session, j.slot)
                self.free_slots.append(j.slot)
                j.slot = None
                self.release(j)
                self.finish(j, "failed" if "deadline" not in r["error"] else "timeout_running", r["error"])
            else:
                j.history = j.history + tokens
                j.remaining -= r.get("predicted_n") or n
                j.gen_step += r.get("predicted_n") or n
                if j.remaining > 0:                                   # continuation: keep the cache, wait
                    j.resident, j.ready_at = len(j.history), self.loop.time()
                    if self.cfg.alpha is not None:
                        self.shrink_to_resident(j)
                    self.cont_q.append(j)
                    self.tl.mark(j.ready_at, dq=+1, dready=+1)
                else:                                                 # step done: discard the cache
                    if cfg.slot_reuse:                                # experiment 11: keep it as a leftover
                        self.to_leftover(j, j.history)
                    else:
                        await self.erase(session, j.slot)
                        self.free_slots.append(j.slot)
                        j.slot = None
                    self.release(j)
                    rec.steps_done = j.step + 1
                    self.step_completed(j)
                    if j.step + 1 == task.n_steps:
                        self.finish(j, "ok")
                    else:
                        delay = task.steps[j.step].tool_delay_s or 0.0
                        rec.tool_s += delay
                        self.tl.mark(self.loop.time(), dtool=+1)
                        self.bg.append(asyncio.create_task(self.tool_wait(session, j, delay)))
        await self.dispatch(session)

    async def tool_wait(self, session, j: Job, delay: float):
        rec = self.recs[j.index]
        if delay >= self.cfg.deadline_s - (self.loop.time() - rec.arrival):
            self.tl.mark(self.loop.time(), dtool=-1)
            self.finish(j, "timeout_queued", "deadline while waiting for tool")
            return
        await asyncio.sleep(delay)
        task = self.tasks[j.index]
        j.step += 1
        j.history = j.history + task.steps[j.step].input
        j.remaining = task.steps[j.step].n_gen
        j.ready_at = self.loop.time()
        j.promoted = False
        self.tl.mark(j.ready_at, dtool=-1, dq=+1, dready=+1)
        async with self.lock:
            self.became_ready(j)
            self.new_q.append(j)
        await self.dispatch(session)

    async def arrivals(self, session):
        for i, off in enumerate(self.offsets):
            await asyncio.sleep(max(0.0, self.t0 + off - self.loop.time()))
            j, task = self.jobs[i], self.tasks[i]
            self.recs[i].arrival = j.ready_at = self.loop.time()
            j.history, j.remaining = list(task.steps[0].input), task.steps[0].n_gen
            self.tl.mark(j.ready_at, dq=+1, dready=+1)
            async with self.lock:
                self.became_ready(j)
                self.new_q.append(j)
            await self.dispatch(session)

    async def run(self):
        async with new_session(limit=64) as session:
            for s in range(self.cfg.slots):
                await self.erase(session, s)
            await self.arrivals(session)
            await self.all_done.wait()
            await asyncio.gather(*self.bg, return_exceptions=True)


def execute(cfg: Config, group: str = "mem_admission") -> dict:
    out = RESULTS / group / cfg.name
    out.mkdir(parents=True, exist_ok=True)
    tasks, offsets = make_workload(cfg)
    with tempfile.TemporaryDirectory(prefix="memadm-") as slot_dir:
        server = ServerConfig(ctx=cfg.ctx, parallel=cfg.slots, kv_unified=True, cache_ram=0, mmap=False,
                              extra=["--slot-save-path", slot_dir])
        with GpuMemSampler() as gpu, Server(server, out / "server.log", sample_interval=0.1) as srv:
            with KvMemSampler(srv.url) as kv:
                if not kv.samples:
                    raise RuntimeError(kv.error or "physical KV occupancy metrics unavailable")

                async def go():
                    r = Run(cfg, srv.url, tasks, offsets)
                    await r.run()
                    return r
                run = asyncio.run(go())
            metrics = srv.metrics()
            t0 = run.t0
            mem = pd.DataFrame([{"t": s.t - t0, "footprint_mib": s.footprint / MiB, "rss_mib": s.rss / MiB}
                                for s in srv.mem.samples])

    tdf = pd.DataFrame([asdict(r) for r in run.recs])
    tdf["queue_s"] = tdf.admit - tdf.arrival
    tdf["completion_s"] = tdf.finish - tdf.arrival
    tdf["service_s"] = tdf.finish - tdf.admit
    for c in ("arrival", "admit", "finish"):
        tdf[c] = tdf[c] - t0
    tdf["chunks"] = [j.chunks for j in run.jobs]
    reqs = pd.DataFrame(run.requests)
    res = pd.DataFrame(run.res_rows)
    summary = summarize(argparse.Namespace(name=cfg.name, trace=cfg.trace, scheduler=f"mem_admission_cap{cfg.cap}",
                                           concurrency=0, server=server, arrival_rate=cfg.arrival_rate,
                                           delay_scale=1.0, cache_prompt=True), tdf, reqs, mem, metrics, [])
    summary.update(kv.summary())
    rok = reqs[reqs.error.isna()]
    summary.update({
        "cap": cfg.cap, "reserve": cfg.reserve, "slots": cfg.slots, "margin": cfg.margin, "chunk_requests": len(reqs),
        "continuations": int(reqs.continuation.sum()),
        "steps_needing_continuation": int(rok[rok.continuation].groupby(["task_index", "step"]).ngroups),
        "deadlock_discards": sum(e["event"] == "deadlock_discard" for e in run.events),
        "admissions_with_overtake": int((reqs.overtook > 0).sum()),
        "max_wait_s": float(reqs.wait_s.max()),
        "peak_running": int(res.running.max()) if len(res) else 0,
        "peak_reserved": int(res.reserved.max()) if len(res) else 0,
        "starve_s": cfg.starve_s, "order": cfg.order,
        "online_refits": run.online.refits if run.online else None,
        "online_trained_on": run.online.fitted_n if run.online else None, "lookup": cfg.lookup if cfg.order == "lookup" else None,
        "starving_promotions": sum(e["event"] == "starving" for e in run.events),
        "starving_admissions": int(reqs.starving.sum()),
        "new_wait_p50_s": float(reqs[~reqs.continuation].wait_s.median()),
        "new_wait_p95_s": float(reqs[~reqs.continuation].wait_s.quantile(.95)),
        "alpha": cfg.alpha, "fifo_conc": cfg.fifo_conc if cfg.alpha is not None else None,
        "split_breaker": cfg.split_breaker if cfg.alpha is not None else None,
        "admitted_by_P": int((reqs.path == "P").sum()) if "path" in reqs else None,
        "admitted_by_F": int((reqs.path == "F").sum()) if "path" in reqs else None,
        "admitted_oversized": int((reqs.part == "B").sum()) if "part" in reqs else None,
        "oversized_fails": sum(e["event"] == "oversized_fail" for e in run.events),
        "slot_reuse": cfg.slot_reuse, "slot_order": cfg.slot_order if cfg.slot_reuse else None,
        "guard_order": cfg.guard_order if cfg.slot_reuse else None,
        "max_running": cfg.max_running, "deadline_k": cfg.deadline_k,
        "leftover_erases": run.leftover_erases,
        "prompt_reused_pct": float(100 * reqs.cache_n.sum() / max(reqs.cache_n.sum() + reqs.prompt_n.sum(), 1)),
        "starve_blocked_s": float((res.t.diff().shift(-1).fillna(0) * res.blocked_by_starving).sum()) if len(res) else 0.0,
    })
    tdf.to_csv(out / "tasks.csv", index=False)
    reqs.to_csv(out / "requests.csv", index=False)
    res.to_csv(out / "reservations.csv", index=False)
    pd.DataFrame(run.dispatches).to_csv(out / "dispatches.csv", index=False)
    if run.online is not None:
        pd.DataFrame(run.model_log).to_csv(out / "online_model.csv", index=False)
    pd.DataFrame(run.events, columns=["t", "event", "partition", "task_id", "step", "cells"]).to_csv(out / "events.csv", index=False)
    pd.DataFrame(run.tl.rows).to_csv(out / "timeline.csv", index=False)
    mem.to_csv(out / "memory.csv", index=False)
    pd.DataFrame([{**s, "t": s["t"] - t0} for s in gpu.samples],
                 columns=["t", "gpu_uuid", "used_mib", "total_mib"]).to_csv(out / "gpu_memory.csv", index=False)
    pd.DataFrame([{**s, "t": s["t"] - t0} for s in kv.samples],
                 columns=["t", "used_cells", "capacity_cells", "usage_ratio", "requests_processing"]).to_csv(
        out / "kv_memory.csv", index=False)
    (out / "meta.json").write_text(json.dumps({"env": environment(), "run": asdict(cfg), "summary": summary,
                                               "server_args": " ".join(server.args()), "doc": __doc__},
                                              indent=2, default=str))
    print(f"[{cfg.name}] ok {summary['ok']}/{summary['n_tasks']} failed {summary['failed']} | "
          f"p50 {summary['completion_p50_s']:.1f}s p95 {summary['completion_p95_s']:.1f}s | "
          f"{summary['throughput_tasks_min']:.2f} tasks/min | peak running {summary['peak_running']} | "
          f"continuations {summary['continuations']} deadlock discards {summary['deadlock_discards']}", flush=True)
    return summary


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--name", required=True)
    p.add_argument("--cap", type=int, required=True)
    p.add_argument("--trace", default="data/traces/agenttrace_50_seed2.jsonl")
    p.add_argument("--n-tasks", type=int, default=50)
    p.add_argument("--rate", type=float, default=0.2)
    p.add_argument("--reserve", choices=("cap", "min_true"), default="cap")
    p.add_argument("--group", default="mem_admission")
    p.add_argument("--starve-s", type=float, default=None, help="experiment 5 starvation threshold (s); off if unset")
    p.add_argument("--order", choices=("need", "lookup", "online_median", "online_gbm"), default="need",
                   help="new-request order (lookup = Exp 6; online_median / online_gbm = Exp 7a / 7b)")
    p.add_argument("--lookup", default="length_lookup.json", help="lookup file in experiments/mem_admission/")
    p.add_argument("--alpha", type=float, default=None, help="experiment 10: share of the pool for partition P")
    p.add_argument("--fifo-conc", type=int, default=2, help="experiment 10: max F requests in flight")
    p.add_argument("--slot-reuse", action="store_true",
                   help="experiment 11: keep finished caches as leftovers and reuse them (no erase per step)")
    p.add_argument("--slot-order", choices=("lcp", "own_first"), default="lcp",
                   help="experiment 11 slot choice (lcp = E11, own_first = E11b)")
    p.add_argument("--deadline-k", type=float, default=None,
                   help="experiment 13: order new requests by task arrival + k * min(predicted tokens, cap) (s/token)")
    p.add_argument("--slots", type=int, default=32,
                   help="llama.cpp slots (-np); experiment 12b uses 4, as FIFO N4, so at most 4 caches exist")
    p.add_argument("--max-running", type=int, default=None,
                   help="experiment 12: at most this many requests running at once (continuations count)")
    p.add_argument("--guard-order", choices=("lru", "finished_first"), default="lru",
                   help="experiment 11 memory guard order (lru = E11/E11b, finished_first = E11c)")
    p.add_argument("--split-breaker", choices=("global", "partition"), default="global",
                   help="experiment 10: deadlock breaker scope (global = E10/E10b, partition = E10c)")
    a = p.parse_args()
    execute(Config(name=a.name, cap=a.cap, reserve=a.reserve, trace=a.trace, n_tasks=a.n_tasks,
                   arrival_rate=a.rate, starve_s=a.starve_s, order=a.order, lookup=a.lookup,
                   alpha=a.alpha, fifo_conc=a.fifo_conc, split_breaker=a.split_breaker, slot_reuse=a.slot_reuse,
                   slot_order=a.slot_order, guard_order=a.guard_order,
                   max_running=a.max_running, slots=a.slots, deadline_k=a.deadline_k), a.group)


if __name__ == "__main__":
    main()
