"""M4: value of reusing the cached context between two steps of one agent task.

Step 1 has a C-token context and generates 32 tokens. Step 2 sends the full history plus a 64-token tool result,
as an agent does. With cache_prompt the server reuses the cached prefix and only computes the new suffix;
without it, the whole history is recomputed. This bounds what retaining a context is worth.
"""

import asyncio

import numpy as np

from harness.client import complete, new_session, random_tokens
from harness.results import save
from harness.server import Server, ServerConfig, ROOT
from micro.common import PORT, parse_args

CTX = 32768
N_GEN = 32
N_TOOL = 64


async def bench(srv: Server, contexts: list, reps: int) -> list:
    rng = np.random.default_rng(4)
    rows = []
    async with new_session() as s:
        await complete(s, srv.url, random_tokens(rng, 256), 1, cache_prompt=False)  # warmup
        for c in contexts:
            for cache in (True, False):
                for rep in range(reps):
                    p1 = random_tokens(rng, c)
                    r1 = await complete(s, srv.url, p1, N_GEN, cache_prompt=True, return_tokens=True)
                    p2 = p1 + r1["tokens"] + random_tokens(rng, N_TOOL)
                    r2 = await complete(s, srv.url, p2, N_GEN, cache_prompt=cache)
                    r2.update(context=c, rep=rep, step1_ttft_s=r1["ttft_s"], step1_error=r1["error"])
                    rows.append(r2)
                ok = [r for r in rows if r["context"] == c and r["cache_prompt"] == cache and not r["error"]]
                print(f"context {c:6d}, cache_prompt {str(cache):5}: step-2 ttft {np.median([r['ttft_s'] for r in ok]) * 1e3:8.1f} ms, "
                      f"computed {np.median([r['prompt_n'] for r in ok]):6.0f} / reused {np.median([r['cache_n'] for r in ok]):6.0f} tokens")
    return rows


def main():
    args = parse_args(__doc__)
    contexts = [256, 4096] if args.quick else [256, 1024, 2048, 4096, 8192, 16384]
    cfg = ServerConfig(ctx=CTX, parallel=1, port=PORT)
    with Server(cfg, ROOT / "results/micro/m4_prefix_reuse/server.log") as srv:
        rows = asyncio.run(bench(srv, contexts, 1 if args.quick else args.reps))
        save("micro", "m4_prefix_reuse", rows, {"description": __doc__, "server": srv.describe(),
                                                 "n_gen": N_GEN, "n_tool": N_TOOL})


if __name__ == "__main__":
    main()
