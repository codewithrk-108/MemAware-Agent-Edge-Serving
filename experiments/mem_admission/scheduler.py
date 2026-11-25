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

    def free(self) -> int:
        return self.cfg.ctx - self.cfg.margin - self.reserved()

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

    def new_key(self, j: Job):
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
                              "blocked_by_starving": self.starve_blocked})

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
            while True:
                progress = False
                # 1. continuations, smallest extra first
                for j in sorted(self.cont_q, key=lambda x: (self.reserve_len(x), x.ready_at)):
                    extra = self.reserve_len(j)
                    if extra <= self.free():
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
                        if need <= self.free() and self.free_slots:
                            fb = self.free()
                            self.new_q.remove(j)
                            j.slot = self.free_slots.pop()
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
                        if need <= self.free() and self.free_slots:
                            overtook = sum(1 for o in older if o.ready_at < j.ready_at and o in self.new_q)
                            fb = self.free()
                            self.new_q.remove(j)
                            j.slot = self.free_slots.pop()
                            j.reserved = need
                            self.start(session, j, cont=False, free_before=fb, overtook=overtook)
                            admitted_new += 1
                            progress = True
                # 3. deadlock breaker
                if not self.running and self.cont_q and not progress:
                    v = min(self.cont_q, key=lambda x: x.resident)
                    self.cont_q.remove(v)
                    await self.erase(session, v.slot)
                    self.free_slots.append(v.slot)
                    self.events.append({"t": self.now(), "event": "deadlock_discard",
                                        "task_id": self.tasks[v.index].task_id, "step": v.step, "cells": v.resident})
                    v.slot, v.resident = None, 0
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
                "starving": starving, "pred": j.pred if not cont else None, "pred_n": j.pred_n if not cont else None}
        self.bg.append(asyncio.create_task(self.run_chunk(session, j, meta)))

    # ---------- execution ----------
    async def run_chunk(self, session, j: Job, meta: dict):
        cfg, task, rec = self.cfg, self.tasks[j.index], self.recs[j.index]
        n = self.chunk_len(j)
        left = cfg.deadline_s - (self.loop.time() - rec.arrival)
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
                self.finish(j, "failed" if "deadline" not in r["error"] else "timeout_running", r["error"])
            else:
                j.history = j.history + tokens
                j.remaining -= r.get("predicted_n") or n
                j.gen_step += r.get("predicted_n") or n
                if j.remaining > 0:                                   # continuation: keep the cache, wait
                    j.resident, j.ready_at = len(j.history), self.loop.time()
                    self.cont_q.append(j)
                    self.tl.mark(j.ready_at, dq=+1, dready=+1)
                else:                                                 # step done: discard the cache
                    await self.erase(session, j.slot)
                    self.free_slots.append(j.slot)
                    j.slot = None
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
        "starve_blocked_s": float((res.t.diff().shift(-1).fillna(0) * res.blocked_by_starving).sum()) if len(res) else 0.0,
    })
    tdf.to_csv(out / "tasks.csv", index=False)
    reqs.to_csv(out / "requests.csv", index=False)
    res.to_csv(out / "reservations.csv", index=False)
    pd.DataFrame(run.dispatches).to_csv(out / "dispatches.csv", index=False)
    if run.online is not None:
        pd.DataFrame(run.model_log).to_csv(out / "online_model.csv", index=False)
    pd.DataFrame(run.events, columns=["t", "event", "task_id", "step", "cells"]).to_csv(out / "events.csv", index=False)
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
    a = p.parse_args()
    execute(Config(name=a.name, cap=a.cap, reserve=a.reserve, trace=a.trace, n_tasks=a.n_tasks,
                   arrival_rate=a.rate, starve_s=a.starve_s, order=a.order, lookup=a.lookup), a.group)


if __name__ == "__main__":
    main()
