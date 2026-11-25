"""Follow-up: is the decode slowdown set by the NUMBER of occupied KV cells or by the occupied RANGE?

Setup (one active request, slot 0, 1,024-token prompt, 256 tokens decoded; -np 16, unified 32K pool):
  the active prompt is prefilled first (cells at the start of the pool), then 6 idle slots (4..9) of 4,096 tokens
  each are written after it, so the pool holds cells [0, ~25.6K). Then, before the active request decodes:
    full        nothing erased                          count ~25.6K, range ~25.6K
    none        all 6 idle slots erased                 count ~1K,    range ~1K   (control)
    keep_top    erase slots 4..8, keep slot 9 (highest) count ~5.1K,  range ~25.6K
    drop_top    erase only slot 9 (highest)             count ~21.5K, range ~21.5K
    keep_low    erase slots 5..9, keep slot 4 (lowest)  count ~5.1K,  range ~5.1K
If keep_top is as slow as full, the slowdown follows the range (the highest occupied cell); if it is as fast as
keep_low, it follows the count. Idle slots are assumed to be placed in write order; kv_used_cells is recorded.
"""

import asyncio
import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from harness.client import complete, new_session, random_tokens
from harness.kv_memory import kv_memory_sample
from harness.server import Server, ServerConfig

HERE = Path(__file__).resolve().parent
ERASE = {"full": [], "none": [4, 5, 6, 7, 8, 9], "keep_top": [4, 5, 6, 7, 8], "drop_top": [9], "keep_low": [5, 6, 7, 8, 9]}


async def one(url, case, rng):
    async with new_session(limit=8) as s:
        async def erase(slot):
            async with s.post(f"{url}/slots/{slot}?action=erase", json={}) as r:
                await r.read()
        for slot in range(16):
            await erase(slot)
        prompt = random_tokens(rng, 1024)
        await complete(s, url, prompt, 1, id_slot=0)
        for slot in range(4, 10):
            await complete(s, url, random_tokens(rng, 4096), 1, id_slot=slot)
        for slot in ERASE[case]:
            await erase(slot)
        kv = kv_memory_sample(url)["used_cells"]
        r = await complete(s, url, prompt, 256, id_slot=0)
        for slot in range(16):
            await erase(slot)
    return {"case": case, "kv_used_before": kv, "tpot_ms": r["tpot_ms"], "prefill_tokens": r.get("prompt_n"),
            "error": r["error"]}


def main():
    out = HERE / "data"
    rng = np.random.default_rng(3)
    jobs = [c for _ in range(3) for c in ERASE]
    jobs = [jobs[i] for i in rng.permutation(len(jobs))]
    rows = []
    with tempfile.TemporaryDirectory() as d:
        cfg = ServerConfig(ctx=32768, parallel=16, kv_unified=True, cache_ram=0, mmap=False, port=8097,
                           extra=["--slot-save-path", d])
        with Server(cfg, out / "server_range.log", sample_interval=1.0) as srv:
            asyncio.run(one(srv.url, "full", rng))   # warm-up, not recorded
            for n, c in enumerate(jobs, 1):
                row = asyncio.run(one(srv.url, c, rng))
                rows.append(row)
                print(f"[{n}/{len(jobs)}] {c:9s} kv {row['kv_used_before']:6d} tpot {row['tpot_ms']:.2f} ms "
                      f"prefill {row['prefill_tokens']} {row['error'] or ''}", flush=True)
    pd.DataFrame(rows).to_csv(out / "range_measurements.csv", index=False)
    print(pd.DataFrame(rows).groupby("case")[["kv_used_before", "tpot_ms"]].median().round(2).to_string())


if __name__ == "__main__":
    main()
