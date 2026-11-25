"""Zero-shot check of the published Learning-to-Rank predictor (Fu et al., NeurIPS 2024) on our workload.

Predictor: LLM-ltr/OPT-Predictors `opt-125m-llama3-8b-sharegpt-score-trainbucket10-b32` (OPT-125M with a 1-output
sequence-classification head, ListMLE-trained on Llama-3-8B-Instruct output lengths for ShareGPT prompts; label =
(8192 - length) // 10, so a HIGHER score means a SHORTER predicted output). Used exactly as in vllm-ltr
(`vllm/model_executor/prefill_predictor.py`): raw prompt text, OPT tokenizer, right truncation at 2,048 tokens,
`logits` = score. Nothing is fine-tuned here.

Requests: every model request of the AgentTrace pool (data/traces/agenttrace_all_tool_delay_150x_max10s.jsonl).
  test        = the 113 requests of the 50-task smoke trace (data/traces/agenttrace_50_seed2.jsonl)
  calibration = all requests of tasks whose original problem (source_task_key) is not in the test set
The true output length (`n_gen`) is used only as the target to measure error and, on the calibration set only, to
fit the score -> tokens mapping ("factor"); test lengths are never used for fitting except in the explicitly
labelled in-sample row.

Prompt the predictor sees for step k (what a scheduler knows before the request runs, minus earlier model outputs,
which the trace does not record; at serving time they would be our server's own outputs):
  full_head  served prompt text (system prompt + task + tool results of steps < k, decoded from the trace's Qwen
             token ids), first 2,048 OPT tokens (paper's truncation)
  full_tail  same text, last 2,048 OPT tokens (sees the latest tool result)
  user_head  task text + tool results only (no system prompt / chat template; closer to ShareGPT user prompts),
             first 2,048 OPT tokens
Mappings score -> predicted tokens (fit on calibration, evaluated on test):
  const_median   no predictor: median calibration length (baseline)
  label_inverse  8192 - 10 * score (inverting the training label; score scale is not calibrated by ListMLE)
  factor         k * label_inverse, k = median(true / label_inverse) on calibration (one multiplicative factor)
  linear         a + b * score (least squares)
  loglinear      exp(a + b * score) (least squares on log length; predicts a typical/median length)
  isotonic       monotone non-increasing step function of score (best monotone calibration)
Run with /opt/pytorch/bin/python3 and PYTHONPATH=~/ltr_pylib (transformers), see README.
"""

import argparse
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from scipy.stats import kendalltau, spearmanr
from tokenizers import ByteLevelBPETokenizer, Tokenizer
from transformers import OPTForSequenceClassification

ROOT = Path(__file__).resolve().parents[2]
CKPT = Path.home() / "models/ltr/opt-125m-llama3-8b-sharegpt-score-trainbucket10-b32/finetuned"
OPT_TOK = Path.home() / "models/ltr/opt-125m-tokenizer"
QWEN_TOK = Path.home() / "phase-1-full500-v1/data/source/Qwen3-0.6B-tokenizer.json"
POOL = ROOT / "data/traces/agenttrace_all_tool_delay_150x_max10s.jsonl"
TEST = ROOT / "data/traces/agenttrace_50_seed2.jsonl"
READABLE = ROOT / "data/source/agenttrace_readable.jsonl"
OUT = ROOT / "results/ltr_ranking/opt_zero_shot"
MAXLEN = 2048


def key(task_id: str) -> str:
    return task_id.rsplit("-qwen3-", 1)[0]


def build_requests() -> pd.DataFrame:
    qwen = Tokenizer.from_file(str(QWEN_TOK))
    pool = [json.loads(l) for l in POOL.open()]
    readable = {r["task_id"]: r for r in map(json.loads, READABLE.open())}
    test_ids = [json.loads(l)["task_id"] for l in TEST.open()]
    test_keys = {key(t) for t in test_ids}
    rows = []
    for t in pool:
        r = readable[t["task_id"]]
        split = "test" if t["task_id"] in test_ids else ("excluded" if key(t["task_id"]) in test_keys else "calib")
        if split == "excluded":
            continue
        ids, user = [], r["initial_prompt"]
        for k, s in enumerate(t["steps"]):
            ids = ids + s["input"]
            if k > 0:
                user = user + "\n\n" + (r["steps"][k - 1]["tool"] or {}).get("result_text", "")
            rows.append({"task_id": t["task_id"], "cls": t["cls"], "step": k, "n_steps": len(t["steps"]),
                         "split": split, "prompt_tokens_qwen": len(ids), "n_gen": s["n_gen"],
                         "full_text": qwen.decode(ids, skip_special_tokens=False), "user_text": user})
    df = pd.DataFrame(rows)
    # the 50-task trace may contain a task twice; keep one row per (task, step) and check the count
    return df.drop_duplicates(["task_id", "step"]).reset_index(drop=True)


