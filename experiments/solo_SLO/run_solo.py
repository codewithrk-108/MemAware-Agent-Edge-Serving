"""Solo (zero-contention) task latency for AgentTrace replay tasks.

Each task runs alone on the GPU, one request at a time, so its latency has no queueing or batching interference.
The server uses the Phase 1 settings: Qwen3-0.6B Q8_0, f16 KV, unified 32K-cell pool (-kvu), prompt reuse on,
host prompt cache off (--cache-ram 0), no mmap, saved tool delays at scale 1, greedy decoding with ignore_eos.

Two KV modes, run interleaved on the same server so drift affects both equally:
  retain  the task's KV stays resident through every tool wait; the next step reuses the cached prefix.
  evict   the slot is erased right after every request, before the tool wait; the next step recomputes
          its whole prompt.
Every task-repetition starts from an empty KV pool (all slots erased), so repetitions are independent.

Order is repetition-major: all tasks of rep 0 (shuffled per rep with a fixed seed), then rep 1, and so on.
Each finished task-repetition is appended as one JSON line to <out>/<mode>/task_metadata.jsonl; a rerun skips
the ones already there, so the run can be stopped and resumed. Build the CSVs and per-task SLO table with
experiments/solo_SLO/analyze.py.
"""

import argparse
import asyncio
import json
import tempfile
import time
import urllib.request
from dataclasses import asdict
from pathlib import Path

import numpy as np

from harness.client import complete, new_session
from harness.kv_memory import kv_memory_sample
from harness.results import environment
from harness.server import Server, ServerConfig
from workloads.trace import load

ROOT = Path(__file__).resolve().parents[2]
MODES = ("retain", "evict")
SLOT = 0   # every request is pinned to one slot; the task is alone, so the slot choice does not matter


