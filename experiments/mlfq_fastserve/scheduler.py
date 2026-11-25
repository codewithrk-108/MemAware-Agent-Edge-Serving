"""Experiment 3: FastServe-style skip-join multi-level feedback queue (MLFQ) on llama.cpp, N4.

FastServe (Wu et al., 2023) schedules LLM jobs with a multi-level feedback queue: a job enters the highest-priority
level whose quantum covers its first (prefill) iteration ("skip-join"), is demoted one level each time it uses up its
level's quantum, and is preempted when a higher-priority job is waiting. KV of preempted jobs is kept on the GPU when
possible and swapped to host memory when not. Output lengths are never used, so short jobs finish first without
knowing in advance which jobs are short.

Mapping onto llama.cpp (which cannot preempt a request from outside):
  job        one model request of an agent task (one step). The task's next step becomes a new job when its tool
             returns, so a task re-enters the MLFQ through skip-join after every tool call.
  chunk      a job runs in chunks of --chunk output tokens; each chunk is one /completion request with
             n_predict = chunk, sent with the full history so the slot's cache is reused (about 1 token recomputed).
             Preemption happens between chunks: iteration-level preemption at chunk granularity.
  levels     quanta of 256, 512, 1024, 2048, 4096 output tokens, then a last level with no quantum.
  skip-join  a new job enters the first level whose quantum time (quantum x running-average time per output token)
             is at least its predicted prefill time (recompute cost model, swap_vs_stay_vs_recompute/params.json).
  demotion   after using its level's quantum, a job moves to the back of the next level.
  preemption after each chunk, if a job at a strictly higher level (lower number) is waiting, the running job goes
             back to the front of its own level and its worker takes the higher-priority job.
  N4         at most 4 chunks run at once (4 workers); the server gets 16 slots so idle jobs can keep their KV.
  KV         idle KV (preempted jobs and tasks waiting on a tool) stays in its slot. Before a job is dispatched, the
             scheduler checks that its chunk fits: (history + chunk - its resident cells) <= capacity - reserved,
             where reserved = running chunks at (history + chunk) + idle jobs at their resident cells. If it does not
             fit, idle jobs are evicted, lowest priority first (preempted jobs from the lowest level, most recently
             idle first; tasks waiting on a tool last, since they return at a high level). Each eviction is a swap
             or a discard, whichever the cost models predict cheaper (swap only within the host budget). If the head
             job still does not fit, it waits for running chunks to finish and nothing behind it is dispatched.
             The same happens when a job needs a slot and none is free.
Same workload, arrival times, deadlines and server settings as Phase 1 and experiments 1-2.
"""

import argparse
import asyncio
import json
import math
import shutil
import tempfile
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd

from experiments.kv_policy.policy import CostModel
from experiments.kv_policy.runner import make_workload
from harness.client import complete, new_session
from harness.kv_memory import KvMemSampler
from harness.memory import GpuMemSampler, MiB
from harness.results import RESULTS, environment
from harness.server import Server, ServerConfig
from phase1.runner import TaskRecord, Timeline, summarize

QUANTA = (256, 512, 1024, 2048, 4096, math.inf)


@dataclass
class Config:
    name: str
    trace: str = "data/traces/agenttrace_full_500_seed2.jsonl"
    n_tasks: int = 500
    arrival_rate: float = 0.2
    concurrency: int = 4
    slots: int = 16
    ctx: int = 32768
    chunk: int = 256
    quanta: tuple = QUANTA
    deadline_s: float = 21600
    request_timeout_s: float = 1800
    seed: int = 2
    host_budget_tokens: int = 50_000
    tpot_prior_ms: float = 16.0
    ema: float = 0.05


@dataclass
class Job:
    index: int                           # task index
    step: int = 0
    history: list = field(default_factory=list)
    remaining: int = 0                   # output tokens still to generate in this step
    level: int = 0
    used: int = 0                        # output tokens used at the current level
    slot: Optional[int] = None
    resident: int = 0
    p_eff: int = 0
    g_eff: int = 0
    swap_file: Optional[str] = None
    swap_P: int = 0
    swap_G: int = 0
    state: str = "queued"                # queued, running, idle (preempted, in queue), tool, done
    idle_since: float = 0.0
    queued_at: float = 0.0
    chunks: int = 0
    preemptions: int = 0
    terminal: bool = False


