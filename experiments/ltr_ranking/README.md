# Learning to Rank (Fu et al., NeurIPS 2024) on our workload

Paper: *Efficient LLM Scheduling by Learning to Rank*, [arXiv 2408.15792](https://arxiv.org/abs/2408.15792), code
[hao-ai-lab/vllm-ltr](https://github.com/hao-ai-lab/vllm-ltr). Released artefacts: trained predictors
(Hugging Face `LLM-ltr/OPT-Predictors`, OPT-125M for Llama-3-8B and OPT-350M for Llama-3-70B, ShareGPT and LMSYS,
ranking and classification variants) and training data (`LLM-ltr/Llama3-Trace`: ShareGPT / LMSYS / Alpaca prompts
with Llama-3-8B/70B-Instruct outputs).

## Step 1: how good is the published predictor on our 113 requests, unchanged? (2026-11-19)

**Question (user):** how far are the original OPT predictor's predictions from the true number of output tokens on
the 113 requests of the 50-task smoke trace, can a factor bring them in line, and what is the error after the factor?

### Method ([`opt_zero_shot.py`](opt_zero_shot.py))

- **Predictor:** `opt-125m-llama3-8b-sharegpt-score-trainbucket10-b32` (OPT-125M + 1-output head, ListMLE, labels
  `(8192 − Llama-3-8B output length) // 10`, so a higher score means a shorter output). Used as in vllm-ltr
  (`vllm/model_executor/prefill_predictor.py`): raw prompt text, OPT tokenizer, at most 2,048 tokens, one score per
  request. No fine-tuning.
- **Tokenizer note:** transformers 5.23 could not build OPT's fast tokenizer from `vocab.json`/`merges.txt`
  (`AutoTokenizer` raised; `GPT2TokenizerFast` silently returned `[0]`). Built with `tokenizers.ByteLevelBPETokenizer`
  plus `</s>` (id 2) in front, verified against OPT's known encoding ("Hello world" → `[2, 31414, 232]`).
- **Requests:** test = the 113 requests of `data/traces/agenttrace_50_seed2.jsonl`. Calibration = 2,283 requests of
  the AgentTrace pool (`agenttrace_all_tool_delay_150x_max10s.jsonl`) from tasks whose original problem is not in the
  test set (both the 0.6B and 1.7B runs of a test problem are excluded). All 1,169 pool tasks matched their
  `data/source/agenttrace_readable.jsonl` rows with identical `n_gen`.
- **What the predictor reads for step k** (what a scheduler knows before the request runs): the trace does not record
  earlier model outputs (at serving time they are our own server's outputs), so the prompt is system prompt + task +
  tool results of steps before k. Three variants:
  - `full_head`: served prompt text decoded from the trace's Qwen token ids, first 2,048 OPT tokens (paper's truncation);
  - `full_tail`: same, last 2,048 OPT tokens (sees the latest tool result);
  - `user_head`: task text + tool results only (no system prompt or chat template, closer to ShareGPT prompts), first
    2,048 OPT tokens.
  4.4% of test prompts exceed 2,048 OPT tokens.
- **Score → tokens ("factor") mappings**, fitted on calibration only and evaluated on test:
  `const_median` (no predictor, the baseline any mapping must beat), `label_inverse` (8192 − 10 × score, inverting the
  training label), `factor` (k × label_inverse, k = median of true / label_inverse), `linear` (a + b × score),
  `loglinear` (exp(a + b × score)), `isotonic` (best monotone mapping). Also fitted on the test set itself
  (in-sample, optimistic) as an upper bound for any factor.
- True output length is used only as the target and, on calibration requests, to fit the mapping.

### Results

Test set: true output length mean 1,590, median 995, p25/p75 561 / 2,095, max 7,471 tokens.

**Error in tokens (113 test requests; mapping fitted on calibration):**

| Input | Mapping | MAE | Median abs. error | Median % error | Within 2× | Under-estimates | Mean signed error |
|---|---|---:|---:|---:|---:|---:|---:|
| – | **constant median 951 (no predictor)** | **978** | **531** | **57%** | **51%** | **55%** | −639 |
| full_head | raw label inverse (8192 − 10s) | 6,602 | 7,197 | 723% | 9% | 0% | +6,602 |
| full_head | one factor (k = 0.116) | 978 | 531 | 57% | 51% | 55% | −639 |
| full_head | linear | 997 | 860 | 63% | 51% | 46% | −171 |
| full_head | log-linear | 955 | 566 | 59% | 54% | 55% | −550 |
| full_head | isotonic | 951 | 635 | 60% | 50% | 43% | −173 |
| full_tail | log-linear | 938 | 559 | 57% | 57% | 54% | −535 |
| full_tail | isotonic | 919 | 619 | 54% | 54% | 43% | −143 |
| user_head | log-linear | 940 | 569 | 51% | 58% | 52% | −487 |
| user_head | isotonic | 930 | 702 | 55% | 57% | 43% | −37 |
| user_head | isotonic **fitted on test (in-sample)** | 900 | 640 | 50% | 61% | 42% | −2 |

Full table (all inputs × mappings, RMSE, in-sample rows): `results/ltr_ranking/opt_zero_shot/errors.csv`.

**Ranking quality (Kendall's τ between predicted and true order; Spearman in brackets):**

| Ranker | Test (113) | Calibration (2,283) |
|---|---:|---:|
| OPT full_head | 0.24 (0.37) | 0.28 (0.42) |
| OPT full_tail | 0.27 (0.40) | 0.28 (0.42) |
| OPT user_head | 0.27 (0.39) | 0.26 (0.39) |
| Prompt length | 0.03 | – |
| Step index (later = longer) | −0.19 | – |
| Oracle | 1.00 | – |
| *Paper, in-domain (ShareGPT / LMSYS)* | *0.54 / 0.62* | |
| *Paper, cross-dataset* | *0.40–0.45* | |

By step (test): step 0 (50 requests) τ 0.26 / 0.26 / 0.21; later steps (63) τ 0.05 (full_head) / 0.10 (full_tail) /
0.23 (user_head). Head truncation of the full prompt shows the predictor mostly the shared system prompt and the task,
so it barely distinguishes later steps of the same task.

Scoring cost on the T4 (batch 1): 17 ms per request for full prompts, 8 ms for user text (p95 23 / 9 ms).

### What this shows

1. **The raw score is not a token count.** Inverting the training label predicts ~8,000 tokens for every request
   (MAE 6,600): ListMLE only fixes the order, and the scores vary by a few units (−3.9 to +3.7), so 8192 − 10 × score
   is almost constant.
2. **A single multiplicative factor cannot fix that.** With almost constant base predictions, the best factor
   (k = 0.116) turns every prediction into ~951 tokens, i.e. exactly the constant-median baseline (MAE 978 for both).
3. **The best monotone mapping beats the constant by only 3–6%.** MAE 919–955 vs 978; median % error 51–60% vs 57%;
   within 2× 50–58% vs 51%. Even fitted on the test set itself (in-sample), the error is 900 tokens MAE and 50%
   median error. Predictions are compressed towards the middle: the user_head log-linear predictions span p10–p90
   594–1,609 tokens while the true lengths span 400–3,853, so long outputs are badly underestimated (42–55% of all
   requests are underestimated).
4. **As a ranker it is weak but not useless:** τ 0.24–0.27, about half of the paper's in-domain 0.54 and below its
   cross-dataset 0.40–0.45 (different target model, Qwen3 vs Llama-3, and agent prompts vs chat). It is much better
   than prompt length (0.03). Only `user_head` keeps some signal on later steps (τ 0.23).
5. **For our schedulers:** not usable as an output budget for memory reservation (it would underestimate about half
   the requests, the overflow problem of FIFO N4, or cause continuations). A ranking-only use (shortest-predicted
   first) is possible but with τ ≈ 0.27 the expected gain is small. A predictor trained on Qwen3 output lengths of
   our own (non-test) agent requests is the obvious next step (proposed option B).

### Environment and files

- Predictor runs in the preinstalled PyTorch at `/opt/pytorch` (torch 2.14.1+cu130) with `transformers` 5.23 and its
  missing dependencies installed to `~/ltr_pylib` (`pip --target`, 193 MB). A first attempt with a venv
  (`~/ltr_venv`) could not see `/opt/pytorch`'s torch (it is itself a venv) and was deleted.
- Checkpoint and OPT tokenizer files: `~/models/ltr/` (241 MB). Root disk is ~99% full (≈480 MB free afterwards).
- Code from the authors (for reading only): sparse clone in the session scratchpad, not in the repo.
- Outputs `results/ltr_ranking/opt_zero_shot/`: `predictions.csv` (per request: split, step, Qwen prompt tokens,
  true `n_gen`, scores, OPT token counts, scoring ms, factor and log-linear predictions), `errors.csv`,
  `rank_reference.csv`, `by_step.csv`, `meta.json` (fitted mapping parameters, timing), `run.log`.

```bash
PYTHONPATH=~/ltr_pylib /opt/pytorch/bin/python3 -W ignore -m experiments.ltr_ranking.opt_zero_shot   # ~2 min
```

## Step 2: with the model's own earlier outputs in the prompt (2026-11-19)

**Question (user):** step 1 lacked the model's previous outputs (reasoning + tool call), which the trace does not
store; replay the workload as in the experiments and measure again how far the predicted output budgets are from
the true token counts.

### Replay ([`replay_outputs.py`](replay_outputs.py))

Same generation as every experiment (Qwen3-0.6B Q8_0, unified 32K pool, `--cache-ram 0`, greedy, `ignore_eos`,
each step generates exactly its recorded `n_gen`, history = history + generated tokens + next tool result), 4 tasks
at a time, tool waits skipped. Output: every step's generated token ids in
`results/ltr_ranking/replay_outputs/outputs.jsonl` (113 rows, prompt lengths checked against the trace).

Attempts (all recorded because they exposed two issues):
1. **Streaming loses token ids.** With the streamed client (`harness/client.py`) a step got 521–530 of the 533
   tokens the server generated. Cause, from the b11780 source (`tools/server/server-context.cpp`): after each token
   the server checks for an incomplete UTF-8 character at the end of the text (lines 2028–2029) and sends no stream
   message for that token (`if (!incomplete) { … send_partial_response }`); the next message carries only its own
   token id (`res->tokens.assign(1, tkn.tok)`, line 2245), and the final stream message carries none (lines
   2284–2287). Token ids of multi-byte characters split across tokens are therefore never sent. Non-streamed
   responses return the server's full list (`res->tokens = std::move(slot.generated_tokens)`), so the replay uses a
   non-streamed request. **Effect on earlier experiments** (they build histories from streamed tokens): Exp 4 cap
   2,048 lost 198 tokens over 14 of 104 step/continuation handovers (max 82), E5a 89 tokens over 11; ≈0.1% of the
   generated tokens. The next prompt then matched the server's cache only up to the first missing token, so a few
   handovers recomputed part of their prompt. Scheduling comparisons are unaffected in any meaningful way.
2. **KV overflow, no saving:** four long histories overflowed the 32K pool ("Context size has been exceeded", as in
   FIFO N4); 3 immediate retries failed and everything was lost (results written only at the end).
3. A fix (incremental saving, 20 retries with growing pauses) silently did not apply (a scripted text replacement
   failed its match check and the old code ran); same crash. Log: `replay_outputs_attempt2_failed.log`.
4. With the fix applied: **livelock** — the four workers overflowed together, backed off by the same amount and
   collided again (36 consecutive overflows, even for 1,247-token first steps), while idle slots kept their old
   caches. Log: `replay_outputs_attempt3_livelock.log`. Fixed with KV-aware admission inside the replay: a step is
   sent only if history + n_gen fits next to the other workers' reservations; a waiting worker erases its slot
   first. Using the recorded `n_gen` is acceptable here because the replay only produces text for this evaluation;
   it is not a scheduler under test.
5. 49 of 50 tasks done with 0 retries when the session ended (`replay_outputs_attempt4_interrupted.log`); the
   resumed run finished the last task (113 requests, 0 retries).

Observation: none of the 113 generated outputs contains an end-of-turn token (`ignore_eos` keeps the model going), so
each output continues past where the model would have stopped, often repeating itself ("… The final answer is "12".
The final"). This is how all experiments generate, and it matters for what a predictor reading the history sees.

### Scoring ([`opt_with_outputs.py`](opt_with_outputs.py))

Same predictor. Inputs: full history (system prompt + task + earlier outputs + tool results) first or last 2,048 OPT
tokens; the same without the system prompt / chat template, first or last 2,048. Later-step prompts now have a median
of 5,100 OPT tokens; 50% of requests exceed 2,048 (41% without the system prompt). Score → tokens mappings fitted by
**leave-one-task-out** on the 113 (each task predicted by a mapping fitted on the other 49), plus the step-1
calibration mappings (fitted on prompts without outputs) for reference.

### Results (113 requests; true median 995, mean 1,590)

| Input | Mapping | MAE (tokens) | Median % error | Within 2× | Under-estimated | Kendall τ |
|---|---|---:|---:|---:|---:|---:|
| – | constant median (no predictor) | 980 | 59% | 51% | 50% | – |
| full, first 2,048 | one factor | 980 | 59% | 51% | 50% | 0.30 |
| full, first 2,048 | **log-linear** | **925** | 57% | 56% | 50% | **0.30** |
| full, first 2,048 | isotonic | 1,004 | 62% | 54% | 45% | 0.30 |
| full, last 2,048 | log-linear | 962 | 59% | 53% | 50% | 0.21 |
| user text, first 2,048 | isotonic | 967 | 57% | 56% | 37% | 0.17 |
| user text, last 2,048 | log-linear | 997 | 61% | 53% | 48% | 0.10 |

Full table: `results/ltr_ranking/opt_with_outputs/errors.csv`.

Kendall τ without → with earlier outputs (all / later steps): full first 2,048: 0.24 → 0.30 / 0.05 → 0.11; full last
2,048: 0.27 → 0.21 / 0.10 → −0.08; user text first 2,048: 0.27 → 0.17 / 0.23 → 0.17. Step 0 is unchanged (no
earlier outputs). Prediction spread (best mapping, p10–p90) 834–2,605 vs true 400–3,853.

### What this shows

1. **Adding the real history does not rescue the published predictor.** Best case MAE 925 vs 980 for a constant
   (−6%), median error 57%, half the requests underestimated; a single factor still equals the constant.
2. **Ranking improves only slightly** (best τ 0.30 vs 0.24, paper 0.54–0.62) and stays near random on later steps.
3. **Why:** the histories exceed OPT's 2,048-token window for half the requests; the head shows the shared system
   prompt, task and first answer, and the tail shows the end of an `ignore_eos` answer (repetitive filler) plus the
   tool result, which ranks worse (τ < 0 on later steps). The predictor was also trained on another model's lengths
   (Llama-3) for another prompt type (chat).
4. **Conclusion:** the published LTR predictor is not usable as an output budget for our workload in any variant
   tried. Training the same OPT-125M on Qwen3-0.6B output lengths of our own non-test agent requests is the remaining
   LTR option.

Outputs: `results/ltr_ranking/opt_with_outputs/` (`predictions.csv`, `errors.csv`, `by_step.csv`,
`tau_with_vs_without_outputs.csv`, `meta.json`, `run.log`); replay: `results/ltr_ranking/replay_outputs/`
(`outputs.jsonl`, `outputs.partial.jsonl`, `meta.json`, `server.log`) and `results/ltr_ranking/replay_outputs*.log`.

```bash
export MODEL=/path/to/Qwen3-0.6B-Q8_0.gguf LLAMA_SERVER=/path/to/instrumented/llama-server
.venv/bin/python -u -m experiments.ltr_ranking.replay_outputs                                       # ~15 min
PYTHONPATH=~/ltr_pylib /opt/pytorch/bin/python3 -W ignore -m experiments.ltr_ranking.opt_with_outputs  # ~1 min
```