def erase_slot(url: str, slot: int) -> float:
    t = time.monotonic()
    req = urllib.request.Request(f"{url}/slots/{slot}?action=erase", data=b"{}",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        r.read()
    return (time.monotonic() - t) * 1e3


def erase_all(url: str, n_slots: int):
    for s in range(n_slots):
        erase_slot(url, s)


def kv_used(url: str):
    try:
        return kv_memory_sample(url)["used_cells"]
    except (OSError, ValueError):
        return None


async def run_task(session, url: str, task, mode: str, rep: int, request_timeout: float) -> dict:
    loop = asyncio.get_running_loop()
    history, requests, tools = [], [], []
    status, error = "ok", None
    t_start = loop.time()
    for k, step in enumerate(task.steps):
        history = history + step.input
        r = await complete(session, url, history, step.n_gen, cache_prompt=True,
                           return_tokens=True, id_slot=SLOT, timeout=request_timeout)
        tokens = r.pop("tokens", [])
        r.update(step=k, t_rel_s=r["t_send"] - t_start, kv_used_cells_after=kv_used(url))
        for key in ("t_send", "t_first", "t_last", "t_end"):
            r.pop(key, None)
        requests.append(r)
        if r["error"]:
            status, error = "failed", r["error"]
            break
        history = history + tokens
        if step.tool_delay_s is None:
            break
        erase_ms = None
        if mode == "evict":
            erase_ms = erase_slot(url, SLOT)
        kv_during_tool = kv_used(url)
        t = loop.time()
        await asyncio.sleep(step.tool_delay_s)
        tools.append({"after_step": k, "planned_s": step.tool_delay_s, "actual_s": loop.time() - t,
                      "erase_ms": erase_ms, "kv_used_cells_during_wait": kv_during_tool})
    latency = loop.time() - t_start
    return {
        "task_id": task.task_id, "cls": task.cls, "mode": mode, "rep": rep, "status": status, "error": error,
        "n_steps": task.n_steps, "steps_done": sum(r["error"] is None for r in requests),
        "latency_s": latency,
        "model_s": sum(r["e2e_s"] for r in requests),
        "tool_s": sum(t["actual_s"] for t in tools),
        "prefill_tokens_computed": sum(r.get("prompt_n") or 0 for r in requests),
        "prefill_tokens_reused": sum(r.get("cache_n") or 0 for r in requests),
        "output_tokens": sum(r.get("predicted_n") or 0 for r in requests),
        "peak_kv_used_cells": max((r["kv_used_cells_after"] or 0 for r in requests), default=0),
        "final_context_tokens": len(history),
        "wall_clock_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "requests": requests, "tool_waits": tools,
    }


def done_keys(path: Path) -> set:
    keys = set()
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                d = json.loads(line)
            except json.JSONDecodeError:   # a line cut off by a kill; that task-rep is rerun
                continue
            keys.add((d["task_id"], d["rep"]))
    return keys


async def replay(args, srv: Server, tasks: list, out: Path):
    files = {m: out / m / "task_metadata.jsonl" for m in args.modes}
    done = {m: done_keys(f) for m, f in files.items()}
    total = len(tasks) * args.reps * len(args.modes)
    n_done = sum(len(v) for v in done.values())
    print(f"{n_done}/{total} task-reps already done", flush=True)
    async with new_session(limit=4) as session:
        # warm-up request, not recorded
        await complete(session, srv.url, list(tasks[0].steps[0].input), 16, id_slot=SLOT)
        for rep in range(args.reps):
            order = np.random.default_rng(args.seed + rep).permutation(len(tasks))
            for i in order:
                task = tasks[i]
                # alternate which mode goes first so neither is always second
                modes = args.modes if (rep + i) % 2 == 0 else args.modes[::-1]
                for mode in modes:
                    if (task.task_id, rep) in done[mode]:
                        continue
                    erase_all(srv.url, args.parallel)
                    rec = await run_task(session, srv.url, task, mode, rep, args.request_timeout)
                    with files[mode].open("a") as f:
                        f.write(json.dumps(rec, default=str) + "\n")
                    n_done += 1
                    print(f"[{n_done}/{total}] rep {rep} {mode:6s} {task.task_id:40s} {rec['status']:6s} "
                          f"{rec['latency_s']:8.1f}s steps {rec['steps_done']}/{rec['n_steps']} "
                          f"prefill {rec['prefill_tokens_computed']}", flush=True)
    erase_all(srv.url, args.parallel)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--name", default="smoke50_v1")
    p.add_argument("--trace", default="data/traces/agenttrace_50_seed2.jsonl")
    p.add_argument("--source-model", default="all",
                   help="keep only tasks whose recorded trajectory came from this model (task_id suffix); 'all' keeps every task")
    p.add_argument("--n-tasks", type=int, default=0, help="use only the first N kept tasks of the trace (0 = all)")
    p.add_argument("--reps", type=int, default=3)
    p.add_argument("--modes", nargs="+", choices=MODES, default=list(MODES))
    p.add_argument("--ctx", type=int, default=32768)
    p.add_argument("--parallel", type=int, default=4, help="server slots (-np); 4 matches the N4 FIFO baseline")
    p.add_argument("--request-timeout", type=float, default=1800)
    p.add_argument("--seed", type=int, default=2)
    p.add_argument("--port", type=int, default=8090)
    args = p.parse_args()
    args.modes = tuple(args.modes)

    tasks = load(ROOT / args.trace)
    if args.source_model != "all":
        tasks = [t for t in tasks if t.task_id.endswith("-" + args.source_model)]
    if args.n_tasks:
        tasks = tasks[:args.n_tasks]
    out = ROOT / "results" / "solo_SLO" / args.name
    for m in args.modes:
        (out / m).mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="solo-slots-") as slot_dir:
        # --slot-save-path only enables the erase endpoint; nothing is saved to it
        cfg = ServerConfig(ctx=args.ctx, parallel=args.parallel, kv_unified=True, cache_ram=0, mmap=False,
                           port=args.port, extra=["--slot-save-path", slot_dir])
        with Server(cfg, out / "server.log", sample_interval=1.0) as srv:
            kv_memory_sample(srv.url)   # fail early if the runtime lacks the KV-cell metrics patch
            meta = {"env": environment(), "args": vars(args), "server": asdict(cfg),
                    "server_args": " ".join(cfg.args()), "n_tasks": len(tasks),
                    "task_ids": [t.task_id for t in tasks],
                    "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "doc": __doc__}
            (out / f"meta_{time.strftime('%Y%m%dT%H%M%S')}.json").write_text(json.dumps(meta, indent=2, default=str))
            asyncio.run(replay(args, srv, tasks, out))
    print("done", flush=True)


if __name__ == "__main__":
    main()
