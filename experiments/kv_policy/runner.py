"""FIFO N4 with a stay / swap / discard KV policy at every tool call (experiments 1 and 2).

Same workload, arrivals, deadlines and server settings as the Phase 1 request-FIFO runner (phase1/runner.py), with:
  - at most N requests running (FIFO N4), but the server gets --slots slots (default 16), so a task that stays
    keeps its own slot while N other requests run. Slots are assigned by this scheduler (id_slot), never by the server.
  - a decision at every tool call (experiments/kv_policy/policy.py):
      stay     the slot is kept; the next request reuses the cache
      swap     POST /slots/{s}?action=save to /dev/shm, erase; on dispatch, restore into a free slot first
      discard  erase now; the next request prefills its whole prompt
    A slot is erased whenever its task finishes or fails, so the pool only ever holds running or staying tasks.
    If a request needs a slot and none is free, the least recently idle staying task is discarded (reclaim).
  - requeue mode after the tool returns:
      back   (experiment 1) the next request joins the back of the single FIFO, as in Phase 1
      front  (experiment 2) returning requests queue ahead of every new task (FIFO among themselves); the head
             returning request is dispatched only when its KV need fits the pool
             (capacity - reserved >= prompt + output - its own resident cells). While it does not fit, staying
             caches of other tasks are reclaimed (discarded, least recently idle first); if it still does not fit,
             it waits for running requests to finish and nothing behind it is dispatched (strict FIFO).
             New tasks (first requests) are dispatched without a space check, as in Phase 1.
Reserved cells = sum over running requests of (prompt + output budget) + sum over staying tasks of their cells.

Outputs (results/kv_policy/<name>/): the Phase 1 files (tasks, requests, timeline, kv_memory, gpu_memory, memory,
meta.json with the same summary fields), plus decisions.csv (one row per tool call, with predicted and measured
costs) and events.csv (reclaims, swap and restore failures).
"""

import argparse
import asyncio
import json
import shutil
import tempfile
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from experiments.kv_policy.policy import CostModel, Policy, PolicyConfig
from harness.client import complete, new_session
from harness.kv_memory import KvMemSampler
from harness.memory import GpuMemSampler, MiB
from harness.results import RESULTS, environment
from harness.server import Server, ServerConfig
from phase1.runner import TaskRecord, Timeline, summarize
from workloads.trace import load

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class RunConfig:
    name: str
    requeue: str = "back"                 # back (experiment 1) or front (experiment 2)
    trace: str = "data/traces/agenttrace_full_500_seed2.jsonl"
    n_tasks: int = 500
    arrival_rate: float = 0.2             # tasks per second, Poisson; <= 0: burst
    concurrency: int = 4
    slots: int = 16
    ctx: int = 32768
    deadline_s: float = 21600
    request_timeout_s: float = 1800
    seed: int = 2
    policy: PolicyConfig = field(default_factory=PolicyConfig)
    force_action: Optional[str] = None    # stay | swap | discard: ignore the policy (ablations)


@dataclass
class TaskState:
    index: int
    next_step: int = 0
    history: list = field(default_factory=list)
    slot: Optional[int] = None            # slot holding this task's cache while it stays
    resident: int = 0                     # cells in that slot
    p_eff: int = 0                        # resident tokens that were prefilled
    g_eff: int = 0                        # resident tokens that were generated
    swap_file: Optional[str] = None
    swap_P: int = 0
    swap_G: int = 0
    resume: str = "new"                   # how the next request starts: new, stay, swap, discard, reclaimed
    runnable_at: Optional[float] = None
    idle_since: float = 0.0
    terminal: bool = False


