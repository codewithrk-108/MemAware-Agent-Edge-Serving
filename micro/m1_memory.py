"""M1: static memory cost of the runtime.

For each server configuration: the allocations the runtime reports at startup, the process memory when idle,
and the process memory after one request has filled the whole context. Answers whether KV memory is paid at
startup (sized by -c) or as the context fills, and what slots and mmap cost.
"""

import asyncio

import numpy as np

from harness.client import complete, new_session, random_tokens
from harness.results import save
from harness.server import Server, ServerConfig, ROOT
from micro.common import PORT, parse_args, settle

N_PREDICT = 8


async def fill(srv: Server, n_ctx_slot: int) -> dict:
    rng = np.random.default_rng(1)
    async with new_session() as s:
        return await complete(s, srv.url, random_tokens(rng, n_ctx_slot - N_PREDICT - 8), N_PREDICT, cache_prompt=False)


def run_config(name: str, cfg: ServerConfig) -> dict:
    cfg.port, cfg.verbose = PORT, True
    log = ROOT / "results/micro/m1_memory/logs" / f"{name}.log"
    with Server(cfg, log) as srv:
        asyncio.run(settle(1.0))
        idle = srv.memory_now()
        n_ctx_slot = cfg.ctx if cfg.kv_unified else cfg.ctx // cfg.parallel
        r = asyncio.run(fill(srv, n_ctx_slot))
        asyncio.run(settle(1.0))
        filled = srv.memory_now()
        row = {
            "name": name, "ctx": cfg.ctx, "parallel": cfg.parallel, "kv_unified": cfg.kv_unified, "mmap": cfg.mmap,
            **srv.breakdown(),
            "idle_footprint_mib": idle["footprint_mib"], "idle_rss_mib": idle["rss_mib"],
            "filled_footprint_mib": filled["footprint_mib"], "filled_rss_mib": filled["rss_mib"],
            "peak_footprint_mib": filled["peak_footprint_mib"],
            "fill_tokens": r["n_prompt"], "fill_prefill_ms": r.get("prompt_ms"), "error": r["error"],
        }
    print(f"{name:>16}: kv {row.get('kv_mib', 0):7.0f} MiB | footprint idle {row['idle_footprint_mib']:7.0f} "
          f"-> filled {row['filled_footprint_mib']:7.0f} MiB | rss idle {row['idle_rss_mib']:7.0f} -> {row['filled_rss_mib']:7.0f} MiB"
          + (f" | ERROR {r['error']}" if r["error"] else ""))
    return row


def main():
    args = parse_args(__doc__)
    ctxs = [2048, 8192, 32768] if args.quick else [2048, 4096, 8192, 16384, 32768, 65536]
    configs = [(f"ctx{c}", ServerConfig(ctx=c, parallel=1)) for c in ctxs]
    configs += [(f"np{n}_ctx16384", ServerConfig(ctx=16384, parallel=n)) for n in ([8] if args.quick else [4, 16, 32])]
    configs += [("nommap_ctx8192", ServerConfig(ctx=8192, parallel=1, mmap=False))]
    rows = [run_config(name, cfg) for name, cfg in configs]
    save("micro", "m1_memory", rows, {"description": __doc__, "n_predict": N_PREDICT})


if __name__ == "__main__":
    main()
