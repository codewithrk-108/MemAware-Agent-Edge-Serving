"""Measure KV swap-out, swap-in and recompute times under FIFO N4 load.

Workload: every model request of the 50-task smoke trace (data/traces/agenttrace_50_seed2.jsonl), 113 requests,
treated as independent jobs. Tool waits and task structure are ignored. Request j has
  L_prompt = full prompt length before that request (task inputs + earlier outputs)
  n_gen    = its output budget
and its prompt is L_prompt random ordinary-vocabulary tokens, so no request reuses another's cache and every
prefill is a full recompute (content does not change timing).

Scheduler: one FIFO queue, 4 workers (N4), all requests queued at t = 0 in a seeded shuffled order per repetition.
Worker w always uses server slot w. For each request:
  1. erase slot w                                   (start empty: full prefill)
  2. /completion, n_gen tokens, ignore_eos          -> recompute time   = server prompt_ms for L_prompt tokens
  3. POST /slots/w?action=save                      -> GPU->CPU time    = server save_ms for n_saved tokens
  4. erase slot w
  5. POST /slots/w?action=restore                   -> CPU->GPU time    = server restore_ms for n_restored tokens
  6. verify: resend the prompt + its output with n_predict 1; the restored cache must be reused
  7. erase slot w
Steps 2-6 run while the other three workers keep decoding, so every time includes N4 contention.

Server: the Phase 1 N4 configuration (Qwen3-0.6B Q8_0, f16 KV, -c 32768 -np 4 -kvu, --cache-ram 0, no mmap),
plus --slot-save-path on /dev/shm (RAM-backed tmpfs), so save/restore copies KV between GPU and host memory
through llama.cpp's own sequence-state path without touching disk.
"""

import argparse
import asyncio
import json
import shutil
import tempfile
import time
from pathlib import Path

import numpy as np
import pandas as pd

from harness.client import complete, new_session, random_tokens
from harness.kv_memory import kv_memory_sample
from harness.results import environment
from harness.server import Server, ServerConfig
from workloads.trace import load

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent


def trace_requests(trace: Path) -> pd.DataFrame:
    rows = []
    for task in load(trace):
        ctx = 0
        for k, step in enumerate(task.steps):
            ctx += len(step.input)
            rows.append({"task_id": task.task_id, "step": k, "L_prompt": ctx, "n_gen": step.n_gen})
            ctx += step.n_gen
    return pd.DataFrame(rows)


async def slot_action(session, url: str, slot: int, action: str, body: dict = None) -> tuple:
    t = time.monotonic()
    async with session.post(f"{url}/slots/{slot}?action={action}", json=body or {}) as r:
        out = await r.json(content_type=None)
        if r.status != 200:
            raise RuntimeError(f"{action} slot {slot}: {r.status} {out}")
    return out, (time.monotonic() - t) * 1e3


