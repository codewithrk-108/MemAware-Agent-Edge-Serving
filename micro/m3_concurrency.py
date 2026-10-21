"""M3: batching - decode speed and throughput vs number of concurrent requests and context length.

B requests with unique prompts start together. The KV pool is large enough for all of them, so this
isolates compute: how much aggregate throughput concurrency buys, and what it costs per request.
"""

import asyncio

import numpy as np

from harness.client import complete, new_session, random_tokens
from harness.results import save
from harness.server import Server, ServerConfig, ROOT
from micro.common import PORT, now, parse_args

N_PREDICT = 128
MAX_B = 32


async def burst(srv: Server, rng, b: int, prompt_len: int) -> list:
    async with new_session() as s:
        t0 = now()
        m0 = srv.memory_now()
        rs = await asyncio.gather(*[complete(s, srv.url, random_tokens(rng, prompt_len), N_PREDICT, cache_prompt=False)
                                    for _ in range(b)])
        t1 = now()
    peak = srv.mem.peak(t0, t1) / 2**20
    ok = [r for r in rs if not r["error"]]
    agg = sum(r["predicted_n"] for r in ok) / (t1 - t0)
    for r in rs:
        r.update(concurrency=b, prompt_len=prompt_len, wall_s=t1 - t0, agg_gen_tok_s=agg,
                 peak_footprint_mib=peak, start_footprint_mib=m0["footprint_mib"])
    print(f"ctx {prompt_len:5d}, B {b:3d}: tpot {np.median([r['tpot_ms'] for r in ok]):6.1f} ms, "
          f"ttft p50 {np.median([r['ttft_s'] for r in ok]) * 1e3:7.0f} ms, agg {agg:7.1f} tok/s, "
          f"failed {len(rs) - len(ok)}, peak footprint {peak:6.0f} MiB")
    return rs


async def bench(srv: Server, prompt_lens: list, bs: list, reps: int) -> list:
    rng = np.random.default_rng(3)
    rows = []
    await burst(srv, rng, 2, 128)  # warmup
    for prompt_len in prompt_lens:
        for b in bs:
            for rep in range(reps):
                for r in await burst(srv, rng, b, prompt_len):
                    r["rep"] = rep
                    rows.append(r)
    return rows


def main():
    args = parse_args(__doc__)
    prompt_lens = [128, 2048]
    bs = [1, 4, 16] if args.quick else [1, 2, 4, 8, 16, 32]
    ctx = MAX_B * (max(prompt_lens) + N_PREDICT + 64)
    cfg = ServerConfig(ctx=ctx, parallel=MAX_B, port=PORT)
    with Server(cfg, ROOT / "results/micro/m3_concurrency/server.log") as srv:
        rows = asyncio.run(bench(srv, prompt_lens, bs, 1 if args.quick else 2))
        save("micro", "m3_concurrency", rows, {"description": __doc__, "server": srv.describe(), "n_predict": N_PREDICT})


if __name__ == "__main__":
    main()