def opt_tokenizer():
    """OPT tokenizer = GPT-2 byte-level BPE (vocab.json + merges.txt of facebook/opt-125m) with </s> (id 2) prepended,
    built with `tokenizers` because transformers 5 cannot convert the slow tokenizer here. Verified: "Hello world" ->
    [2, 31414, 232], as with the reference OPT tokenizer."""
    return ByteLevelBPETokenizer(str(OPT_TOK / "vocab.json"), str(OPT_TOK / "merges.txt"), add_prefix_space=False)


@torch.no_grad()
def score(df: pd.DataFrame) -> pd.DataFrame:
    tok = opt_tokenizer()
    model = OPTForSequenceClassification.from_pretrained(CKPT).cuda().eval()
    out = {}
    enc_cache = {c: [tok.encode(t).ids for t in df[c]] for c in ("full_text", "user_text")}
    for name, col, side in (("full_head", "full_text", "right"), ("full_tail", "full_text", "left"),
                            ("user_head", "user_text", "right")):
        s, n, ms = [], [], []
        for ids in enc_cache[col]:
            t0 = time.perf_counter()
            ids = ids[:MAXLEN - 1] if side == "right" else ids[-(MAXLEN - 1):]   # BOS added after truncation, as HF
            x = torch.tensor([[2] + ids], device="cuda")
            s.append(float(model(input_ids=x, attention_mask=torch.ones_like(x)).logits[0, 0]))
            torch.cuda.synchronize()
            ms.append(1000 * (time.perf_counter() - t0))
            n.append(x.shape[1])
        out[f"score_{name}"], out[f"opt_tokens_{name}"], out[f"ms_{name}"] = s, n, ms
    out["opt_tokens_full_untruncated"] = [len(x) + 1 for x in enc_cache["full_text"]]
    return df.assign(**out)


def isotonic_decreasing(x, y):
    """Pool-adjacent-violators for a non-increasing fit of y on x; returns a predict function."""
    o = np.argsort(x)
    xs, ys = np.asarray(x)[o], -np.asarray(y, float)[o]
    blocks = [[v, 1.0] for v in ys]
    vals, wts, ends = [], [], []
    for i, (v, w) in enumerate(blocks):
        vals.append(v); wts.append(w); ends.append(i)
        while len(vals) > 1 and vals[-2] > vals[-1]:
            v2, w2 = vals.pop(), wts.pop(); ends.pop()
            vals[-1] = (vals[-1] * wts[-1] + v2 * w2) / (wts[-1] + w2); wts[-1] += w2; ends[-1] = i
    fit = np.empty(len(xs))
    start = 0
    for v, e in zip(vals, ends):
        fit[start:e + 1] = -v; start = e + 1
    return lambda q: np.interp(q, xs, fit)


def mappings(cal_s, cal_y):
    inv = lambda s: np.clip(8192 - 10 * np.asarray(s), 1, 8192)
    k = float(np.median(cal_y / inv(cal_s)))
    b, a = np.polyfit(cal_s, cal_y, 1)
    lb, la = np.polyfit(cal_s, np.log(cal_y), 1)
    iso = isotonic_decreasing(cal_s, cal_y)
    med = float(np.median(cal_y))
    params = {"factor_k": k, "linear_a": a, "linear_b": b, "loglinear_a": la, "loglinear_b": lb, "const_median": med}
    return {
        "const_median": lambda s: np.full(len(s), med),
        "label_inverse": inv,
        "factor": lambda s: k * inv(s),
        "linear": lambda s: np.clip(a + b * np.asarray(s), 1, None),
        "loglinear": lambda s: np.exp(la + lb * np.asarray(s)),
        "isotonic": iso,
    }, params