class Run:
    def __init__(self, cfg: Config, url: str, slot_dir: Path, tasks: list, offsets):
        self.cfg, self.url, self.slot_dir, self.tasks, self.offsets = cfg, url, slot_dir, tasks, offsets
        self.cost = CostModel()
        self.jobs = [Job(i) for i in range(len(tasks))]
        self.recs = [TaskRecord(t.task_id, t.cls, t.n_steps, t.total_tokens) for t in tasks]
        self.levels = [deque() for _ in cfg.quanta]
        self.cond = asyncio.Condition()
        self.closed = False
        self.free_slots = list(range(cfg.slots))
        self.running = {}                # worker -> (job, reserved cells, context for contention)
        self.idle = {}                   # slot -> job holding KV but not running
        self.host_used = 0
        self.tpot_ms = cfg.tpot_prior_ms
        self.requests, self.evictions, self.joins = [], [], []
        self.loop = asyncio.get_running_loop()
        self.t0 = self.loop.time()
        self.tl = Timeline(self.t0)
        self.completed = 0
        self.all_done = asyncio.Event()
        self.tool_jobs = []

    # ---------- helpers ----------
    def now(self) -> float:
        return self.loop.time() - self.t0

    def reserved(self, exclude: Optional[Job] = None) -> int:
        return (sum(n for j, n, _ in self.running.values() if j is not exclude)
                + sum(j.resident for j in self.idle.values() if j is not exclude))

    def chunk_need(self, j: Job) -> int:
        n = min(self.cfg.chunk, j.remaining)
        return len(j.history) + n - j.resident

    def skip_join_level(self, j: Job) -> tuple:
        uncached = len(j.history) - j.resident
        others = list(self.running.values())
        t_pre = self.cost.t_rc(uncached, n_dec=len(others), dec_ctx=sum(c for _, _, c in others)) if uncached > 0 else 0.0
        for k, q in enumerate(self.cfg.quanta):
            if q * self.tpot_ms >= t_pre:
                return k, t_pre
        return len(self.cfg.quanta) - 1, t_pre

    async def slot_action(self, session, slot: int, action: str, body: dict = None) -> dict:
        async with session.post(f"{self.url}/slots/{slot}?action={action}", json=body or {}) as r:
            out = await r.json(content_type=None)
            if r.status != 200:
                raise RuntimeError(f"{action} slot {slot}: {r.status} {str(out)[:200]}")
            return out

    # ---------- eviction ----------
    def victims(self, exclude: Job) -> list:
        # lowest priority first: preempted jobs (deepest level, most recently idle), then tool-waiting tasks
        cands = [j for j in self.idle.values() if j is not exclude]
        return sorted(cands, key=lambda j: (j.state != "tool", j.level, j.idle_since), reverse=True)

    async def evict(self, session, j: Job):
        slot = j.slot
        L = j.p_eff + j.g_eff
        t_swap = self.cost.t_out(j.p_eff, j.g_eff) + self.cost.t_in(j.p_eff, j.g_eff)
        others = list(self.running.values())
        t_rc = self.cost.t_rc(L, len(others), sum(c for _, _, c in others))
        action = "swap" if t_swap < t_rc and self.host_used + L <= self.cfg.host_budget_tokens else "discard"
        row = {"t": self.now(), "task_id": self.tasks[j.index].task_id, "step": j.step, "state": j.state,
               "level": j.level, "cells": j.resident, "P": j.p_eff, "G": j.g_eff,
               "pred_t_swap_ms": t_swap, "pred_t_rc_ms": t_rc, "action": action}
        del self.idle[slot]
        if action == "swap":
            fname = f"{j.index}_{j.step}_{j.chunks}.bin"
            try:
                t = time.monotonic()
                out = await self.slot_action(session, slot, "save", {"filename": fname})
                row.update(save_ms=out["timings"]["save_ms"], save_client_ms=(time.monotonic() - t) * 1e3)
                j.swap_file, j.swap_P, j.swap_G = fname, j.p_eff, j.g_eff
                self.host_used += L
            except Exception as e:
                row.update(action="discard", error=f"{type(e).__name__}: {e}"[:200])
        await self.slot_action(session, slot, "erase")
        self.free_slots.append(slot)
        j.slot, j.resident = None, 0
        if row["action"] == "discard":
            j.p_eff = j.g_eff = 0
        self.evictions.append(row)

    def drop_swap(self, j: Job):
        if j.swap_file:
            (self.slot_dir / j.swap_file).unlink(missing_ok=True)
            self.host_used -= j.swap_P + j.swap_G
            j.swap_file, j.swap_P, j.swap_G = None, 0, 0

    async def free_job(self, session, j: Job):
        if j.slot is not None:
            self.idle.pop(j.slot, None)
            await self.slot_action(session, j.slot, "erase")
            self.free_slots.append(j.slot)
            j.slot, j.resident = None, 0
        self.drop_swap(j)

    # ---------- queue ----------
    async def enqueue(self, j: Job, front: bool = False):
        async with self.cond:
            j.queued_at = self.loop.time()
            j.state = "idle" if j.slot is not None else "queued"
            if j.slot is not None:
                self.idle[j.slot] = j
                j.idle_since = j.queued_at
            (self.levels[j.level].appendleft if front else self.levels[j.level].append)(j)
            self.tl.mark(j.queued_at, dq=+1, dready=+1)
            self.cond.notify_all()

    def head(self) -> Optional[Job]:
        for q in self.levels:
            if q:
                return q[0]
        return None

    def higher_waiting(self, level: int) -> bool:
        return any(self.levels[k] for k in range(level))

    async def take(self, session) -> Optional[Job]:
        async with self.cond:
            while True:
                j = self.head()
                if j is not None:
                    while (self.chunk_need(j) > self.cfg.ctx - self.reserved(exclude=j)
                           or (j.slot is None and not self.free_slots)):
                        v = self.victims(exclude=j)
                        if not v:
                            break
                        await self.evict(session, v[0])
                    fits = self.chunk_need(j) <= self.cfg.ctx - self.reserved(exclude=j)
                    if (fits or not self.running) and (j.slot is not None or self.free_slots):
                        self.levels[j.level].popleft()
                        self.idle.pop(j.slot, None) if j.slot is not None else None
                        return j
                elif self.closed:
                    return None
                await self.cond.wait()

    async def notify(self):
        async with self.cond:
            self.cond.notify_all()

    # ---------- lifecycle ----------
    def finish(self, j: Job, status: str, error: Optional[str] = None):
        if j.terminal:
            return
        j.terminal, j.state = True, "done"
        rec = self.recs[j.index]
        rec.status, rec.error, rec.finish = status, error, self.loop.time()
        self.completed += 1
        if self.completed == len(self.jobs):
            self.all_done.set()

    async def new_step(self, j: Job, step: int):
        task = self.tasks[j.index]
        j.step, j.remaining, j.used = step, task.steps[step].n_gen, 0
        j.level, t_pre = self.skip_join_level(j)
        self.joins.append({"t": self.now(), "task_id": task.task_id, "step": step, "level": j.level,
                           "pred_prefill_ms": t_pre, "uncached": len(j.history) - j.resident,
                           "tpot_ms": self.tpot_ms})
        await self.enqueue(j)

    async def tool_wait(self, j: Job, delay: float):
        rec = self.recs[j.index]
        left = self.cfg.deadline_s - (self.loop.time() - rec.arrival)
        if delay >= left:
            await asyncio.sleep(max(left, 0))
            self.tl.mark(self.loop.time(), dtool=-1)
            async with self.cond:
                async with new_session(limit=2) as s:
                    await self.free_job(s, j)
                self.finish(j, "timeout_queued", "deadline while waiting for tool")
                self.cond.notify_all()
            return
        await asyncio.sleep(delay)
        if j.terminal:
            return
        async with self.cond:
            self.idle.pop(j.slot, None) if j.slot is not None else None
        j.history = j.history + self.tasks[j.index].steps[j.step + 1].input
        self.tl.mark(self.loop.time(), dtool=-1)
        await self.new_step(j, j.step + 1)

    async def worker(self, w: int, session):
        cfg = self.cfg
        while (j := await self.take(session)) is not None:
            rec, task = self.recs[j.index], self.tasks[j.index]
            now = self.loop.time()
            rec.worker = w
            if rec.admit is None:
                rec.admit = now
            rec.ready_queue_s += now - j.queued_at
            self.tl.mark(now, dq=-1, dready=-1)
            left = cfg.deadline_s - (now - rec.arrival)
            if left <= 0:
                await self.free_job(session, j)
                self.finish(j, "timeout_queued", "deadline while queued")
                await self.notify()
                continue
            if j.slot is None:
                j.slot = self.free_slots.pop()
            j.state = "running"
            restore = {}
            if j.swap_file:
                try:
                    t = time.monotonic()
                    out = await self.slot_action(session, j.slot, "restore", {"filename": j.swap_file})
                    restore = {"restore_ms": out["timings"]["restore_ms"],
                               "restore_client_ms": (time.monotonic() - t) * 1e3}
                    j.p_eff, j.g_eff, j.resident = j.swap_P, j.swap_G, out["n_restored"]
                except Exception as e:
                    restore = {"restore_error": f"{type(e).__name__}: {e}"[:200]}
                    await self.slot_action(session, j.slot, "erase")
                    j.p_eff = j.g_eff = j.resident = 0
                self.drop_swap(j)
            self.tl.mark(self.loop.time(), dr=+1)
            outcome = None
            while outcome is None:
                n = min(cfg.chunk, j.remaining, max(1, cfg.quanta[j.level] - j.used) if math.isfinite(cfg.quanta[j.level]) else cfg.chunk)
                self.running[w] = (j, len(j.history) + n, len(j.history) + n / 2)
                left = cfg.deadline_s - (self.loop.time() - rec.arrival)
                try:
                    r = await asyncio.wait_for(complete(session, self.url, j.history, n, cache_prompt=True,
                                                        return_tokens=True, id_slot=j.slot,
                                                        timeout=cfg.request_timeout_s), timeout=max(left, 0.001))
                except asyncio.TimeoutError:
                    outcome = "timeout"
                    break
                finally:
                    self.running.pop(w, None)
                tokens = r.pop("tokens", [])
                r.update(task_id=task.task_id, task_index=j.index, cls=task.cls, step=j.step, chunk=j.chunks,
                         level=j.level, worker=w, slot=j.slot, t_rel=r["t_send"] - self.t0, **restore)
                restore = {}
                j.chunks += 1
                self.requests.append(r)
                if r["error"]:
                    outcome = "failed"
                    break
                if r.get("tpot_ms"):
                    self.tpot_ms += cfg.ema * (r["tpot_ms"] - self.tpot_ms)
                reused = (r.get("cache_n") or 0) > 0
                j.p_eff = (j.p_eff if reused else 0) + (r.get("prompt_n") or 0)
                j.g_eff = (j.g_eff if reused else 0) + len(tokens)
                j.history = j.history + tokens
                j.resident = len(j.history)
                done = r.get("predicted_n") or n
                j.remaining -= done
                j.used += done
                if j.remaining <= 0:
                    outcome = "step_done"
                elif math.isfinite(cfg.quanta[j.level]) and j.used >= cfg.quanta[j.level]:
                    outcome = "demote"
                elif self.higher_waiting(j.level):
                    outcome = "preempt"
                r["outcome"] = outcome or "continue"
            self.tl.mark(self.loop.time(), dr=-1)
            if outcome in ("demote", "preempt", "step_done") and j.slot is not None:
                # still holds its KV: count it before any await lets another coroutine check the pool
                j.idle_since = self.loop.time()
                self.idle[j.slot] = j

            if outcome in ("failed", "timeout"):
                await self.free_job(session, j)
                self.finish(j, "failed" if outcome == "failed" else "timeout_running",
                            r["error"] if outcome == "failed" else "deadline during request")
            elif outcome == "demote":
                j.level, j.used = min(j.level + 1, len(cfg.quanta) - 1), 0
                j.preemptions += 1
                await self.enqueue(j)
            elif outcome == "preempt":
                j.preemptions += 1
                await self.enqueue(j, front=True)
            elif j.step + 1 == task.n_steps:
                rec.steps_done = j.step + 1
                await self.free_job(session, j)
                self.finish(j, "ok")
            else:
                rec.steps_done = j.step + 1
                delay = task.steps[j.step].tool_delay_s or 0.0
                rec.tool_s += delay
                async with self.cond:
                    j.state, j.idle_since = "tool", self.loop.time()
                    self.idle[j.slot] = j
                self.tl.mark(self.loop.time(), dtool=+1)
                self.tool_jobs.append(asyncio.create_task(self.tool_wait(j, delay)))
            await self.notify()

    async def arrivals(self):
        for i, off in enumerate(self.offsets):
            await asyncio.sleep(max(0.0, self.t0 + off - self.loop.time()))
            self.recs[i].arrival = self.loop.time()
            self.jobs[i].history = list(self.tasks[i].steps[0].input)
            await self.new_step(self.jobs[i], 0)

    async def run(self):
        async with new_session(limit=self.cfg.concurrency + 8) as session:
            for s in range(self.cfg.slots):
                await self.slot_action(session, s, "erase")
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


