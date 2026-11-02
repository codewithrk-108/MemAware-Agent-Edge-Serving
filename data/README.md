# Phase 1 workload dataset

Replayable multi-step agent tasks built from real agent trajectories. Each task is a fixed sequence of model requests (token IDs and an output budget) separated by tool waits with a recorded tool result. The same file gives every scheduler identical prompts, output lengths, tool results and delays.

## Files

| File | Tasks | Requests | Tool waits | Used for |
|---|---:|---:|---:|---|
| `source/agenttrace_readable.jsonl` | 1,257 runs | 3,200 | 1,943 | Combined source: selected AgentTrace rows in readable form (see `source/README.md`) |
| `traces/agenttrace_all_tool_delay_150x_max10s.jsonl` | 1,169 | 2,506 | 1,337 | Token-ID trace with scaled tool delays; the pool both workloads below are drawn from (and what the S1 L4 burst runs sampled from at seed 2) |
| `traces/agenttrace_50_seed2.jsonl` | 50 | 113 | 63 | Smoke and stress tests: writeup S1–S3 |
| `traces/agenttrace_full_500_seed2.jsonl` | 500 | 1,068 | 568 | Full runs R1–R7 |

Every trace has an adjacent `.manifest.json` with input and output SHA-256 hashes, the transformation or selection rule, and counts. `agenttrace_full_500_seed2.jsonl.stats.md` describes the 500-task file in detail.

## How it is built

```
pagarsky/agent-trace on Hugging Face (MBPP + NL2Bash runs of Qwen3-0.6B and Qwen3-1.7B)
  │  select rows with usable step/tool structure            -> source/agenttrace_readable.jsonl
  │  workloads.build_agenttrace --split all --max-context 16384
  │     tokenize with the Qwen3-0.6B tokenizer, prepend the smolagents tool-calling system prompt,
  │     drop runs whose full context exceeds 16,384 tokens (88 of 1,257)
  │  workloads.derive_agenttrace scale
  │     tool_delay_s = min(recorded delay × 150, 10 s); 15 of 1,337 delays hit the cap
  ▼                                                          -> traces/agenttrace_all_tool_delay_150x_max10s.jsonl
  ├─ workloads.derive_agenttrace sample   numpy default_rng(2), 50 tasks without replacement
  │                                                          -> traces/agenttrace_50_seed2.jsonl
  └─ workloads.build_full_agenttrace      seeded stratified sample: 500 distinct problems,
                                          step-count and class quotas, rare long tasks first
                                                             -> traces/agenttrace_full_500_seed2.jsonl
```

Rebuild everything and check it against the committed files (byte-for-byte):

```bash
./workloads/rebuild_workloads.sh
```

The script downloads the tokenizer to `data/source/Qwen3-0.6B-tokenizer.json` if it is missing, from <https://huggingface.co/Qwen/Qwen3-0.6B/resolve/main/tokenizer.json>, and checks its SHA-256 `aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4`. The intermediate `traces/agenttrace_all.jsonl` (unscaled delays) is built in a temporary folder and not kept.

## Trace format

One task per line:

```json
{"task_id": "agenttrace-mbpp-139-qwen3-0.6b", "cls": "mbpp", "source": "agenttrace",
 "steps": [{"input": [151644, 8948, ...], "n_gen": 1544, "tool_delay_s": 0.0123},
           {"input": [...], "n_gen": 310, "tool_delay_s": null}]}
```

- `input`: token IDs appended to the conversation before this request. Step 1 holds the system prompt and task; later steps hold the recorded tool result.
- `n_gen`: tokens to generate (greedy, `ignore_eos`), taken from the source run.
- `tool_delay_s`: wait after this request before the next one; `null` ends the task.
- The runner appends the model's own generated tokens between steps, so context grows as in a real agent loop. Tools are never executed and answers are not scored.
- `task_id` ends with the source model; the 0.6B and 1.7B runs of one problem are different tasks with a shared problem key.

## Statistics

| | 50-task sample | 500-task sample | Pool (1,169) |
|---|---:|---:|---:|
| MBPP / NL2Bash tasks | 35 / 15 | 350 / 150 | 819 / 350 |
| Distinct problems | 50 | 500 | 682 |
| Steps per task: mean (max) | 2.26 (11) | 2.14 (11) | 2.14 (11) |
| Tasks with 1 / 2 / 3 / ≥4 steps | 17 / 21 / 8 / 4 | 165 / 230 / 60 / 45 | 386 / 536 / 141 / 106 |
| First prompt, tokens: median (max) | 1,113 (1,303) | 1,113 (4,749) | 1,114 (4,749) |
| Output budget per request: median / p95 | 995 / 4,641 | 960 / 4,467 | 955 / 4,378 |
| Full prompt before a request: median / p95 / max | 1,826 / 9,606 / 14,794 | 1,562 / 8,097 / 16,062 | 1,569 / 8,480 / 16,062 |
| Tool wait per call, s: median / p95 / max | 0.12 / 3.71 / 10.0 | 0.20 / 4.46 / 10.0 | 0.19 / 4.19 / 10.0 |
| Total output tokens | 179,622 | 1,591,939 | 3,681,820 |

## Checksums (SHA-256)

| File | SHA-256 |
|---|---|
| `source/agenttrace_readable.jsonl` | `20b2439cff3a440e29a401e8952ec5d779876feab5d9ef2801c5d8c69e324fd9` |
| `traces/agenttrace_all_tool_delay_150x_max10s.jsonl` | `81346e727fb278983e39d4e7d4b96f08082f8ef29856523fa96c98e1a7c2ad04` |
| `traces/agenttrace_50_seed2.jsonl` | `84a8ff891dce683ace8682787957dc888b2198aed36412554f355cdd1d2f15a1` |
| `traces/agenttrace_full_500_seed2.jsonl` | `9131e06eb8915b466e91a608d7f135634373d59dd9ccb84578385065ab30573b` |

## Limitations

One source (AgentTrace: MBPP and NL2Bash, two tool types), little variation in first-prompt length, and short tool waits even after the 150× scaling. Exgentic traces from the project plan are not included yet. The scaled delays are a controlled experimental choice, not measured execution times.