def errors(y, p) -> dict:
    y, p = np.asarray(y, float), np.asarray(p, float)
    e = p - y
    return {"MAE": np.abs(e).mean(), "median_AE": np.median(np.abs(e)), "mean_signed": e.mean(),
            "median_APE_pct": 100 * np.median(np.abs(e) / y), "within_2x_pct": 100 * np.mean((p >= y / 2) & (p <= 2 * y)),
            "under_pct": 100 * np.mean(p < y), "RMSE": np.sqrt((e ** 2).mean())}


def rank_metrics(y, s) -> dict:
    return {"kendall_tau": kendalltau(-np.asarray(s), y).statistic, "spearman": spearmanr(-np.asarray(s), y).statistic}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    df = score(build_requests())
    test, cal = df[df.split == "test"], df[df.split == "calib"]
    assert len(test) == 113, len(test)
    rows, params_all = [], {}
    for v in ("full_head", "full_tail", "user_head"):
        sc = f"score_{v}"
        maps, params = mappings(cal[sc].values, cal.n_gen.values.astype(float))
        params_all[v] = params
        for name, f in maps.items():
            rows.append({"input": v, "mapping": name, "fit_on": "calibration",
                         **errors(test.n_gen, f(test[sc].values)), **rank_metrics(test.n_gen, test[sc])})
        ins, _ = mappings(test[sc].values, test.n_gen.values.astype(float))
        for name in ("factor", "loglinear", "isotonic"):
            rows.append({"input": v, "mapping": name, "fit_on": "test (in-sample, optimistic)",
                         **errors(test.n_gen, ins[name](test[sc].values)), **rank_metrics(test.n_gen, test[sc])})
        df[f"pred_factor_{v}"] = maps["factor"](df[sc].values)
        df[f"pred_loglinear_{v}"] = maps["loglinear"](df[sc].values)
    # reference rankers on the test set
    ref = {"prompt length (longer prompt = longer output)": -test.prompt_tokens_qwen.values,
           "step index (later step = longer output)": -test.step.values,
           "oracle (true length)": -test.n_gen.values}
    refs = [{"ranker": k, **rank_metrics(test.n_gen, s)} for k, s in ref.items()]
    refs += [{"ranker": f"OPT {v} (test)", **rank_metrics(test.n_gen, test[f'score_{v}'])} for v in
             ("full_head", "full_tail", "user_head")]
    refs += [{"ranker": f"OPT {v} (calibration, n={len(cal)})", **rank_metrics(cal.n_gen, cal[f'score_{v}'])} for v in
             ("full_head", "full_tail", "user_head")]
    test = df[df.split == "test"]
    by_step = []
    for v in ("full_head", "full_tail", "user_head"):
        for name, g in (("step 0", test[test.step == 0]), ("steps 1+", test[test.step > 0])):
            by_step.append({"input": v, "subset": name, "n": len(g), **rank_metrics(g.n_gen, g[f"score_{v}"]),
                            **errors(g.n_gen, g[f"pred_factor_{v}"])})
    res, refs, by_step = pd.DataFrame(rows), pd.DataFrame(refs), pd.DataFrame(by_step)
    df.drop(columns=["full_text", "user_text"]).to_csv(OUT / "predictions.csv", index=False)
    res.to_csv(OUT / "errors.csv", index=False)
    refs.to_csv(OUT / "rank_reference.csv", index=False)
    by_step.to_csv(OUT / "by_step.csv", index=False)
    ms = {v: {"mean": float(df[f"ms_{v}"].mean()), "p95": float(df[f"ms_{v}"].quantile(.95))}
          for v in ("full_head", "full_tail", "user_head")}
    trunc = float((test.opt_tokens_full_untruncated > MAXLEN).mean() * 100)
    meta = {"checkpoint": str(CKPT), "n_test": len(test), "n_calibration": len(cal), "mapping_params": params_all,
            "score_ms_per_request": ms, "test_pct_truncated_full": trunc,
            "test_n_gen": test.n_gen.describe().to_dict(), "calib_n_gen": cal.n_gen.describe().to_dict(),
            "doc": __doc__}
    (OUT / "meta.json").write_text(json.dumps(meta, indent=2, default=float))
    with pd.option_context("display.width", 250, "display.max_columns", 20, "display.float_format", "{:.2f}".format):
        print(res.to_string(index=False)); print(); print(refs.to_string(index=False)); print()
        print(by_step.to_string(index=False)); print(json.dumps({"ms": ms, "truncated_pct": trunc,
                                                                 "params": params_all}, indent=1, default=float))


if __name__ == "__main__":
    main()
