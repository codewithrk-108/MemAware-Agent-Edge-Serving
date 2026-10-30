"""Phase 1 baseline: FIFO queue with fixed concurrency in front of llama-server.

Tasks arrive open-loop (Poisson) and wait in one FIFO queue. The default request scheduler runs one model step per
dequeue and releases its worker while the tool runs outside the FIFO. When the tool finishes, its response is
appended and the next step joins the FIFO tail as runnable work. The optional task scheduler has N workers run
whole tasks, holding workers through tool waits.
llama-server gets N slots, so the Python scheduler decides admission.

A task that has not finished `deadline_s` after its arrival is abandoned (timeout), whether queued or running.
"""

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from harness.client import complete, new_session
from harness.memory import GpuMemSampler, MiB
from harness.results import RESULTS, check_power, environment
from harness.server import Server, ServerConfig
from workloads.trace import load

ROOT = Path(__file__).resolve().parents[1]
ADMISSION_CHECK_COLUMNS = (
    "t", "worker", "task_index", "task_id", "step", "queue_length_before",
    "active_count", "active_reserved_tokens", "kv_capacity_tokens", "kv_budget_remaining_tokens",
    "head_prompt_tokens", "head_output_budget_tokens", "head_need_tokens", "head_fits",
    "fitting_later_count", "smallest_fitting_later_need_tokens", "backfill_opportunity",
    "request_outcome",
)


@dataclass
class RunConfig:
    name: str
    trace: str = "data/traces/synthetic.jsonl"
    n_tasks: int = 100
    arrival_rate: float = 0.5        # tasks per second, Poisson; <= 0: all tasks arrive at t = 0
    concurrency: int = 8             # tasks admitted at once (FIFO workers)
    delay_scale: float = 1.0         # multiplies every tool delay; 0 removes idle periods
    cache_prompt: bool = True        # let the server reuse cached prefixes
    deadline_s: float = 240.0        # per task, from arrival
    request_timeout_s: float = 180.0
    scheduler: str = "request"       # request: one ready step at a time; task: hold worker through tools
    seed: int = 0
    server: ServerConfig = field(default_factory=ServerConfig)


@dataclass
class TaskRecord:
    task_id: str
    cls: str
    n_steps: int
    total_tokens: int
    arrival: float = 0.0
    admit: Optional[float] = None
    finish: Optional[float] = None
    status: str = "pending"          # ok, failed, timeout_queued, timeout_running
    error: Optional[str] = None
    steps_done: int = 0
    tool_s: float = 0.0
    ready_queue_s: float = 0.0
    worker: int = -1


class Timeline:
    """Queue length and running tasks over time, one row per change."""

    def __init__(self, t0: float):
        self.t0, self.rows = t0, []
        self.admission_checks = []
        self.queued, self.runnable, self.running, self.tool_waiting = 0, 0, 0, 0

    def mark(self, now: float, dq: int = 0, dr: int = 0, dready: int = 0, dtool: int = 0):
        self.queued += dq
        self.runnable += dready
        self.running += dr
        self.tool_waiting += dtool
        self.rows.append({"t": now - self.t0, "queued": self.queued, "runnable": self.runnable,
                          "running": self.running, "tool_waiting": self.tool_waiting})


@dataclass
class RequestTaskState:
    task_index: int
    next_step: int = 0
    history: list = field(default_factory=list)
    enqueued_at: float = 0.0
    runnable_at: Optional[float] = None
    runnable: bool = False
    terminal: bool = False


class RunnableFifo:
    """FIFO containing only model steps whose inputs are ready."""

    def __init__(self):
        self.entries = []
        self.closed = False
        self.changed = asyncio.Condition()

    async def put(self, state: RequestTaskState):
        async with self.changed:
            state.runnable = True
            self.entries.append(state)
            self.changed.notify_all()

    async def take(self) -> Optional[RequestTaskState]:
        async with self.changed:
            while True:
                if self.entries:
                    return self.entries.pop(0)
                if self.closed:
                    return None
                await self.changed.wait()

    async def close(self):
        async with self.changed:
            self.closed = True
            self.changed.notify_all()


