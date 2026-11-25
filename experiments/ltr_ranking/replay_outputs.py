"""Replay the 50-task smoke trace on llama.cpp and record the tokens the server generates at every step.

Why: the trace keeps only the length of each earlier model output, not its text, so a length predictor evaluated
on the trace alone never sees the model's previous reasoning and tool call (name, code, command). At serving time a
scheduler does see them: they are our own server's outputs in the request history. This replay produces them.

Same generation as every experiment: Qwen3-0.6B Q8_0, f16 KV, unified 32K pool (-kvu, -c 32768), --cache-ram 0, no
mmap, greedy (temperature 0), ignore_eos, each step generates exactly its recorded n_gen tokens, history = previous
history + generated tokens + next step's input (tool result). Tool waits are skipped (they do not change the
tokens). 4 slots, 4 tasks at a time, each worker pinned to its slot. Greedy output is the same as in the scheduling
experiments up to batch-dependent floating-point differences. A step that fails (e.g. "Context size has been
exceeded" when four long histories meet, as in FIFO N4) is retried after erasing its slot, up to 20 times with a
growing pause (5 s x attempt) so that other requests can finish. Finished tasks are appended to
outputs.partial.jsonl as they complete, and a rerun skips them. (The first two full attempts used 3 immediate
retries, failed on the same overflow and lost everything because output was written only at the end.)
Output: results/ltr_ranking/replay_outputs/outputs.jsonl (one row per step: task_id, step, prompt length, n_gen,
generated token ids), meta.json, server.log.
"""

import asyncio
import json
import time
from pathlib import Path

from harness.client import new_session
from harness.server import Server, ServerConfig

ROOT = Path(__file__).resolve().parents[2]
TRACE = ROOT / "data/traces/agenttrace_50_seed2.jsonl"
OUT = ROOT / "results/ltr_ranking/replay_outputs"
WORKERS = 4


async def generate(session, url, prompt, n, slot):
    """Non-streamed completion returning every generated token id. (The streamed client in harness/client.py lost
    a few token ids per long response in this check: 521-530 of 533 received while the server reported 533.)"""
    body = {"prompt": prompt, "n_predict": n, "ignore_eos": True, "cache_prompt": True, "return_tokens": True,
            "id_slot": slot, "stream": False, "temperature": 0.0}
    t0 = time.monotonic()
    try:
        async with session.post(url + "/completion", json=body) as r:
            js = await r.json(content_type=None)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "tokens": [], "e2e_s": time.monotonic() - t0}
    if "error" in js:
        return {"error": json.dumps(js["error"])[:300], "tokens": [], "e2e_s": time.monotonic() - t0}
    return {"error": None, "tokens": js.get("tokens") or [], "predicted_n": js.get("timings", {}).get("predicted_n"),
            "stop_type": js.get("stop_type"), "e2e_s": time.monotonic() - t0}


BUDGET = 32768 - 512
RES = {}            # cells per worker: its running request's history + n_gen, or the cache its slot still holds
COND = None


async def admit(wid, url, session, need):
    """Wait until history + n_gen of this step fits next to the other workers' reservations. Uses the recorded
    output length; acceptable here because this replay only produces text for the predictor evaluation (it is not
    a scheduler under test). Added after the third attempt livelocked: four workers overflowed together, backed off
    by the same amount and collided again (36 consecutive overflows). A waiting worker erases its slot first so its
    old cache does not block the others."""
    async with COND:
        if sum(RES.values()) - RES.get(wid, 0) + need > BUDGET:
            async with session.post(f"{url}/slots/{wid}?action=erase", json={}) as e:
                await e.read()
            RES[wid] = 0
            COND.notify_all()
            await COND.wait_for(lambda: sum(RES.values()) - RES.get(wid, 0) + need <= BUDGET)
        RES[wid] = need


async def worker(wid, url, session, queue, rows, log):
    while True:
        try:
            task = queue.get_nowait()
        except asyncio.QueueEmpty:
            return
        history, task_rows = [], []
        for k, s in enumerate(task["steps"]):
            history = history + s["input"]
            await admit(wid, url, session, len(history) + s["n_gen"])
            for attempt in range(20):
                r = await generate(session, url, history, s["n_gen"], wid)
                if r["error"] is None and len(r["tokens"]) == s["n_gen"]:
                    break
                log.append({"task_id": task["task_id"], "step": k, "attempt": attempt, "error": r["error"],
                            "n_tokens": len(r.get("tokens") or []), "predicted_n": r.get("predicted_n"),
                            "n_prompt": len(history), "stop_type": r.get("stop_type")})
                print("[retry]", log[-1], flush=True)
                async with session.post(f"{url}/slots/{wid}?action=erase", json={}) as e:
                    await e.read()
                await asyncio.sleep(5 * (attempt + 1))
            else:
                raise RuntimeError(f"{task['task_id']} step {k} failed 20 times: {r['error']}")
            task_rows.append({"task_id": task["task_id"], "step": k, "n_prompt": len(history), "n_gen": s["n_gen"],
                              "e2e_s": r["e2e_s"], "tokens": r["tokens"]})
            history = history + r["tokens"]
        rows.extend(task_rows)
        with (OUT / "outputs.partial.jsonl").open("a") as f:
            for r in task_rows:
                f.write(json.dumps(r) + "\n")
        print(f"[w{wid}] {task['task_id']} done ({len(task['steps'])} steps, {len(rows)} requests total)", flush=True)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    tasks = [json.loads(l) for l in TRACE.open()]
    cfg = ServerConfig(ctx=32768, parallel=WORKERS, kv_unified=True, cache_ram=0, mmap=False,
                       extra=["--slot-save-path", "/dev/shm"])
    part = OUT / "outputs.partial.jsonl"
    rows = [json.loads(l) for l in part.open()] if part.exists() else []
    done = {r["task_id"] for r in rows}
    log = []
    t0 = time.time()
    with Server(cfg, OUT / "server.log", sample_interval=1.0) as srv:
        async def go():
            global COND
            COND = asyncio.Condition()
            q = asyncio.Queue()
            for t in tasks:
                if t["task_id"] not in done:
                    q.put_nowait(t)
            async with new_session() as s:
                await asyncio.gather(*(worker(w, srv.url, s, q, rows, log) for w in range(WORKERS)))
        asyncio.run(go())
    rows.sort(key=lambda r: ([t["task_id"] for t in tasks].index(r["task_id"]), r["step"]))
    with (OUT / "outputs.jsonl").open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    (OUT / "meta.json").write_text(json.dumps({"trace": str(TRACE.relative_to(ROOT)), "n_tasks": len(tasks),
                                               "n_requests": len(rows), "wall_s": time.time() - t0,
                                               "retries": log, "server_args": " ".join(cfg.args()),
                                               "doc": __doc__}, indent=2))
    print(f"done: {len(rows)} requests in {time.time() - t0:.0f} s, {len(log)} retries", flush=True)


if __name__ == "__main__":
    main()