class Run:
    def __init__(self, cfg: RunConfig, url: str, slot_dir: Path, tasks: list, offsets):
        self.cfg, self.url, self.slot_dir, self.tasks, self.offsets = cfg, url, slot_dir, tasks, offsets
        self.policy = Policy(CostModel(), cfg.policy)
        self.states = [TaskState(i) for i in range(len(tasks))]
        self.recs = [TaskRecord(t.task_id, t.cls, t.n_steps, t.total_tokens) for t in tasks]
        self.requests, self.decisions, self.events = [], [], []
        self.fresh, self.returning = deque(), deque()   # back mode uses only `fresh`
        self.cond = asyncio.Condition()
        self.closed = False
        self.free_slots = list(range(cfg.slots))
        self.staying = {}                               # slot -> TaskState
        self.running = {}                               # worker -> (state, need_cells, ctx_for_others)
        self.host_used = 0
        self.completed = 0
        self.all_done = asyncio.Event()
        self.loop = asyncio.get_running_loop()
        self.t0 = self.loop.time()
        self.tl = Timeline(self.t0)
        self.tool_jobs = []

    # ---------- accounting ----------
    def reserved(self, exclude_state: Optional[TaskState] = None) -> int:
        run = sum(n for s, n, _ in self.running.values() if s is not exclude_state)
        stay = sum(s.resident for s in self.staying.values() if s is not exclude_state)
        return run + stay

    def need(self, st: TaskState) -> int:
        return len(st.history) + self.tasks[st.index].steps[st.next_step].n_gen

    def queue_order(self) -> list:
        return list(self.returning) + list(self.fresh)

    def event(self, kind: str, st: TaskState, **kw):
        self.events.append({"t": self.loop.time() - self.t0, "event": kind,
                            "task_id": self.tasks[st.index].task_id, "step": st.next_step, **kw})

    # ---------- server slot actions ----------
    async def slot_action(self, session, slot: int, action: str, body: dict = None) -> dict:
        async with session.post(f"{self.url}/slots/{slot}?action={action}", json=body or {}) as r:
            out = await r.json(content_type=None)
            if r.status != 200:
                raise RuntimeError(f"{action} slot {slot}: {r.status} {str(out)[:200]}")
            return out

    async def erase(self, session, slot: int):
        await self.slot_action(session, slot, "erase")

    def drop_swap_file(self, st: TaskState):
        if st.swap_file:
            (self.slot_dir / st.swap_file).unlink(missing_ok=True)
            self.host_used -= st.swap_P + st.swap_G
            st.swap_file, st.swap_P, st.swap_G = None, 0, 0

    async def reclaim_one(self, session, exclude: Optional[TaskState] = None) -> bool:
        """Discard the least recently idle staying task. Returns False if there is none."""
        cands = [s for s in self.staying.values() if s is not exclude]
        if not cands:
            return False
        st = min(cands, key=lambda s: s.idle_since)
        slot = st.slot
        del self.staying[slot]
        await self.erase(session, slot)
        self.free_slots.append(slot)
        self.event("reclaim", st, slot=slot, cells=st.resident)
        st.slot, st.resident, st.p_eff, st.g_eff, st.resume = None, 0, 0, 0, "reclaimed"
        return True

    # ---------- queue ----------
    async def put(self, st: TaskState, returning: bool):
        async with self.cond:
            st.runnable_at = self.loop.time()
            (self.returning if returning and self.cfg.requeue == "front" else self.fresh).append(st)
            self.tl.mark(st.runnable_at, dq=+1, dready=+1)
            self.cond.notify_all()

    async def take(self, session) -> Optional[TaskState]:
        async with self.cond:
            while True:
                if self.returning:
                    head = self.returning[0]
                    while self.need(head) - head.resident > self.cfg.ctx - self.reserved(exclude_state=head):
                        if not await self.reclaim_one(session, exclude=head):
                            break
                    fits = self.need(head) - head.resident <= self.cfg.ctx - self.reserved(exclude_state=head)
                    if fits or not self.running:
                        return self.returning.popleft()
                elif self.fresh:
                    return self.fresh.popleft()
                elif self.closed:
                    return None
                await self.cond.wait()

    async def notify(self):
        async with self.cond:
            self.cond.notify_all()

    # ---------- task lifecycle ----------
    def finish(self, st: TaskState, status: str, error: Optional[str] = None):
        if st.terminal:
            return
        st.terminal = True
        rec = self.recs[st.index]
        rec.status, rec.error, rec.finish = status, error, self.loop.time()
        self.completed += 1
        if self.completed == len(self.states):
            self.all_done.set()

    async def release_slot(self, session, st: TaskState, slot: Optional[int]):
        if slot is None:
            return
        self.staying.pop(slot, None)
        await self.erase(session, slot)
        self.free_slots.append(slot)
        st.slot, st.resident = None, 0

    async def acquire_slot(self, session, st: TaskState) -> int:
        if st.slot is not None:                         # stayed: keep using its own slot
            self.staying.pop(st.slot, None)
            return st.slot
        while not self.free_slots:
            if not await self.reclaim_one(session):
                raise RuntimeError("no free slot and nothing to reclaim")
        return self.free_slots.pop()

    async def wait_for_tool(self, st: TaskState, delay: float):
        rec = self.recs[st.index]
        left = self.cfg.deadline_s - (self.loop.time() - rec.arrival)
        if left <= 0 or delay >= left:
            if left > 0:
                await asyncio.sleep(left)
            self.tl.mark(self.loop.time(), dtool=-1)
            async with self.cond:
                await self.drop_task_resources(st)
                self.finish(st, "timeout_queued", "deadline while waiting for tool")
                self.cond.notify_all()
            return
        t = self.loop.time()
        await asyncio.sleep(delay)
        self.policy.observe_tool(self.loop.time() - t)
        if st.terminal:
            return
        st.history = st.history + self.tasks[st.index].steps[st.next_step].input
        self.tl.mark(self.loop.time(), dtool=-1)
        await self.put(st, returning=True)

    async def drop_task_resources(self, st: TaskState):
        async with new_session(limit=2) as s:
            await self.release_slot(s, st, st.slot)
        self.drop_swap_file(st)

    async def worker(self, w: int, session):
        cfg = self.cfg
        while (st := await self.take(session)) is not None:
            now = self.loop.time()
            rec = self.recs[st.index]
            rec.worker = w
            if rec.admit is None:
                rec.admit = now
            wait_s = now - st.runnable_at
            rec.ready_queue_s += wait_s
            self.tl.mark(now, dq=-1, dready=-1)
            left = cfg.deadline_s - (now - rec.arrival)
            task = self.tasks[st.index]
            k = st.next_step
            step = task.steps[k]
            if left <= 0:
                await self.drop_task_resources(st)
                self.finish(st, "timeout_queued", "deadline while queued")
                await self.notify()
                continue

            slot = await self.acquire_slot(session, st)
            # counted as running from here, so other workers' space checks see it during a restore too
            self.running[w] = (st, self.need(st), len(st.history) + step.n_gen / 2)
            resume = st.resume
            restore = {}
            if st.swap_file:
                try:
                    t = time.monotonic()
                    out = await self.slot_action(session, slot, "restore", {"filename": st.swap_file})
                    restore = {"restore_ms": out["timings"]["restore_ms"],
                               "restore_client_ms": (time.monotonic() - t) * 1e3,
                               "restored_tokens": out["n_restored"]}
                    st.p_eff, st.g_eff, st.resident = st.swap_P, st.swap_G, out["n_restored"]
                except Exception as e:
                    self.event("restore_failed", st, slot=slot, error=f"{type(e).__name__}: {e}"[:200])
                    await self.erase(session, slot)
                    resume, st.p_eff, st.g_eff, st.resident = "swap_restore_failed", 0, 0, 0
                self.drop_swap_file(st)
            self.tl.mark(self.loop.time(), dr=+1)
            try:
                r = await asyncio.wait_for(
                    complete(session, self.url, st.history, step.n_gen, cache_prompt=True,
                             return_tokens=True, id_slot=slot, timeout=cfg.request_timeout_s), timeout=left)
            except asyncio.TimeoutError:
                self.running.pop(w, None)
                self.tl.mark(self.loop.time(), dr=-1)
                await self.release_slot(session, st, slot)
                self.finish(st, "timeout_running", "deadline during request")
                await self.notify()
                continue
            self.running.pop(w, None)
            tokens = r.pop("tokens", [])
            self.policy.observe_service(r["e2e_s"])
            r.update(task_id=task.task_id, task_index=st.index, cls=task.cls, step=k, worker=w, slot=slot,
                     resume=resume, t_rel=r["t_send"] - self.t0, ready_queue_s=wait_s, **restore)
            self.requests.append(r)
            if r["error"]:
                self.tl.mark(self.loop.time(), dr=-1)
                await self.release_slot(session, st, slot)
                self.finish(st, "failed", r["error"])
                await self.notify()
                continue

            reused = (r.get("cache_n") or 0) > 0
            st.p_eff = (st.p_eff if reused else 0) + (r.get("prompt_n") or 0)
            st.g_eff = (st.g_eff if reused else 0) + (r.get("predicted_n") or 0)
            st.history = st.history + tokens
            rec.steps_done = k + 1
            st.next_step += 1
            if st.next_step == task.n_steps:
                self.tl.mark(self.loop.time(), dr=-1)
                await self.release_slot(session, st, slot)
                self.finish(st, "ok")
                await self.notify()
                continue

            # ---- tool call: stay, swap or discard ----
            delay = step.tool_delay_s or 0.0
            rec.tool_s += delay
            P, G = st.p_eff, st.g_eff
            others = [v for v in self.running.values()]
            order = self.queue_order()
            headroom = max((self.need(q) - q.resident for q in order[:cfg.concurrency]), default=0)
            queue_ahead = len(order) if cfg.requeue == "back" else len(self.returning)
            spare = bool(self.free_slots) or len(self.staying) < cfg.slots - cfg.concurrency
            d = self.policy.decide(P, G, n_dec=len(others), dec_ctx=sum(c for _, _, c in others),
                                   reserved_others=self.reserved(exclude_state=st), headroom=headroom,
                                   queue_ahead=queue_ahead, workers=cfg.concurrency,
                                   host_free_tokens=cfg.policy.host_budget_tokens - self.host_used,
                                   spare_slot=spare)
            action = cfg.force_action or d["action"]
            d.update(t=self.loop.time() - self.t0, task_id=task.task_id, step=k, slot=slot, policy_action=d["action"],
                     action=action, resident_cells=len(st.history))
            if action == "swap":
                fname = f"{st.index}_{k}.bin"
                try:
                    t = time.monotonic()
                    out = await self.slot_action(session, slot, "save", {"filename": fname})
                    d.update(save_ms=out["timings"]["save_ms"], save_client_ms=(time.monotonic() - t) * 1e3,
                             saved_tokens=out["n_saved"])
                    st.swap_file, st.swap_P, st.swap_G = fname, P, G
                    self.host_used += P + G
                except Exception as e:
                    self.event("swap_failed", st, slot=slot, error=f"{type(e).__name__}: {e}"[:200])
                    action = d["action"] = "discard"
            if action == "stay":
                st.slot, st.resident, st.idle_since = slot, len(st.history), self.loop.time()
                self.staying[slot] = st
            else:
                await self.erase(session, slot)
                self.free_slots.append(slot)
                st.slot, st.resident = None, 0
                if action == "discard":
                    st.p_eff, st.g_eff = 0, 0
            st.resume = action
            d["action"] = action
            self.decisions.append(d)
            self.tl.mark(self.loop.time(), dr=-1, dtool=+1)
            await self.notify()
            self.tool_jobs.append(asyncio.create_task(self.wait_for_tool(st, delay)))

    async def arrivals(self):
        for i, off in enumerate(self.offsets):
            await asyncio.sleep(max(0.0, self.t0 + off - self.loop.time()))
            st, rec = self.states[i], self.recs[i]
            rec.arrival = self.loop.time()
            st.history = list(self.tasks[i].steps[0].input)
            await self.put(st, returning=False)

    async def run(self):
        async with new_session(limit=self.cfg.concurrency + 8) as session:
            for s in range(self.cfg.slots):
                await self.erase(session, s)
            arr = asyncio.create_task(self.arrivals())
            workers = [asyncio.create_task(self.worker(w, session)) for w in range(self.cfg.concurrency)]
            await arr
            await self.all_done.wait()
            if self.tool_jobs:
                await asyncio.gather(*self.tool_jobs)
            async with self.cond:
                self.closed = True
                self.cond.notify_all()
            await asyncio.gather(*workers)


