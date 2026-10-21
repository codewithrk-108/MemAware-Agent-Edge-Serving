"""M5: do retained contexts survive while an agent is idle, and what does keeping them cost?

S agent sessions take turns (one request at a time, round robin), so between two steps of a session the other
S-1 sessions run - the situation of an agent waiting on a tool. Each step resends the full history plus a tool
result. The cache hit ratio (cache_n / prompt tokens) shows whether the session's context was still cached:
in a slot, in the host prompt cache (--cache-ram), or not at all. Memory is sampled after every request.
A second experiment adds wall-clock idle gaps with no interleaving, to separate idle time from interleaving.
"""

import asyncio

import numpy as np

from harness.client import complete, new_session, random_tokens
from harness.results import results_dir, save
from harness.server import Server, ServerConfig, ROOT
from micro.common import PORT, now, parse_args, settle

CTX = 16384
SLOTS = 4
N_INIT = 2048
N_GEN = 32
N_TOOL = 64

CONFIGS = {
    # name: (kv_unified, cache_ram, cache_idle_slots)
    "unified+ram":        (True,  None, None),   # server defaults: shared pool, 8 GiB host prompt cache
    "unified+ram-noidle": (True,  None, False),  # keep idle slots resident instead of moving them to RAM
    "unified-noram":      (True,  0,    None),
    "partitioned-noram":  (False, 0,    None),
    "partitioned+ram":    (False, None, None),
}


async def run_sessions(srv: Server, n_sessions: int, n_steps: int, gap_s: float, seed: int) -> list:
    rng = np.random.default_rng(seed)
    hist = [random_tokens(rng, N_INIT) for _ in range(n_sessions)]
    rows = []
    t_start = now()
    async with new_session() as s:
        for step in range(n_steps):
            if step > 0 and gap_s > 0:
                await asyncio.sleep(gap_s)
            for i in range(n_sessions):
                if step > 0:
                    hist[i] = hist[i] + random_tokens(rng, N_TOOL)
                r = await complete(s, srv.url, hist[i], N_GEN, cache_prompt=True, return_tokens=True)
                hist[i] = hist[i] + r["tokens"]
                r.update(session=i, step=step, t_rel=r["t_send"] - t_start,
                         hit_ratio=(r["cache_n"] / r["n_prompt"]) if r.get("cache_n") is not None else None,
                         **srv.memory_now())
                rows.append(r)
    return rows


def run(config: str, n_sessions: int, n_steps: int, gap_s: float, experiment: str) -> list:
    kvu, ram, idle = CONFIGS[config]
    cfg = ServerConfig(ctx=CTX, parallel=SLOTS, kv_unified=kvu, cache_ram=ram, cache_idle_slots=idle, port=PORT)
    log = results_dir() / f"micro/m5_retention/logs/{experiment}_{config}_S{n_sessions}.log"
    with Server(cfg, log) as srv:
        base = srv.memory_now()["footprint_mib"]
        rows = asyncio.run(run_sessions(srv, n_sessions, n_steps, gap_s, seed=n_sessions))
        asyncio.run(settle(0.5))
        m = srv.metrics()
    later = [r for r in rows if r["step"] > 0 and not r["error"]]
    hit = np.mean([r["hit_ratio"] for r in later]) if later else float("nan")
    ttft = np.median([r["ttft_s"] for r in later]) * 1e3 if later else float("nan")
    grow = max(r["footprint_mib"] for r in rows) - base
    errs = sum(1 for r in rows if r["error"])
    print(f"{experiment:>5} {config:>19} S={n_sessions:2d} gap={gap_s:4.1f}s: hit {hit:5.2f}, step>0 ttft p50 {ttft:7.1f} ms, "
          f"footprint +{grow:6.0f} MiB, errors {errs}")
    for r in rows:
        r.update(config=config, kv_unified=kvu, cache_ram=ram, cache_idle_slots=idle, n_sessions=n_sessions,
                 gap_s=gap_s, experiment=experiment, base_footprint_mib=base,
                 srv_prompt_tokens_cached_total=m.get("prompt_tokens_cached_total"))
    return rows


def main():
    args = parse_args(__doc__)
    sessions = [1, 4, 8] if args.quick else [1, 2, 4, 8, 16]
    n_steps = 2 if args.quick else 4
    configs = ["unified+ram", "unified-noram"] if args.quick else list(CONFIGS)
    rows = []
    for config in configs:
        for n in sessions:
            rows += run(config, n, n_steps, 0.0, "inter")
    for config in (["unified+ram"] if args.quick else ["unified+ram", "unified-noram", "partitioned-noram"]):
        for gap in [0.0, 10.0]:
            rows += run(config, 2, 3, gap, "gap")
    save("micro", "m5_retention", rows, {"description": __doc__, "ctx": CTX, "slots": SLOTS, "n_init": N_INIT,
                                          "n_gen": N_GEN, "n_tool": N_TOOL, "configs": CONFIGS})


if __name__ == "__main__":
    main()