def execute(cfg: Config, group: str = "mlfq_fastserve") -> dict:
    out = RESULTS / group / cfg.name
    out.mkdir(parents=True, exist_ok=True)
    tasks, offsets = make_workload(cfg)
    slot_dir = Path(tempfile.mkdtemp(prefix="mlfq-", dir="/dev/shm"))
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
    tdf["preemptions"] = [j.preemptions for j in run.jobs]
    tdf["chunks"] = [j.chunks for j in run.jobs]
    reqs = pd.DataFrame(run.requests)
    ev = pd.DataFrame(run.evictions)
    summary = summarize(argparse.Namespace(name=cfg.name, trace=cfg.trace, scheduler="mlfq_fastserve",
                                           concurrency=cfg.concurrency, server=server, arrival_rate=cfg.arrival_rate,
                                           delay_scale=1.0, cache_prompt=True), tdf, reqs, mem, metrics, [])
    summary.update(kv.summary())
    rok = reqs[reqs.error.isna()]
    resumes = rok.groupby(["task_index", "step"]).cumcount() > 0
    summary.update({
        "chunk_requests": len(reqs), "chunk_tokens": cfg.chunk, "quanta": str(cfg.quanta), "slots": cfg.slots,
        "preemptions": int((rok.outcome == "preempt").sum()), "demotions": int((rok.outcome == "demote").sum()),
        "evictions_swap": int((ev.action == "swap").sum()) if len(ev) else 0,
        "evictions_discard": int((ev.action == "discard").sum()) if len(ev) else 0,
        "resume_recomputed_tokens": int(rok[resumes].prompt_n.sum()),
        "join_levels": str(pd.Series([x["level"] for x in run.joins]).value_counts().sort_index().to_dict()),
    })
    tdf.to_csv(out / "tasks.csv", index=False)
    reqs.to_csv(out / "requests.csv", index=False)
    ev.to_csv(out / "evictions.csv", index=False)
    pd.DataFrame(run.joins).to_csv(out / "joins.csv", index=False)
    pd.DataFrame(run.tl.rows).to_csv(out / "timeline.csv", index=False)
    mem.to_csv(out / "memory.csv", index=False)
    pd.DataFrame([{**s, "t": s["t"] - t0} for s in gpu.samples],
                 columns=["t", "gpu_uuid", "used_mib", "total_mib"]).to_csv(out / "gpu_memory.csv", index=False)
    pd.DataFrame([{**s, "t": s["t"] - t0} for s in kv.samples],
                 columns=["t", "used_cells", "capacity_cells", "usage_ratio", "requests_processing"]).to_csv(
        out / "kv_memory.csv", index=False)
    (out / "meta.json").write_text(json.dumps({
        "env": environment(), "run": asdict(cfg), "summary": summary, "server_args": " ".join(server.args()),
        "cost_model": run.cost.source, "doc": __doc__}, indent=2, default=str))
    print(f"[{cfg.name}] ok {summary['ok']}/{summary['n_tasks']} failed {summary['failed']} "
          f"timeouts {summary['timeout_queued']}+{summary['timeout_running']} | p50 {summary['completion_p50_s']:.1f}s "
          f"p95 {summary['completion_p95_s']:.1f}s | {summary['throughput_tasks_min']:.2f} tasks/min | "
          f"chunks {summary['chunk_requests']} preempt {summary['preemptions']} demote {summary['demotions']} "
          f"evict swap/discard {summary['evictions_swap']}/{summary['evictions_discard']}", flush=True)
    return summary


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--name", required=True)
    p.add_argument("--trace", default="data/traces/agenttrace_full_500_seed2.jsonl")
    p.add_argument("--n-tasks", type=int, default=500)
    p.add_argument("--rate", type=float, default=0.2)
    p.add_argument("--chunk", type=int, default=256)
    p.add_argument("--group", default="mlfq_fastserve")
    a = p.parse_args()
    execute(Config(name=a.name, trace=a.trace, n_tasks=a.n_tasks, arrival_rate=a.rate, chunk=a.chunk), a.group)


if __name__ == "__main__":
    main()
