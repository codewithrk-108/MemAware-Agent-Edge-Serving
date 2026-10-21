"""Phase 1 baseline: FIFO queue with fixed concurrency in front of llama-server.

Tasks arrive open-loop (Poisson) and wait in one FIFO queue. N workers each take the oldest waiting task and run it
to completion: every step sends the full history (the model's own tokens included) and then waits for the tool,
holding its worker - an admitted task keeps its concurrency slot through its idle periods. llama-server gets N
slots, so the only queue that decides admission is this one.

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

from harness import platform
from harness.client import complete, new_session
from harness.memory import MiB
from harness.results import check_power, environment, results_dir
from harness.server import Server, ServerConfig
from workloads.trace import load

ROOT = Path(__file__).resolve().parents[1]


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
    worker: int = -1


class Timeline:
    """Queue length and running tasks over time, one row per change."""

    def __init__(self, t0: float):
        self.t0, self.rows, self.queued, self.running = t0, [], 0, 0

    def mark(self, now: float, dq: int = 0, dr: int = 0):
        self.queued += dq
        self.running += dr
        self.rows.append({"t": now - self.t0, "queued": self.queued, "running": self.running})


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


async def run(cfg: RunConfig, srv: Server) -> tuple:
    rng = np.random.default_rng(cfg.seed)
    pool = load(ROOT / cfg.trace)
    idx = rng.choice(len(pool), size=cfg.n_tasks, replace=cfg.n_tasks > len(pool))
    tasks = [pool[i] for i in idx]
    gaps = rng.exponential(1.0 / cfg.arrival_rate, size=len(tasks)) if cfg.arrival_rate > 0 else np.zeros(len(tasks))
    gaps[0] = 0.0
    offsets = np.cumsum(gaps)

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
                tl.mark(loop.time(), dq=+1)
            for _ in range(cfg.concurrency):
                queue.put_nowait(None)   # sentinels come after every task, FIFO keeps that order

        async def worker(w: int):
            while (i := await queue.get()) is not None:
                rec = recs[i]
                rec.admit, rec.worker = loop.time(), w
                tl.mark(rec.admit, dq=-1)
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


def summarize(cfg: RunConfig, tasks: pd.DataFrame, reqs: pd.DataFrame, mem: pd.DataFrame, metrics: dict) -> dict:
    ok = tasks[tasks.status == "ok"]
    rok = reqs[reqs.error.isna()] if len(reqs) else reqs
    span = (tasks.finish.max() - tasks.arrival.min()) if len(tasks) else float("nan")
    q = lambda s, p: float(np.percentile(s, p)) if len(s) else float("nan")
    return {
        "name": cfg.name, "trace": Path(cfg.trace).stem, "concurrency": cfg.concurrency, "ctx": cfg.server.ctx,
        "arrival_rate": cfg.arrival_rate, "delay_scale": cfg.delay_scale, "cache_prompt": cfg.cache_prompt,
        "cache_ram": cfg.server.cache_ram, "n_tasks": len(tasks),
        "ok": len(ok), "failed": int((tasks.status == "failed").sum()),
        "timeout_queued": int((tasks.status == "timeout_queued").sum()),
        "timeout_running": int((tasks.status == "timeout_running").sum()),
        "completion_p50_s": q(ok.completion_s, 50), "completion_p95_s": q(ok.completion_s, 95),
        "completion_p99_s": q(ok.completion_s, 99),
        "queue_p50_s": q(tasks.queue_s.dropna(), 50), "queue_p95_s": q(tasks.queue_s.dropna(), 95),
        "ttft_p50_s": q(rok.ttft_s.dropna(), 50), "ttft_p95_s": q(rok.ttft_s.dropna(), 95),
        "tpot_p50_ms": q(rok.tpot_ms.dropna(), 50),
        "throughput_tasks_min": len(ok) / span * 60 if span else float("nan"),
        "gen_tok_s": float(rok.predicted_n.sum() / span) if len(rok) and span else float("nan"),
        "cache_hit_ratio": float(rok.cache_n.sum() / rok.n_prompt.sum()) if len(rok) else float("nan"),
        "prefill_tokens_computed": int(rok.prompt_n.sum()) if len(rok) else 0,
        "requests": len(reqs), "requests_failed": int(reqs.error.notna().sum()) if len(reqs) else 0,
        "peak_footprint_mib": float(mem.footprint_mib.max()) if len(mem) else float("nan"),
        "peak_host_mib": float(mem.rss_mib.max()) if len(mem) else float("nan"),
        "peak_gpu_mib": float(mem.gpu_mib.max()) if len(mem) else float("nan"),
        "start_footprint_mib": float(mem.footprint_mib.iloc[0]) if len(mem) else float("nan"),
        "span_s": float(span),
        "srv_prompt_tokens_cached_total": metrics.get("prompt_tokens_cached_total"),
    }


def execute(cfg: RunConfig, group: str = "phase1") -> dict:
    out = results_dir() / group / cfg.name
    out.mkdir(parents=True, exist_ok=True)
    cfg.server.parallel = cfg.concurrency
    with Server(cfg.server, out / "server.log", sample_interval=0.1) as srv:
        recs, requests, tl, t0 = asyncio.run(run(cfg, srv))
        metrics = srv.metrics()
        mem = pd.DataFrame([{"t": s.t - t0, "footprint_mib": s.footprint / MiB, "rss_mib": s.rss / MiB,
                             "gpu_mib": s.gpu / MiB} for s in srv.mem.samples])

    tasks = pd.DataFrame([asdict(r) for r in recs])
    tasks["queue_s"] = tasks.admit - tasks.arrival
    tasks["completion_s"] = tasks.finish - tasks.arrival
    tasks["service_s"] = tasks.finish - tasks.admit
    for c in ("arrival", "admit", "finish"):
        tasks[c] = tasks[c] - t0
    reqs = pd.DataFrame(requests).drop(columns=["tokens"], errors="ignore")
    summary = summarize(cfg, tasks, reqs, mem, metrics)

    tasks.to_csv(out / "tasks.csv", index=False)
    reqs.to_csv(out / "requests.csv", index=False)
    pd.DataFrame(tl.rows).to_csv(out / "timeline.csv", index=False)
    mem.to_csv(out / "memory.csv", index=False)
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
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--allow-battery", action="store_true")
    platform.add_argument(p)
    a = p.parse_args()
    platform.apply(a)
    check_power(a.allow_battery)
    cfg = RunConfig(name=a.name, trace=a.trace, n_tasks=a.n_tasks, arrival_rate=a.rate, concurrency=a.concurrency,
                    delay_scale=a.delay_scale, cache_prompt=not a.no_cache_prompt, deadline_s=a.deadline, seed=a.seed,
                    server=ServerConfig(ctx=a.ctx, cache_ram=a.cache_ram, mmap=False))
    execute(cfg)


if __name__ == "__main__":
    main()