async def run_task(cfg: RunConfig, session, url: str, task, rec: TaskRecord, requests: list, t0: float):
    loop = asyncio.get_running_loop()
    history = []
    for k, step in enumerate(task.steps):
        history = history + step.input
        r = await complete(session, url, history, step.n_gen, cache_prompt=cfg.cache_prompt,
                           return_tokens=True, timeout=cfg.request_timeout_s)
        r.update(task_id=task.task_id, cls=task.cls, step=k, worker=rec.worker, t_rel=r["t_send"] - t0)
        r.pop("tokens_list", None)
        requests.append(r)
        if r["error"]:
            rec.status, rec.error = "failed", r["error"]
            return
        history = history + r["tokens"]
        rec.steps_done = k + 1
        if step.tool_delay_s is not None and cfg.delay_scale > 0:
            d = step.tool_delay_s * cfg.delay_scale
            rec.tool_s += d
            await asyncio.sleep(d)
    rec.status = "ok"
    rec.finish = loop.time()


async def run_task_fifo(cfg: RunConfig, srv: Server, tasks: list, offsets) -> tuple:
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    queue: asyncio.Queue = asyncio.Queue()   # FIFO
    recs = [TaskRecord(t.task_id, t.cls, t.n_steps, t.total_tokens) for t in tasks]
    requests: list = []
    tl = Timeline(t0)

    async with new_session(limit=cfg.concurrency + 8) as session:
        async def arrivals():
            for i, off in enumerate(offsets):
                await asyncio.sleep(max(0.0, t0 + off - loop.time()))
                recs[i].arrival = loop.time()
                queue.put_nowait(i)
                tl.mark(loop.time(), dq=+1, dready=+1)
            for _ in range(cfg.concurrency):
                queue.put_nowait(None)   # sentinels come after every task, FIFO keeps that order

        async def worker(w: int):
            while (i := await queue.get()) is not None:
                rec = recs[i]
                rec.admit, rec.worker = loop.time(), w
                rec.ready_queue_s = rec.admit - rec.arrival
                tl.mark(rec.admit, dq=-1, dready=-1)
                left = cfg.deadline_s - (rec.admit - rec.arrival)
                if left <= 0:
                    rec.status = "timeout_queued"
                    continue
                tl.mark(loop.time(), dr=+1)
                try:
                    await asyncio.wait_for(run_task(cfg, session, srv.url, tasks[i], rec, requests, t0), timeout=left)
                except asyncio.TimeoutError:
                    rec.status = "timeout_running"
                if rec.finish is None:
                    rec.finish = loop.time()
                tl.mark(loop.time(), dr=-1)

        await asyncio.gather(arrivals(), *[worker(w) for w in range(cfg.concurrency)])
    return recs, requests, tl, t0