def make_workload(cfg: RunConfig):
    """Same task order and arrival offsets as phase1.runner.run for the same trace, n_tasks, rate and seed."""
    rng = np.random.default_rng(cfg.seed)
    pool = load(ROOT / cfg.trace)
    idx = rng.choice(len(pool), size=cfg.n_tasks, replace=cfg.n_tasks > len(pool))
    tasks = [pool[i] for i in idx]
    gaps = rng.exponential(1.0 / cfg.arrival_rate, size=len(tasks)) if cfg.arrival_rate > 0 else np.zeros(len(tasks))
    if len(gaps):
        gaps[0] = 0.0
    return tasks, np.cumsum(gaps)


def execute(cfg: RunConfig, group: str = "kv_policy") -> dict:
    out = RESULTS / group / cfg.name
    out.mkdir(parents=True, exist_ok=True)
    tasks, offsets = make_workload(cfg)
    slot_dir = Path(tempfile.mkdtemp(prefix="kvpolicy-", dir="/dev/shm"))
    server = ServerConfig(ctx=cfg.ctx, parallel=cfg.slots, kv_unified=True, cache_ram=0, mmap=False,
                          extra=["--slot-save-path", str(slot_dir)])
    try:
        with GpuMemSampler() as gpu, Server(server, out / "server.log", sample_interval=0.1) as srv:
            with KvMemSampler(srv.url) as kv:
                if not kv.samples:
                    raise RuntimeError(kv.error or "physical KV occupancy metrics unavailable")

                async def go():
                    r = Run(cfg, srv.url, slot_dir, tasks, offsets)
                    await r.run()
                    return r
                run = asyncio.run(go())
            metrics = srv.metrics()
            t0 = run.t0
            mem = pd.DataFrame([{"t": s.t - t0, "footprint_mib": s.footprint / MiB, "rss_mib": s.rss / MiB}
                                for s in srv.mem.samples])
    finally:
        shutil.rmtree(slot_dir, ignore_errors=True)

    tdf = pd.DataFrame([asdict(r) for r in run.recs])
    tdf["queue_s"] = tdf.admit - tdf.arrival
    tdf["completion_s"] = tdf.finish - tdf.arrival
    tdf["service_s"] = tdf.finish - tdf.admit
    for c in ("arrival", "admit", "finish"):
        tdf[c] = tdf[c] - t0
    reqs = pd.DataFrame(run.requests).drop(columns=["tokens"], errors="ignore")
    dec = pd.DataFrame(run.decisions)
    summary = summarize(argparse.Namespace(name=cfg.name, trace=cfg.trace, scheduler=f"request_kvpolicy_{cfg.requeue}",
                                           concurrency=cfg.concurrency, server=server, arrival_rate=cfg.arrival_rate,
                                           delay_scale=1.0, cache_prompt=True), tdf, reqs, mem, metrics, [])
    summary.update(kv.summary())
    gpu_rows = [{**s, "t": s["t"] - t0} for s in gpu.samples]
    summary.update({
        "requeue": cfg.requeue, "slots": cfg.slots, "force_action": cfg.force_action,
        "decisions": len(dec), **{f"decisions_{a}": int((dec.action == a).sum()) if len(dec) else 0
                                  for a in ("stay", "swap", "discard")},
        "reclaims": sum(e["event"] == "reclaim" for e in run.events),
        "swap_failures": sum(e["event"] == "swap_failed" for e in run.events),
        "restore_failures": sum(e["event"] == "restore_failed" for e in run.events),
        "swap_out_ms_total": float(dec.save_ms.sum()) if "save_ms" in dec else 0.0,
        "swap_in_ms_total": float(reqs.restore_ms.sum()) if "restore_ms" in reqs else 0.0,
        "gpu_peak_used_mib": max((s["used_mib"] for s in gpu_rows), default=None),
    })
    tdf.to_csv(out / "tasks.csv", index=False)
    reqs.to_csv(out / "requests.csv", index=False)
    dec.to_csv(out / "decisions.csv", index=False)
    pd.DataFrame(run.events, columns=["t", "event", "task_id", "step", "slot", "cells", "error"]).to_csv(
        out / "events.csv", index=False)
    pd.DataFrame(run.tl.rows).to_csv(out / "timeline.csv", index=False)
    mem.to_csv(out / "memory.csv", index=False)
    pd.DataFrame(gpu_rows, columns=["t", "gpu_uuid", "used_mib", "total_mib"]).to_csv(out / "gpu_memory.csv", index=False)
    pd.DataFrame([{**s, "t": s["t"] - t0} for s in kv.samples],
                 columns=["t", "used_cells", "capacity_cells", "usage_ratio", "requests_processing"]).to_csv(
        out / "kv_memory.csv", index=False)
    (out / "meta.json").write_text(json.dumps({
        "env": environment(), "run": asdict(cfg), "summary": summary, "server_args": " ".join(server.args()),
        "cost_model": run.policy.cost.source, "doc": __doc__}, indent=2, default=str))
    print(f"[{cfg.name}] ok {summary['ok']}/{summary['n_tasks']} failed {summary['failed']} "
          f"timeouts {summary['timeout_queued']}+{summary['timeout_running']} | p50 {summary['completion_p50_s']:.1f}s "
          f"p95 {summary['completion_p95_s']:.1f}s | {summary['throughput_tasks_min']:.2f} tasks/min | "
          f"stay {summary['decisions_stay']} swap {summary['decisions_swap']} discard {summary['decisions_discard']} "
          f"reclaims {summary['reclaims']}", flush=True)
    return summary


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--name", required=True)
    p.add_argument("--requeue", choices=("back", "front"), required=True)
    p.add_argument("--trace", default="data/traces/agenttrace_full_500_seed2.jsonl")
    p.add_argument("--n-tasks", type=int, default=500)
    p.add_argument("--rate", type=float, default=0.2)
    p.add_argument("--slots", type=int, default=16)
    p.add_argument("--stay-max-idle", type=float, default=30.0)
    p.add_argument("--force-action", choices=("stay", "swap", "discard"), default=None)
    p.add_argument("--group", default="kv_policy")
    a = p.parse_args()
    cfg = RunConfig(name=a.name, requeue=a.requeue, trace=a.trace, n_tasks=a.n_tasks, arrival_rate=a.rate,
                    slots=a.slots, force_action=a.force_action,
                    policy=PolicyConfig(stay_max_idle_s=a.stay_max_idle))
    execute(cfg, a.group)


if __name__ == "__main__":
    main()
