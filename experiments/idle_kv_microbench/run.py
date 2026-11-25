"""Microbenchmark: does idle KV in llama.cpp's unified pool slow down decoding of other requests?

Motivation: across the 50-task smoke runs, time per output token rose with mean KV occupancy
(baseline 50% -> 16.3 ms, experiment 2 57% -> 19.8 ms, experiment 3 80% -> 23.0 ms). If idle cells slow attention
for everyone, idle-KV management is about speed, not only memory.

For each configuration:
  1. erase all 16 slots (empty pool)
  2. place X idle cells: slots 4..15 each prefill up to 4,096 random tokens (n_predict 1) and then sit idle
  3. B active requests (slots 0..B-1), each with a P-token random prompt, decode 256 tokens concurrently
     (greedy, ignore_eos); record time per output token (client-side, stream inter-token) and the server's own
Order of steps 2 and 3:
  idle_first    idle cells are written first, so the active requests' cells sit after them in the pool
  active_first  the active prompts are prefilled first (n_predict 1), then the idle cells are written, then the active
                requests decode (their prompt is reused from cache), so idle cells sit after the active prompts
Comparing the two separates "how many cells are occupied" from "where they sit".

Server: Phase 1 settings (Qwen3-0.6B Q8_0, f16 KV, -c 32768 -kvu, --cache-ram 0, no mmap) with -np 16, as in
experiments 1-3. Configurations whose total cells would exceed 31,744 (32K minus a 1,024-cell margin) are skipped.
Each configuration is repeated --reps times; configurations run in a seeded random order.
"""

import argparse
import asyncio
import itertools
import json
import tempfile
import time
from pathlib import Path

import numpy as np
import pandas as pd

from harness.client import complete, new_session, random_tokens
from harness.kv_memory import kv_memory_sample
from harness.results import environment
from harness.server import Server, ServerConfig

HERE = Path(__file__).resolve().parent
SLOTS = 16
IDLE_SLOTS = range(4, SLOTS)
IDLE_PER_SLOT = 4096


async def erase_all(session, url):
    for s in range(SLOTS):
        async with session.post(f"{url}/slots/{s}?action=erase", json={}) as r:
            await r.read()


async def fill_idle(session, url, X, rng):
    left, placed = X, 0
    for s in IDLE_SLOTS:
        if left <= 0:
            break
        n = min(IDLE_PER_SLOT, left)
        r = await complete(session, url, random_tokens(rng, n), 1, id_slot=s, timeout=600)
        if r["error"]:
            raise RuntimeError(f"idle fill slot {s}: {r['error']}")
        left -= n
        placed += n
    return placed


async def measure(url, order, B, P, X, gen, rng) -> dict:
    async with new_session(limit=32) as session:
        await erase_all(session, url)
        prompts = [random_tokens(rng, P) for _ in range(B)]
        if order == "active_first":
            await asyncio.gather(*[complete(session, url, p, 1, id_slot=b, timeout=600) for b, p in enumerate(prompts)])
            await fill_idle(session, url, X, rng)
        else:
            await fill_idle(session, url, X, rng)
        kv_before = kv_memory_sample(url)["used_cells"]
        t = time.monotonic()
        rs = await asyncio.gather(*[complete(session, url, p, gen, id_slot=b, timeout=600)
                                    for b, p in enumerate(prompts)])
        wall = time.monotonic() - t
        kv_after = kv_memory_sample(url)["used_cells"]
        await erase_all(session, url)
    errs = [r["error"] for r in rs if r["error"]]
    return {"order": order, "B": B, "P": P, "X": X, "gen": gen,
            "kv_used_before": kv_before, "kv_used_after": kv_after,
            "tpot_ms": float(np.mean([r["tpot_ms"] for r in rs if r["tpot_ms"]])) if not errs else None,
            "srv_tpot_ms": float(np.mean([r["srv_tpot_ms"] for r in rs if r.get("srv_tpot_ms")])) if not errs else None,
            "ttft_ms": float(np.mean([r["ttft_s"] for r in rs if r["ttft_s"]])) * 1e3 if not errs else None,
            "prefill_tokens": int(sum(r.get("prompt_n") or 0 for r in rs)),
            "agg_tok_s": B * gen / wall if not errs else None, "wall_s": wall,
            "error": errs[0] if errs else None}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--reps", type=int, default=3)
    p.add_argument("--gen", type=int, default=256)
    p.add_argument("--seed", type=int, default=2)
    p.add_argument("--port", type=int, default=8096)
    p.add_argument("--out", default=str(HERE / "data"))
    a = p.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    grid = [c for c in itertools.product(("idle_first", "active_first"), (1, 4), (1024, 4096),
                                         (0, 4096, 8192, 16384, 24576))
            if c[3] + c[1] * (c[2] + a.gen) <= 32768 - 1024]
    jobs = [(c, r) for r in range(a.reps) for c in grid]
    rng = np.random.default_rng(a.seed)
    order = rng.permutation(len(jobs))
    print(f"{len(grid)} configurations x {a.reps} reps = {len(jobs)} measurements", flush=True)
    with tempfile.TemporaryDirectory(prefix="idlekv-") as d:
        cfg = ServerConfig(ctx=32768, parallel=SLOTS, kv_unified=True, cache_ram=0, mmap=False, port=a.port,
                           extra=["--slot-save-path", d])
        rows = []
        with Server(cfg, out / "server.log", sample_interval=1.0) as srv:
            kv_memory_sample(srv.url)
            asyncio.run(measure(srv.url, "idle_first", 4, 1024, 4096, 64, rng))   # warm-up, not recorded
            for n, j in enumerate(order, 1):
                (o, B, P, X), rep = jobs[j]
                row = {"rep": rep, "seq": n, **asyncio.run(measure(srv.url, o, B, P, X, a.gen, rng))}
                rows.append(row)
                print(f"[{n}/{len(jobs)}] {o:12s} B{B} P{P:5d} X{X:6d} kv {row['kv_used_before']:6d} "
                      f"tpot {row['tpot_ms'] or float('nan'):6.2f} ms srv {row['srv_tpot_ms'] or float('nan'):6.2f} "
                      f"{row['error'] or ''}", flush=True)
                pd.DataFrame(rows).to_csv(out / "measurements.csv", index=False)
    (out / "meta.json").write_text(json.dumps({"env": environment(), "args": vars(a),
                                               "server_args": " ".join(cfg.args()), "doc": __doc__}, indent=2))
    print("done", flush=True)


if __name__ == "__main__":
    main()
