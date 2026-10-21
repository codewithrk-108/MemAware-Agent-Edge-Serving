"""Build an agent trace from the AgentInstruct dataset, tokenized with the served model's chat template.

Download the dataset first:
    hf download zai-org/AgentInstruct --repo-type dataset --local-dir data/raw/AgentInstruct
"""

import argparse
import asyncio
import glob
from pathlib import Path

import aiohttp
import numpy as np
import pandas as pd
import yaml

from harness import platform
from harness.server import Server, ServerConfig
from workloads.trace import Step, Task, describe, lognormal, save

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# Llama 3 chat template
ROLE = {"human": "user", "gpt": "assistant"}
BOS = "<|begin_of_text|>"


def turn_text(role: str, text: str) -> str:
    return f"<|start_header_id|>{role}<|end_header_id|>\n\n{text}<|eot_id|>"


ASSISTANT_HEADER = "<|start_header_id|>assistant<|end_header_id|>\n\n"
EOT = "<|eot_id|>"


async def tokenize_all(url: str, texts: list, concurrency: int = 32) -> list:
    sem = asyncio.Semaphore(concurrency)
    async with aiohttp.ClientSession() as s:
        async def one(t):
            async with sem:
                async with s.post(url + "/tokenize", json={"content": t, "parse_special": True}) as r:
                    return (await r.json())["tokens"]
        return await asyncio.gather(*[one(t) for t in texts])


def segments(conv) -> list:
    """Split a conversation into (first_context_text, [(gpt_text, following_tool_text or None), ...])."""
    conv = list(conv)
    first = next((i for i, t in enumerate(conv) if t["from"] == "gpt" and t["loss"]), None)
    if first is None:
        return None
    context = BOS + "".join(turn_text(ROLE[t["from"]], t["value"]) for t in conv[:first]) + ASSISTANT_HEADER
    calls = []
    i = first
    while i < len(conv):
        if conv[i]["from"] != "gpt":
            i += 1
            continue
        tool = conv[i + 1]["value"] if i + 1 < len(conv) and conv[i + 1]["from"] == "human" else None
        calls.append((conv[i]["value"], tool))
        i += 2
    # a trailing environment message after the final answer starts no further model call
    if calls and calls[-1][1] is not None and not any(t["from"] == "gpt" for t in conv[i:]):
        calls[-1] = (calls[-1][0], None)
    return context, calls


def build(spec: dict, url: str, raw: Path) -> tuple:
    rng = np.random.default_rng(spec["seed"])
    convs = []
    for f in sorted(glob.glob(str(raw / "data/*.parquet"))):
        env = Path(f).name.split("-")[0]
        d = pd.read_parquet(f)
        convs += [(env, row.id, segments(row.conversations)) for row in d.itertuples()]
    convs = [c for c in convs if c[2] is not None]

    # tokenize every piece once: first context, generated turns, tool results (with the template around them)
    texts = []
    for _, _, (ctx, calls) in convs:
        texts.append(ctx)
        for gen, tool in calls:
            texts.append(gen + EOT)
            if tool is not None:
                texts.append(turn_text("user", tool) + ASSISTANT_HEADER)
    toks = iter(asyncio.run(tokenize_all(url, texts)))

    tasks, dropped = [], 0
    for env, cid, (ctx, calls) in convs:
        delay = spec["environments"][env]
        steps = []
        inp = next(toks)
        for gen, tool in calls:
            n_gen = len(next(toks))
            if tool is None:
                steps.append(Step(input=inp, n_gen=n_gen, tool_delay_s=None))
                break
            steps.append(Step(input=inp, n_gen=n_gen, tool_delay_s=lognormal(rng, delay["median"], delay["sigma"])))
            inp = next(toks)
        else:
            steps[-1].tool_delay_s = None
        # drain tokens of calls after an early break (keeps the iterator aligned)
        remaining = calls[len(steps):]
        for gen, tool in remaining:
            next(toks)
            if tool is not None:
                next(toks)
        t = Task(task_id=f"{env}-{cid}", cls=env, source="agentinstruct", steps=steps)
        if t.total_tokens > spec["max_context"]:
            dropped += 1
            continue
        tasks.append(t)
    return tasks, dropped


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--spec", type=Path, default=HERE / "agentinstruct.yaml")
    p.add_argument("--raw", type=Path, default=ROOT / "data/raw/AgentInstruct")
    p.add_argument("--out", type=Path, default=ROOT / "data/traces/agentinstruct.jsonl")
    platform.add_argument(p)
    a = p.parse_args()
    platform.apply(a)
    spec = yaml.safe_load(a.spec.read_text())
    with Server(ServerConfig(ctx=2048, parallel=1, port=8095), ROOT / "results/_logs/tokenizer.log") as srv:
        tasks, dropped = build(spec, srv.url, a.raw)
    save(tasks, a.out)
    print(f"wrote {a.out} ({len(tasks)} tasks, {dropped} dropped for exceeding {spec['max_context']} tokens)\n{describe(tasks)}")


if __name__ == "__main__":
    main()
