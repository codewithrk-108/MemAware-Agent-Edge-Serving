"""Async client for llama-server /completion with client-side timing."""

import json
import time
from typing import Optional

import aiohttp
import numpy as np

# Qwen3 has ordinary vocabulary tokens in this range; avoid special and byte-token regions.
_TOKEN_LO, _TOKEN_HI = 1000, 30000


def random_tokens(rng: np.random.Generator, n: int) -> list:
    """Unique-content prompt with an exact token count (prompts are sent as token ids, no tokenizer involved)."""
    return rng.integers(_TOKEN_LO, _TOKEN_HI, size=n).tolist()


def new_session(limit: int = 256, timeout: float = 3600) -> aiohttp.ClientSession:
    # force_close: the server drops idle keep-alive connections, reusing one fails with ServerDisconnectedError
    return aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=limit, force_close=True),
                                 timeout=aiohttp.ClientTimeout(total=timeout))


async def complete(session: aiohttp.ClientSession, url: str, prompt: list, n_predict: int,
                   cache_prompt: bool = True, return_tokens: bool = False, id_slot: int = -1,
                   timeout: Optional[float] = None) -> dict:
    """One streamed completion. Returns client timings, server timings and the error if any; never raises."""
    body = {
        "prompt": prompt,
        "n_predict": n_predict,
        "ignore_eos": True,          # fixed response length, independent of what the model writes
        "cache_prompt": cache_prompt,
        "return_tokens": return_tokens,
        "id_slot": id_slot,
        "stream": True,
        "temperature": 0.0,
    }
    rec = {"n_prompt": len(prompt), "n_predict_req": n_predict, "cache_prompt": cache_prompt,
           "t_send": time.monotonic(), "t_first": None, "t_last": None, "t_end": None, "error": None, "status": None}
    n_streamed = 0
    tokens = []
    final = None
    try:
        req_timeout = aiohttp.ClientTimeout(total=timeout) if timeout else None
        async with session.post(url + "/completion", json=body, timeout=req_timeout) as r:
            rec["status"] = r.status
            if r.status != 200:
                rec["error"] = (await r.text())[:300]
            else:
                async for raw in r.content:
                    line = raw.decode().strip()
                    if not line.startswith("data: "):
                        continue
                    msg = json.loads(line[6:])
                    if "error" in msg:
                        rec["error"] = json.dumps(msg["error"])[:300]
                        break
                    if msg.get("content") or msg.get("tokens"):
                        t = time.monotonic()
                        rec["t_first"] = rec["t_first"] or t
                        rec["t_last"] = t
                        n_streamed += 1
                    tokens += msg.get("tokens") or []
                    if msg.get("stop"):
                        final = msg
                        break
    except Exception as e:  # timeouts and connection errors are results, not crashes
        rec["error"] = f"{type(e).__name__}: {e}"[:300]
    rec["t_end"] = time.monotonic()

    rec["e2e_s"] = rec["t_end"] - rec["t_send"]
    rec["ttft_s"] = rec["t_first"] - rec["t_send"] if rec["t_first"] else None
    # client-side time per output token; the server's predicted_ms is unreliable under concurrency
    rec["tpot_ms"] = (rec["t_last"] - rec["t_first"]) / (n_streamed - 1) * 1e3 if n_streamed > 1 else None
    if final:
        t = final.get("timings", {})
        rec.update({
            "prompt_n": t.get("prompt_n"),          # prompt tokens computed
            "cache_n": t.get("cache_n"),            # prompt tokens reused from the cache
            "prompt_ms": t.get("prompt_ms"),
            "predicted_n": t.get("predicted_n"),
            "predicted_ms": t.get("predicted_ms"),
            "srv_tpot_ms": t.get("predicted_per_token_ms"),
            "id_slot": final.get("id_slot"),
            "truncated": final.get("truncated"),
            "stop_type": final.get("stop_type"),
        })
        if rec["error"] is None and t.get("predicted_n", 0) < n_predict:
            rec["error"] = f"short response: {t.get('predicted_n')} < {n_predict} ({final.get('stop_type')})"
    elif rec["error"] is None:
        rec["error"] = "stream ended without a final message"
    if return_tokens:
        rec["tokens"] = tokens
    return rec
