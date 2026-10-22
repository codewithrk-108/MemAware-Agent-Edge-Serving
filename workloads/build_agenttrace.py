"""Convert readable AgentTrace runs to token-ID tasks for phase1/runner.py.

The source rows contain user prompts, step output budgets, and fixed tool results.
This converter supplies a pinned agent system prompt and Qwen ChatML framing,
then asks the served llama.cpp model to tokenize those exact strings.
"""

import argparse
import hashlib
import json
import os
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Callable


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/source/agenttrace_readable.jsonl"
PROMPT = ROOT / "workloads/prompts/agenttrace_smolagents_1_24_0.txt"
OUTPUT = ROOT / "data/traces/agenttrace.jsonl"


class TokenizeClient:
    """Send text to the same llama-server model used for inference."""

    def __init__(self, base_url: str, timeout_s: float = 60):
        self.url = base_url.rstrip("/") + "/tokenize"
        self.timeout_s = timeout_s

    def tokenize(self, text: str) -> list[int]:
        body = json.dumps({"content": text, "parse_special": True}).encode()
        request = urllib.request.Request(self.url, body, {"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
            tokens = json.load(response)["tokens"]
        if not isinstance(tokens, list) or not all(isinstance(t, int) for t in tokens):
            raise ValueError("/tokenize did not return a list of integer token IDs")
        return tokens


class JsonTokenizer:
    """Tokenize offline with the pinned Qwen tokenizer.json artifact."""

    def __init__(self, path: Path):
        from tokenizers import Tokenizer
        self.tokenizer = Tokenizer.from_file(str(path))

    def tokenize(self, text: str) -> list[int]:
        return self.tokenizer.encode(text, add_special_tokens=False).ids


def first_input(system_prompt: str, user_prompt: str) -> str:
    return (f"<|im_start|>system\n{system_prompt}<|im_end|>\n"
            f"<|im_start|>user\n{user_prompt}<|im_end|>\n"
            "<|im_start|>assistant\n")


def tool_continuation(name: str, result: str) -> str:
    return ("<|im_end|>\n"
            f"<|im_start|>tool\n{name}: {result}<|im_end|>\n"
            "<|im_start|>assistant\n")


def convert_row(row: dict, tokenize: Callable[[str], list[int]],
                system_prompt: str, max_context: int) -> dict:
    """Build one runner task; reject runs that would exceed the KV context."""
    source_steps = row["steps"]
    if not source_steps or not row["initial_prompt"].strip():
        raise ValueError("invalid_source: empty prompt or steps")
    if not system_prompt.strip():
        raise ValueError("invalid_system_prompt: empty instructions")

    steps = []
    total_tokens = 0
    next_input = first_input(system_prompt, row["initial_prompt"])
    for i, source_step in enumerate(source_steps):
        n_gen = source_step["n_gen"]
        tool = source_step["tool"]
        if not isinstance(n_gen, int) or n_gen <= 0:
            raise ValueError("invalid_source: nonpositive output budget")
        if (i < len(source_steps) - 1) != (tool is not None):
            raise ValueError("invalid_source: tool step does not match continuation")
        token_ids = tokenize(next_input)
        if not token_ids:
            raise ValueError("tokenizer returned empty input")
        total_tokens += len(token_ids) + n_gen
        if total_tokens > max_context:
            raise ValueError("context_limit")
        steps.append({"input": token_ids, "n_gen": n_gen,
                      "tool_delay_s": float(tool["observed_delay_s"]) if tool is not None else None})
        if tool is not None:
            next_input = tool_continuation(tool["name"], tool["result_text"])

    return {"task_id": row["task_id"], "cls": row["cls"],
            "source": "agenttrace", "steps": steps}


def convert_source(source: Path, output: Path, tokenize: Callable[[str], list[int]],
                   system_prompt: str, split: str = "all", max_context: int = 16384,
                   source_model: str = "all") -> dict:
    """Convert selected rows, retaining fixed budgets and reporting exclusions."""
    if split not in {"all", "dev", "eval"}:
        raise ValueError("split must be all, dev, or eval")
    if max_context <= 0:
        raise ValueError("max_context must be positive")
    counts = Counter()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    try:
        with source.open() as src, temporary.open("w") as dst:
            for line in src:
                if not line.strip():
                    continue
                row = json.loads(line)
                counts["source_rows"] += 1
                if split != "all" and row["split"] != split:
                    counts["filtered_split"] += 1
                    continue
                if source_model != "all" and row["source_model"] != source_model:
                    counts["filtered_model"] += 1
                    continue
                try:
                    task = convert_row(row, tokenize, system_prompt, max_context)
                except ValueError as exc:
                    if str(exc) != "context_limit":
                        raise ValueError(f"{row.get('task_id', '<unknown>')}: {exc}") from exc
                    counts["excluded_context_limit"] += 1
                    continue
                dst.write(json.dumps(task, separators=(",", ":")) + "\n")
                counts["written"] += 1
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return dict(counts)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--out", type=Path, default=OUTPUT)
    parser.add_argument("--system-prompt", type=Path, default=PROMPT)
    parser.add_argument("--split", choices=("all", "dev", "eval"), default="all")
    parser.add_argument("--source-model", choices=("all", "Qwen/Qwen3-0.6B", "Qwen/Qwen3-1.7B"), default="all")
    parser.add_argument("--max-context", type=int, default=16384)
    tokenizer_source = parser.add_mutually_exclusive_group()
    tokenizer_source.add_argument("--url", help="Use an already running llama-server instead of starting one")
    tokenizer_source.add_argument("--tokenizer-json", type=Path,
                                  help="Tokenize offline with a pinned tokenizer.json file")
    parser.add_argument("--port", type=int, default=8095)
    args = parser.parse_args()
    system_prompt = args.system_prompt.read_text()

    def run(tokenize: Callable[[str], list[int]]) -> None:
        report = convert_source(args.source, args.out, tokenize,
                                system_prompt, args.split, args.max_context, args.source_model)
        manifest = {"source_file": str(args.source), "source_sha256": sha256(args.source),
                    "output_file": str(args.out), "output_sha256": sha256(args.out),
                    "system_prompt_file": str(args.system_prompt),
                    "system_prompt_sha256": sha256(args.system_prompt),
                    "split": args.split, "source_model": args.source_model,
                    "max_context": args.max_context, "counts": report}
        if args.tokenizer_json:
            manifest["tokenizer_json_file"] = str(args.tokenizer_json)
            manifest["tokenizer_json_sha256"] = sha256(args.tokenizer_json)
        args.out.with_name(args.out.name + ".manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        print(json.dumps(manifest, indent=2))

    if args.tokenizer_json:
        run(JsonTokenizer(args.tokenizer_json).tokenize)
    elif args.url:
        run(TokenizeClient(args.url).tokenize)
    else:
        from harness.server import Server, ServerConfig
        log = ROOT / "results/_logs/agenttrace_tokenizer.log"
        with Server(ServerConfig(ctx=args.max_context, parallel=1, port=args.port), log) as server:
            run(TokenizeClient(server.url).tokenize)


if __name__ == "__main__":
    main()
