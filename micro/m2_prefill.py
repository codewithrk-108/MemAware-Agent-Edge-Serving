"""M2: prefill cost vs prompt length.

One request at a time, unique prompts, prompt caching off. TTFT is the cost of admitting a request with a
long (or discarded and recomputed) context.
"""

import asyncio

import numpy as np

from harness.client import complete, new_session, random_tokens
from harness.results import results_dir, save
from harness.server import Server, ServerConfig, ROOT
from micro.common import PORT, parse_args

CTX = 32768


async def bench(srv: Server, lengths: list, reps: int) -> list:
    rng = np.random.default_rng(2)
    rows = []
    async with new_session() as s:
        await complete(s, srv.url, random_tokens(rng, 256), 1, cache_prompt=False)  # warmup
        for n in lengths:
            for rep in range(reps):
                r = await complete(s, srv.url, random_tokens(rng, n), 1, cache_prompt=False)
                r.update(rep=rep, prefill_tok_s=(r["prompt_n"] / r["prompt_ms"] * 1e3) if r.get("prompt_ms") else None)
                rows.append(r)
            ok = [r for r in rows if r["n_prompt"] == n and not r["error"]]
            print(f"prompt {n:6d}: ttft {np.median([r['ttft_s'] for r in ok]) * 1e3:8.1f} ms, "
                  f"prefill {np.median([r['prefill_tok_s'] for r in ok]):7.0f} tok/s")
    return rows


def main():
    args = parse_args(__doc__)
    lengths = [128, 1024, 8192] if args.quick else [64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384, 32000]
    cfg = ServerConfig(ctx=CTX, parallel=1, port=PORT)
    with Server(cfg, results_dir() / "micro/m2_prefill/server.log") as srv:
        rows = asyncio.run(bench(srv, lengths, 1 if args.quick else args.reps))
        save("micro", "m2_prefill", rows, {"description": __doc__, "server": srv.describe()})


if __name__ == "__main__":
    main()