async def run_request_fifo(cfg: RunConfig, srv: Server, tasks: list, offsets) -> tuple:
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    scheduler = RunnableFifo()
    states = [RequestTaskState(i) for i in range(len(tasks))]
    recs = [TaskRecord(t.task_id, t.cls, t.n_steps, t.total_tokens) for t in tasks]
    requests = []
    tl = Timeline(t0)
    all_done = asyncio.Event()
    completed = 0
    tool_jobs = []
    active_needs = {}

    if not states:
        all_done.set()

    def finish(state: RequestTaskState, status: str, error: Optional[str] = None) -> bool:
        nonlocal completed
        if state.terminal:
            return False
        state.terminal = True
        rec = recs[state.task_index]
        rec.status, rec.error, rec.finish = status, error, loop.time()
        completed += 1
        if completed == len(states):
            all_done.set()
        return True

    async def wait_for_tool(state: RequestTaskState, delay: float):
        rec = recs[state.task_index]
        left = cfg.deadline_s - (loop.time() - rec.arrival)
        if left <= 0 or delay >= left:
            if left > 0:
                await asyncio.sleep(left)
            tl.mark(loop.time(), dtool=-1)
            finish(state, "timeout_queued", "deadline while waiting for tool")
            return
        await asyncio.sleep(delay)
        if state.terminal:
            return
        step = tasks[state.task_index].steps[state.next_step]
        state.history = state.history + step.input
        state.enqueued_at = state.runnable_at = loop.time()
        await scheduler.put(state)
        tl.mark(loop.time(), dq=+1, dready=+1, dtool=-1)

    async with new_session(limit=cfg.concurrency + 8) as session:
        async def arrivals():
            for i, off in enumerate(offsets):
                await asyncio.sleep(max(0.0, t0 + off - loop.time()))
                now = loop.time()
                state, rec = states[i], recs[i]
                rec.arrival = now
                state.history = list(tasks[i].steps[0].input)
                state.enqueued_at = state.runnable_at = now
                await scheduler.put(state)
                tl.mark(now, dq=+1, dready=+1)

        async def worker(w: int):
            while (state := await scheduler.take()) is not None:
                now = loop.time()
                rec = recs[state.task_index]
                rec.worker = w
                if rec.admit is None:
                    rec.admit = now
                wait_s = now - state.runnable_at
                rec.ready_queue_s += wait_s
                tl.mark(now, dq=-1, dready=-1)
                left = cfg.deadline_s - (now - rec.arrival)
                if left <= 0:
                    finish(state, "timeout_queued", "deadline while queued")
                    continue

                task = tasks[state.task_index]
                step_index = state.next_step
                step = task.steps[step_index]
                head_prompt_tokens = len(state.history)
                head_need_tokens = head_prompt_tokens + step.n_gen
                active_reserved_tokens = sum(active_needs.values())
                kv_budget_remaining_tokens = cfg.server.ctx - active_reserved_tokens
                head_fits = head_need_tokens <= kv_budget_remaining_tokens
                later_needs = []
                for candidate in scheduler.entries:
                    candidate_rec = recs[candidate.task_index]
                    if now - candidate_rec.arrival >= cfg.deadline_s:
                        continue
                    candidate_step = tasks[candidate.task_index].steps[candidate.next_step]
                    need = len(candidate.history) + candidate_step.n_gen
                    if need <= kv_budget_remaining_tokens:
                        later_needs.append(need)
                admission_check = {
                    "t": now - t0, "worker": w, "task_index": state.task_index,
                    "task_id": task.task_id, "step": step_index,
                    "queue_length_before": 1 + len(scheduler.entries),
                    "active_count": len(active_needs),
                    "active_reserved_tokens": active_reserved_tokens,
                    "kv_capacity_tokens": cfg.server.ctx,
                    "kv_budget_remaining_tokens": kv_budget_remaining_tokens,
                    "head_prompt_tokens": head_prompt_tokens,
                    "head_output_budget_tokens": step.n_gen,
                    "head_need_tokens": head_need_tokens,
                    "head_fits": head_fits,
                    "fitting_later_count": len(later_needs),
                    "smallest_fitting_later_need_tokens": min(later_needs) if later_needs else None,
                    "backfill_opportunity": not head_fits and bool(later_needs),
                    "request_outcome": None,
                }
                tl.admission_checks.append(admission_check)
                tl.mark(loop.time(), dr=+1)
                active_needs[w] = head_need_tokens
                try:
                    r = await asyncio.wait_for(
                        complete(session, srv.url, state.history, step.n_gen, cache_prompt=cfg.cache_prompt,
                                 return_tokens=True, timeout=cfg.request_timeout_s),
                        timeout=left,
                    )
                except asyncio.TimeoutError:
                    admission_check["request_outcome"] = "timeout_running"
                    tl.mark(loop.time(), dr=-1)
                    finish(state, "timeout_running", "deadline during request")
                    continue
                finally:
                    active_needs.pop(w, None)

                admission_check["request_outcome"] = "error" if r["error"] else "ok"
                r.update(task_id=task.task_id, task_index=state.task_index, cls=task.cls,
                         step=step_index, worker=w,
                         t_rel=r["t_send"] - t0, ready_queue_s=wait_s)
                requests.append(r)
                if r["error"]:
                    tl.mark(loop.time(), dr=-1)
                    finish(state, "failed", r["error"])
                    continue

                state.history = state.history + r["tokens"]
                rec.steps_done = step_index + 1
                state.next_step += 1
                if state.next_step == len(task.steps):
                    tl.mark(loop.time(), dr=-1)
                    finish(state, "ok")
                    continue

                delay = (step.tool_delay_s or 0.0) * cfg.delay_scale
                rec.tool_s += delay
                state.enqueued_at, state.runnable_at = 0.0, None
                tl.mark(loop.time(), dr=-1, dtool=+1)
                job = asyncio.create_task(wait_for_tool(state, delay))
                tool_jobs.append(job)

        arrival_job = asyncio.create_task(arrivals())
        workers = [asyncio.create_task(worker(w)) for w in range(cfg.concurrency)]
        await arrival_job
        await all_done.wait()
        if tool_jobs:
            await asyncio.gather(*tool_jobs)
        await scheduler.close()
        await asyncio.gather(*workers)
    return recs, requests, tl, t0


