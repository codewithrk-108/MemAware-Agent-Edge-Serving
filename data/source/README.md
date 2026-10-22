# AgentTrace readable workload

`agenttrace_readable.jsonl` is **one combined source dataset**, not yet an input accepted by the current runner. It contains selected traces from the MBPP and NL2Bash Qwen3-0.6B and Qwen3-1.7B files in [pagarsky/agent-trace](https://huggingface.co/datasets/pagarsky/agent-trace). The source URLs, SHA-256 hashes, selection rule, split rule, and counts are in `agenttrace_manifest.json`. The original source files were not modified.

Each JSONL row is one recorded run of a task. The schema is:

```json
{"task_id":"agenttrace-nl2bash-0-qwen3-0.6b","source_task_key":"agenttrace-nl2bash-0","source_trace_id":"...","cls":"nl2bash","split":"dev","source_model":"Qwen/Qwen3-0.6B","initial_prompt":"...","steps":[{"n_gen":500,"source_input_tokens":1500,"tool":{"name":"bash","result_text":"...","observed_delay_s":0.025}},{"n_gen":300,"source_input_tokens":1800,"tool":null}]}
```

`task_id` uniquely identifies the run and includes the source model. `source_task_key` identifies the original task: its 0.6B and 1.7B runs are distinct rows but always share the same `dev` or `eval` split. These model variants are **not independent task prompts** when measuring generalization.

`n_gen` is the source step's recorded `output_tokens`; `source_input_tokens` records the original run's context size for analysis. A non-null `tool` is the fixed result to append **after** that step's model generation and tool wait. `tool: null` ends the task. The generated tokens in the new run will be retained for the next request; recorded model response text is not replayed. Tool commands are never executed. `observed_delay_s` is the source trace's measured tool-span duration (`duration_ms / 1000`); the converter uses it directly as `tool_delay_s`. Controlled synthetic waits can be added later as a separate workload variant.

The combined file contains 1,257 retained runs from 1,400 source rows: 652 from Qwen3-0.6B and 605 from Qwen3-1.7B. They cover 696 original tasks; 561 tasks have both model variants. The 143 excluded rows lack usable model steps or have ambiguous tool-call structure. No context-length or output-length limit has been applied yet.

`workloads/build_agenttrace.py` converts this file to the token-ID format used by `phase1/runner.py`. Its shared system prompt is in `workloads/prompts/agenttrace_smolagents_1_24_0.txt`, with provenance in the adjacent JSON file. It is a deterministic reconstruction of the `smolagents` 1.24.0 tool-calling prompt: the source trace recorded the user task and input-token counts, but not the exact rendered system message. The converter accepts either the served model's `/tokenize` endpoint or the pinned Qwen3-0.6B `tokenizer.json` in this directory. It adds the system instructions before the first user task and tokenizes each later fixed tool result as the next step's input. The runner appends its own generated tokens between steps. No source tool command is executed.

The included `Qwen3-0.6B-tokenizer.json` came from [Qwen/Qwen3-0.6B](https://huggingface.co/Qwen/Qwen3-0.6B/resolve/main/tokenizer.json); its SHA-256 is `aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4`. The checked-in `agenttrace_dev.jsonl` and `agenttrace_eval.jsonl` under `data/traces/` were built from it at a 16,384-token context limit. Conversion retained 932 development and 237 evaluation runs, excluding 69 and 19 runs respectively for context length. Their manifests record the source, prompt, tokenizer, and output hashes. Verify a sample against the actual GGUF server's `/tokenize` endpoint before inference experiments.

To regenerate them offline:

```bash
python -m workloads.build_agenttrace --source data/source/agenttrace_readable.jsonl \
  --system-prompt workloads/prompts/agenttrace_smolagents_1_24_0.txt \
  --tokenizer-json data/source/Qwen3-0.6B-tokenizer.json \
  --out data/traces/agenttrace_dev.jsonl --split dev --max-context 16384
python -m workloads.build_agenttrace --source data/source/agenttrace_readable.jsonl \
  --system-prompt workloads/prompts/agenttrace_smolagents_1_24_0.txt \
  --tokenizer-json data/source/Qwen3-0.6B-tokenizer.json \
  --out data/traces/agenttrace_eval.jsonl --split eval --max-context 16384
```

If a llama-server with the target model is already running:

```bash
python -m workloads.build_agenttrace --url http://127.0.0.1:8080 \
  --source data/source/agenttrace_readable.jsonl \
  --out data/traces/agenttrace_dev.jsonl --split dev --max-context 16384
python -m workloads.build_agenttrace --url http://127.0.0.1:8080 \
  --source data/source/agenttrace_readable.jsonl \
  --out data/traces/agenttrace_eval.jsonl --split eval --max-context 16384
```

Without `--url`, the script starts the configured `llama-server` and model (see `LLAMA_SERVER` and `MODEL` in `harness/server.py`). It writes a manifest beside each output with the source and prompt hashes, chosen filters, and exclusion counts. Record and pin the served GGUF separately; an external `--url` does not reveal its model artifact to this script. Rows whose full tokenized context plus fixed output budgets exceed `--max-context` are excluded rather than shortened. These generated traces are the files to pass to `phase1.runner --trace`. Use the same converted files for FIFO and later schedulers. Check the resulting context and output-length distributions before running a large CPU experiment; the 1.7B source runs can have very long recorded generations.