async def run_rep(url: str, reqs: pd.DataFrame, rep: int, workers: int, seed: int, slot_dir: Path) -> list:
    rng = np.random.default_rng(seed + rep)
    queue: asyncio.Queue = asyncio.Queue()
    for j in rng.permutation(len(reqs)):
        queue.put_nowait(int(j))
    rows, loop = [], asyncio.get_running_loop()
    t0 = loop.time()

    async with new_session(limit=4 * workers) as session:
        async def worker(w: int):
            wrng = np.random.default_rng([seed, rep, w])
            while not queue.empty():
                j = queue.get_nowait()
                req = reqs.iloc[j]
                row = {"rep": rep, "worker": w, "request_index": j, "task_id": req.task_id, "step": int(req.step),
                       "L_prompt": int(req.L_prompt), "n_gen": int(req.n_gen), "t_start": loop.time() - t0}
                await slot_action(session, url, w, "erase")
                prompt = random_tokens(wrng, int(req.L_prompt))
                r = await complete(session, url, prompt, int(req.n_gen), cache_prompt=True,
                                   return_tokens=True, id_slot=w, timeout=1800)
                tokens = r.pop("tokens", [])
                row.update(error=r["error"], prefill_ms=r.get("prompt_ms"), prefill_tokens=r.get("prompt_n"),
                           prefill_cached=r.get("cache_n"), ttft_ms=(r["ttft_s"] or 0) * 1e3,
                           tpot_ms=r["tpot_ms"], request_s=r["e2e_s"])
                if r["error"] is None:
                    fname = f"r{rep}_w{w}_j{j}.bin"
                    try:
                        save, save_client = await slot_action(session, url, w, "save", {"filename": fname})
                        await slot_action(session, url, w, "erase")
                        rest, rest_client = await slot_action(session, url, w, "restore", {"filename": fname})
                        row.update(save_ms=save["timings"]["save_ms"], save_client_ms=save_client,
                                   saved_tokens=save["n_saved"], saved_bytes=save["n_written"],
                                   restore_ms=rest["timings"]["restore_ms"], restore_client_ms=rest_client,
                                   restored_tokens=rest["n_restored"], restored_bytes=rest["n_read"])
                        v = await complete(session, url, prompt + tokens, 1, cache_prompt=True, id_slot=w, timeout=600)
                        row.update(verify_cache_n=v.get("cache_n"), verify_prompt_n=v.get("prompt_n"),
                                   verify_error=v["error"])
                    except Exception as e:   # e.g. no room in the pool to restore; recorded, not fatal
                        row["swap_error"] = f"{type(e).__name__}: {e}"[:300]
                    finally:
                        (slot_dir / fname).unlink(missing_ok=True)
                await slot_action(session, url, w, "erase")
                row["t_end"] = loop.time() - t0
                rows.append(row)
                print(f"rep {rep} w{w} L {row['L_prompt']:6d} gen {row['n_gen']:5d} "
                      f"prefill {row.get('prefill_ms') or float('nan'):8.1f}ms "
                      f"save {row.get('save_ms', float('nan')):7.1f}ms restore {row.get('restore_ms', float('nan')):7.1f}ms "
                      f"reuse {row.get('verify_cache_n')} {row['error'] or row.get('swap_error') or ''}", flush=True)

        await asyncio.gather(*[worker(w) for w in range(workers)])
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--trace", default="data/traces/agenttrace_50_seed2.jsonl")
    p.add_argument("--reps", type=int, default=3)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--ctx", type=int, default=32768)
    p.add_argument("--seed", type=int, default=2)
    p.add_argument("--port", type=int, default=8091)
    p.add_argument("--out", default=str(HERE / "data"))
    a = p.parse_args()

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    reqs = trace_requests(ROOT / a.trace)
    reqs.to_csv(out / "trace_requests.csv", index=False)
    print(f"{len(reqs)} requests x {a.reps} reps, N{a.workers}", flush=True)

    slot_dir = Path(tempfile.mkdtemp(prefix="kvswap-", dir="/dev/shm"))
    cfg = ServerConfig(ctx=a.ctx, parallel=a.workers, kv_unified=True, cache_ram=0, mmap=False, port=a.port,
                       extra=["--slot-save-path", str(slot_dir)])
    rows = []
    try:
        with Server(cfg, out / "server.log", sample_interval=1.0) as srv:
            kv_memory_sample(srv.url)   # fail early without the instrumented runtime
            warm = pd.DataFrame([{"task_id": "warmup", "step": 0, "L_prompt": L, "n_gen": 16}
                                 for L in (512, 2048, 4096, 1024)])
            asyncio.run(run_rep(srv.url, warm, 999, a.workers, a.seed, slot_dir))   # warm-up, not recorded
            for rep in range(a.reps):
                rows += asyncio.run(run_rep(srv.url, reqs, rep, a.workers, a.seed, slot_dir))
                pd.DataFrame(rows).to_csv(out / "measurements.csv", index=False)
    finally:
        shutil.rmtree(slot_dir, ignore_errors=True)
    (out / "meta.json").write_text(json.dumps({
        "env": environment(), "args": vars(a), "server_args": " ".join(cfg.args()),
        "slot_save_path": "tmpfs under /dev/shm (host RAM)", "doc": __doc__}, indent=2, default=str))
    print("wrote", out / "measurements.csv", flush=True)


if __name__ == "__main__":
    main()