async def run(cfg: RunConfig, srv: Server) -> tuple:
    if cfg.scheduler not in {"task", "request"}:
        raise ValueError(f"unknown scheduler: {cfg.scheduler}")
    rng = np.random.default_rng(cfg.seed)
    pool = load(ROOT / cfg.trace)
    idx = rng.choice(len(pool), size=cfg.n_tasks, replace=cfg.n_tasks > len(pool))
    tasks = [pool[i] for i in idx]
    gaps = rng.exponential(1.0 / cfg.arrival_rate, size=len(tasks)) if cfg.arrival_rate > 0 else np.zeros(len(tasks))
    if len(gaps):
        gaps[0] = 0.0
    offsets = np.cumsum(gaps)
    if cfg.scheduler == "request":
        return await run_request_fifo(cfg, srv, tasks, offsets)
    return await run_task_fifo(cfg, srv, tasks, offsets)


def summarize(cfg: RunConfig, tasks: pd.DataFrame, reqs: pd.DataFrame, mem: pd.DataFrame,
              metrics: dict, admission_checks: list) -> dict:
    ok = tasks[tasks.status == "ok"]
    rok = reqs[reqs.error.isna()] if len(reqs) else reqs
    span = (tasks.finish.max() - tasks.arrival.min()) if len(tasks) else float("nan")
    q = lambda s, p: float(np.percentile(s, p)) if len(s) else float("nan")
    return {
        "name": cfg.name, "trace": Path(cfg.trace).stem, "scheduler": cfg.scheduler,
        "concurrency": cfg.concurrency, "ctx": cfg.server.ctx,
        "arrival_rate": cfg.arrival_rate, "delay_scale": cfg.delay_scale, "cache_prompt": cfg.cache_prompt,
        "cache_ram": cfg.server.cache_ram, "n_tasks": len(tasks),
        "ok": len(ok), "failed": int((tasks.status == "failed").sum()),
        "timeout_queued": int((tasks.status == "timeout_queued").sum()),
        "timeout_running": int((tasks.status == "timeout_running").sum()),
        "completion_p50_s": q(ok.completion_s, 50), "completion_p95_s": q(ok.completion_s, 95),
        "completion_p99_s": q(ok.completion_s, 99),
        "queue_p50_s": q(tasks.queue_s.dropna(), 50), "queue_p95_s": q(tasks.queue_s.dropna(), 95),
        "ready_queue_p50_s": q(tasks.ready_queue_s.dropna(), 50),
        "ready_queue_p95_s": q(tasks.ready_queue_s.dropna(), 95),
        "ttft_p50_s": q(rok.ttft_s.dropna(), 50), "ttft_p95_s": q(rok.ttft_s.dropna(), 95),
        "tpot_p50_ms": q(rok.tpot_ms.dropna(), 50),
        "throughput_tasks_min": len(ok) / span * 60 if span else float("nan"),
        "gen_tok_s": float(rok.predicted_n.sum() / span) if len(rok) and span else float("nan"),
        "cache_hit_ratio": float(rok.cache_n.sum() / rok.n_prompt.sum()) if len(rok) else float("nan"),
        "prefill_tokens_computed": int(rok.prompt_n.sum()) if len(rok) else 0,
        "requests": len(reqs), "requests_failed": int(reqs.error.notna().sum()) if len(reqs) else 0,
        "kv_context_errors": int(reqs.error.fillna("").str.contains(
            "Context size has been exceeded", case=False, regex=False).sum()) if len(reqs) else 0,
        "admission_decisions": len(admission_checks),
        "head_would_exceed_budget": sum(not row["head_fits"] for row in admission_checks),
        "backfill_opportunities": sum(row["backfill_opportunity"] for row in admission_checks),
        "peak_footprint_mib": float(mem.footprint_mib.max()) if len(mem) else float("nan"),
        "start_footprint_mib": float(mem.footprint_mib.iloc[0]) if len(mem) else float("nan"),
        "span_s": float(span),
        "srv_prompt_tokens_cached_total": metrics.get("prompt_tokens_cached_total"),
    }


