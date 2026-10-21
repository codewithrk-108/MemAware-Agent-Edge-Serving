"""M6: what happens when concurrent requests need more KV memory than the pool has.

B requests of L prompt tokens + 256 generated tokens start together on an 8192-cell pool with 8 slots.
Unified: all slots share the pool, so requests fit individually but can exhaust it together.
Partitioned: every slot owns 1024 cells, so long requests are rejected even when the pool is mostly empty.
Records success, the error the runtime returns, and latency, so failure modes are observed and not assumed.
"""

import asyncio

import numpy as np

from harness.client import complete, new_session, random_tokens
from harness.results import save
from harness.server import Server, ServerConfig, ROOT
from micro.common import PORT, now, parse_args

CTX = 8192
SLOTS = 8
N_PREDICT = 256
TIMEOUT_S = 180


def classify(err) -> str:
    if not err:
        return "ok"
    e = err.lower()
    if "exceed_context_size" in e or "exceeds the available context" in e:
        return "rejected_context"
    if "context size has been exceeded" in e or "short response" in e:
        return "truncated"
    if "timeout" in e:
        return "timeout"
    return "other"


async def burst(srv: Server, rng, b: int, prompt_len: int) -> list:
    async with new_session() as s:
        t0 = now()
        rs = await asyncio.gather(*[complete(s, srv.url, random_tokens(rng, prompt_len), N_PREDICT,
                                             cache_prompt=False, timeout=TIMEOUT_S) for _ in range(b)])
        t1 = now()
    m = srv.metrics()
    for r in rs:
        r.update(concurrency=b, prompt_len=prompt_len, demand_tokens=b * (prompt_len + N_PREDICT), wall_s=t1 - t0,
                 outcome=classify(r["error"]), peak_footprint_mib=srv.mem.peak(t0, t1) / 2**20,
                 srv_n_tokens_max=m.get("n_tokens_max"))
    return rs


def run(mode: str, lengths: list, bs: list) -> list:
    cfg = ServerConfig(ctx=CTX, parallel=SLOTS, kv_unified=(mode == "unified"), cache_ram=0, port=PORT)
    rows = []
    with Server(cfg, ROOT / f"results/micro/m6_capacity/logs/{mode}.log") as srv:
        rng = np.random.default_rng(6)
        for L in lengths:
            for b in bs:
                rs = asyncio.run(burst(srv, rng, b, L))
                outcomes = {}
                for r in rs:
                    outcomes[r["outcome"]] = outcomes.get(r["outcome"], 0) + 1
                    r["mode"] = mode
                ok = [r for r in rs if r["outcome"] == "ok"]
                e2e = f"{np.median([r['e2e_s'] for r in ok]):6.2f} s" if ok else "     - "
                print(f"{mode:>11} L={L:5d} B={b:2d} demand {b * (L + N_PREDICT):6d}/{CTX}: e2e p50 {e2e}, {outcomes}")
                rows += rs
    return rows




def main():
    args = parse_args(__doc__)
    lengths = [512, 3072] if args.quick else [512, 1536, 3072]
    bs = [1, 4, 8] if args.quick else [1, 2, 4, 6, 8]
    rows = run("unified", lengths, bs) + run("partitioned", lengths, bs)
    save("micro", "m6_capacity", rows, {"description": __doc__, "ctx": CTX, "slots": SLOTS, "n_predict": N_PREDICT})


if __name__ == "__main__":
    main()