def execute(cfg: RunConfig, group: str = "phase1") -> dict:
    if cfg.scheduler not in {"task", "request"}:
        raise ValueError(f"unknown scheduler: {cfg.scheduler}")
    out = RESULTS / group / cfg.name
    out.mkdir(parents=True, exist_ok=True)
    cfg.server.parallel = cfg.concurrency
    with GpuMemSampler() as gpu, Server(cfg.server, out / "server.log", sample_interval=0.1) as srv:
        recs, requests, tl, t0 = asyncio.run(run(cfg, srv))
        metrics = srv.metrics()
        mem = pd.DataFrame([{"t": s.t - t0, "footprint_mib": s.footprint / MiB, "rss_mib": s.rss / MiB}
                            for s in srv.mem.samples])

    tasks = pd.DataFrame([asdict(r) for r in recs])
    tasks["queue_s"] = tasks.admit - tasks.arrival
    tasks["completion_s"] = tasks.finish - tasks.arrival
    tasks["service_s"] = tasks.finish - tasks.admit
    for c in ("arrival", "admit", "finish"):
        tasks[c] = tasks[c] - t0
    reqs = pd.DataFrame(requests).drop(columns=["tokens"], errors="ignore")
    summary = summarize(cfg, tasks, reqs, mem, metrics, tl.admission_checks)
    gpu_rows = [{**s, "t": s["t"] - t0} for s in gpu.samples]
    summary.update({
        "gpu_peak_used_mib": max((s["used_mib"] for s in gpu_rows), default=None),
        "gpu_total_mib": gpu_rows[0]["total_mib"] if gpu_rows else None,
        "gpu_uuid": gpu_rows[0]["gpu_uuid"] if gpu_rows else None,
        "gpu_sample_count": len(gpu_rows),
        "gpu_sampling_interval_s": gpu.interval,
        "gpu_sampling_error": gpu.error or None,
    })

    tasks.to_csv(out / "tasks.csv", index=False)
    reqs.to_csv(out / "requests.csv", index=False)
    pd.DataFrame(tl.rows).to_csv(out / "timeline.csv", index=False)
    if cfg.scheduler == "request":
        pd.DataFrame(tl.admission_checks).reindex(columns=ADMISSION_CHECK_COLUMNS).to_csv(
            out / "admission_checks.csv", index=False)
    mem.to_csv(out / "memory.csv", index=False)
    pd.DataFrame(gpu_rows, columns=["t", "gpu_uuid", "used_mib", "total_mib"]).to_csv(
        out / "gpu_memory.csv", index=False)
    (out / "meta.json").write_text(json.dumps({"env": environment(), "run": asdict(cfg), "summary": summary,
                                               "server_args": " ".join(cfg.server.args()), "doc": __doc__},
                                              indent=2, default=str))
    print(f"[{cfg.name}] ok {summary['ok']}/{summary['n_tasks']} failed {summary['failed']} "
          f"timeouts {summary['timeout_queued']}+{summary['timeout_running']} | completion p50 {summary['completion_p50_s']:.1f}s "
          f"p95 {summary['completion_p95_s']:.1f}s | queue p95 {summary['queue_p95_s']:.1f}s | "
          f"{summary['throughput_tasks_min']:.1f} tasks/min | hit {summary['cache_hit_ratio']:.2f} | "
          f"peak {summary['peak_footprint_mib']:.0f} MiB")
    return summary


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--name", default="adhoc")
    p.add_argument("--trace", default="data/traces/synthetic.jsonl")
    p.add_argument("--n-tasks", type=int, default=100)
    p.add_argument("--rate", type=float, default=0.5)
    p.add_argument("--concurrency", type=int, default=8)
    p.add_argument("--ctx", type=int, default=16384)
    p.add_argument("--cache-ram", type=int, default=None)
    p.add_argument("--no-cache-prompt", action="store_true")
    p.add_argument("--delay-scale", type=float, default=1.0)
    p.add_argument("--deadline", type=float, default=240.0)
    p.add_argument("--scheduler", choices=("task", "request"), default="request")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--allow-battery", action="store_true")
    a = p.parse_args()
    check_power(a.allow_battery)
    cfg = RunConfig(name=a.name, trace=a.trace, n_tasks=a.n_tasks, arrival_rate=a.rate, concurrency=a.concurrency,
                    delay_scale=a.delay_scale, cache_prompt=not a.no_cache_prompt, deadline_s=a.deadline,
                    scheduler=a.scheduler, seed=a.seed,
                    server=ServerConfig(ctx=a.ctx, cache_ram=a.cache_ram, mmap=False))
    execute(cfg)


if __name__ == "__main__":
    main()
