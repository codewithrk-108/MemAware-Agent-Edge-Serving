"""Online (no-future-data) output-length estimation on the 500-task workload: how does ranking quality grow with history?

Question (user, 2026-11-25): start with the fixed output budget of the fixed-cap runs and, as requests finish, update
the estimated output length of each new request from the history gathered so far. How much does Kendall's tau improve,
and how fast?

Replay of information, not of serving: each request (one agent step) gets its admission time (first chunk sent) and
completion time (last chunk finished) from the 500-task E6 run (results/mem_admission/full500_e6_cap2048_lookup), the
closest real run with fixed 2,048-token reservations. When a request is admitted, an estimator may use ONLY requests
whose completion time is earlier (their true lengths are then known to the server) plus what the request itself shows
at admission. No offline data, no lookup table, no future data. Caveat: real orderings would change these times; the
timeline is held fixed here.

Estimators (refitted after every REFIT new completions; before MIN_FIT completions every request gets the fixed cap):
  fixed_cap      every request 2,048 (the fixed-cap runs; no ranking: all tied, tau undefined, shown as 0)
  online_median  median true length of completed requests with the same (class, step 0 / 1 / 2+); falls back to the
                 overall completed median for an unseen cell
  online_gbm     gradient-boosted trees on log length, features known at admission: class, step, this task's
                 previous output lengths (last, mean), tool result error flag and length, history size in tokens,
                 task text length, number of test cases in the task text
Scored in windows of 100 requests by admission order: Kendall tau-b, MAE, median % error, share underestimated, and
the number of completed requests the estimator had learned from.
Outputs: results/online_length/predictions.csv, windows.csv, summary.json, tau_over_time.png.
"""

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.stats import kendalltau
from sklearn.ensemble import HistGradientBoostingRegressor

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / "results/mem_admission/full500_e6_cap2048_lookup"
TRACE = ROOT / "data/traces/agenttrace_full_500_seed2.jsonl"
READABLE = ROOT / "data/source/agenttrace_readable.jsonl"
OUT = ROOT / "results/online_length"
CAP, MIN_FIT, REFIT, WINDOW = 2048, 30, 20, 100
FEATURES = ["cls", "sb", "prev", "meanprev", "err", "toollen", "hist", "task_chars", "n_tests"]


def requests() -> pd.DataFrame:
    tasks = {json.loads(l)["task_id"]: json.loads(l) for l in TRACE.open()}
    readable = {r["task_id"]: r for r in map(json.loads, READABLE.open())}
    feat = {}
    for tid, t in tasks.items():
        r, hist, prevs, txt = readable[tid], 0, [], readable[tid]["initial_prompt"]
        for k, s in enumerate(t["steps"]):
            hist += len(s["input"])
            res = (r["steps"][k - 1]["tool"] or {}).get("result_text", "") if k else ""
            feat[(tid, k)] = {"cls": int(t["cls"] == "mbpp"), "cls_name": t["cls"], "sb": min(k, 2), "step": k,
                              "prev": prevs[-1] if prevs else -1, "meanprev": float(np.mean(prevs)) if prevs else -1,
                              "err": int(("Error" in res) or ("Traceback" in res)), "toollen": len(res), "hist": hist,
                              "task_chars": len(txt), "n_tests": txt.count("assert"), "n": s["n_gen"]}
            prevs.append(s["n_gen"])
            hist += s["n_gen"]
    rq = pd.read_csv(RUN / "requests.csv")
    rq["end"] = rq.t_rel + rq.e2e_s
    g = rq.groupby(["task_id", "step"]).agg(admit=("t_rel", "min"), done=("end", "max")).reset_index()
    df = pd.DataFrame([{**feat[(r.task_id, r.step)], "task_id": r.task_id, "admit": r.admit, "done": r.done}
                       for r in g.itertuples()])
    assert len(df) == sum(len(t["steps"]) for t in tasks.values())
    return df.sort_values("admit").reset_index(drop=True)


def simulate(df: pd.DataFrame) -> pd.DataFrame:
    done_order = df.sort_values("done")
    preds = {"fixed_cap": [], "online_median": [], "online_gbm": []}
    n_known, fitted_at, gbm, med_tab, overall = [], -1, None, {}, CAP
    for _, r in df.iterrows():
        known = done_order[done_order.done < r.admit]
        n = len(known)
        n_known.append(n)
        if n >= MIN_FIT and (fitted_at < 0 or n - fitted_at >= REFIT):
            gbm = HistGradientBoostingRegressor(max_iter=200, learning_rate=0.05, min_samples_leaf=min(30, max(5, n // 10)),
                                                random_state=0).fit(known[FEATURES], np.log(known.n))
            med_tab = known.groupby(["cls", "sb"]).n.median().to_dict()
            overall = float(known.n.median())
            fitted_at = n
        preds["fixed_cap"].append(CAP)
        if gbm is None:
            preds["online_median"].append(CAP)
            preds["online_gbm"].append(CAP)
        else:
            preds["online_median"].append(med_tab.get((r.cls, r.sb), overall))
            preds["online_gbm"].append(float(np.exp(gbm.predict(r[FEATURES].to_frame().T.astype(float))[0])))
    return df.assign(n_known=n_known, **{f"pred_{k}": v for k, v in preds.items()})


def score(y, p) -> dict:
    y, p = np.asarray(y, float), np.asarray(p, float)
    tau = kendalltau(p, y).statistic if len(np.unique(p)) > 1 else 0.0
    e = p - y
    return {"tau": tau, "MAE": np.abs(e).mean(), "median_pct_err": 100 * np.median(np.abs(e) / y),
            "under_pct": 100 * np.mean(p < y)}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    df = simulate(requests())
    df.to_csv(OUT / "predictions.csv", index=False)
    rows = []
    for w, g in df.groupby(df.index // WINDOW):
        for m in ("fixed_cap", "online_median", "online_gbm"):
            rows.append({"window": w, "requests": f"{w * WINDOW + 1}-{w * WINDOW + len(g)}", "n": len(g),
                         "admit_from_min": g.admit.min() / 60, "admit_to_min": g.admit.max() / 60,
                         "known_at_start": int(g.n_known.min()), "known_at_end": int(g.n_known.max()),
                         "estimator": m, **score(g.n, g[f"pred_{m}"])})
    win = pd.DataFrame(rows)
    win.to_csv(OUT / "windows.csv", index=False)
    warm = df[df.n_known >= MIN_FIT]
    summary = {m: {"all": score(df.n, df[f"pred_{m}"]), "after_warmup": score(warm.n, warm[f"pred_{m}"])}
               for m in ("fixed_cap", "online_median", "online_gbm")}
    summary["n_requests"] = len(df)
    summary["n_before_warmup"] = int((df.n_known < MIN_FIT).sum())
    summary["warmup_ends_min"] = float(df[df.n_known >= MIN_FIT].admit.min() / 60)
    summary["doc"] = __doc__
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, default=float))

    fig, (a, b) = plt.subplots(2, 1, figsize=(10, 7.5), dpi=150, sharex=True)
    x = win[win.estimator == "online_gbm"].known_at_end.values
    labels = {"online_gbm": "online model (features)", "online_median": "online class × step median",
              "fixed_cap": "fixed 2,048 budget"}
    colors = {"online_gbm": "#2348a8", "online_median": "#6f9be0", "fixed_cap": "#999999"}
    for m in ("online_gbm", "online_median", "fixed_cap"):
        s = win[win.estimator == m]
        a.plot(range(len(s)), s.tau, marker="o", color=colors[m], label=labels[m])
        b.plot(range(len(s)), s.MAE, marker="o", color=colors[m], label=labels[m])
    a.axhline(0.32, color="#c2410c", ls="--", lw=1)
    a.text(0.1, 0.335, "frozen offline table (287 non-overlapping tasks): 0.32", color="#c2410c", fontsize=8.5)
    a.set_ylabel("Kendall τ-b in window"); a.legend(frameon=False, fontsize=9, loc="lower right")
    b.set_ylabel("mean abs. error (tokens)")
    ticks = [f"{r}\n({k} known)" for r, k in zip(win[win.estimator == 'online_gbm'].requests,
                                                  win[win.estimator == 'online_gbm'].known_at_end)]
    b.set_xticks(range(len(ticks))); b.set_xticklabels(ticks, fontsize=7.5)
    b.set_xlabel("requests in admission order (completed requests known to the estimator at the window's end)")
    for ax in (a, b):
        ax.spines[["top", "right"]].set_visible(False); ax.grid(axis="y", alpha=0.3)
    a.set_title("Online output-length estimation on the 500-task workload (no future data)", fontsize=11)
    fig.tight_layout()
    fig.savefig(OUT / "tau_over_time.png")
    with pd.option_context("display.width", 200, "display.max_rows", 100, "display.float_format", "{:.2f}".format):
        print(win.pivot(index=["requests", "known_at_start", "known_at_end"], columns="estimator", values="tau").to_string())
        print()
        print(win.pivot(index="requests", columns="estimator", values="MAE").round(0).to_string())
    print(json.dumps({m: summary[m] for m in ("fixed_cap", "online_median", "online_gbm")}, indent=1, default=float))
    print("before warm-up:", summary["n_before_warmup"], "requests; warm-up ends at", round(summary["warmup_ends_min"], 1), "min")


if __name__ == "__main__":
    main()


# ---------------------------------------------------------------------------------------------------------------
# Learning curve (added after the first run): per-window tau mixes learning with window composition (E6 admitted
# mbpp first steps late, so windows 7-10 are >50% one kind of request and the last has almost no first steps).
# Here the estimator is trained on the first K requests to COMPLETE (completion order = what a server would have
# learned after K completions) and scored on all other requests of the 1,068, a fixed-size population.
# ---------------------------------------------------------------------------------------------------------------
def learning_curve():
    df = requests()
    order = df.sort_values("done").index.values
    rows = []
    for k in (30, 50, 100, 150, 200, 300, 400, 500, 700, 900):
        tr, te = df.loc[order[:k]], df.drop(index=order[:k])
        gbm = HistGradientBoostingRegressor(max_iter=200, learning_rate=0.05, min_samples_leaf=min(30, max(5, k // 10)),
                                            random_state=0).fit(tr[FEATURES], np.log(tr.n))
        med = tr.groupby(["cls", "sb"]).n.median().to_dict()
        pm = te.apply(lambda r: med.get((r.cls, r.sb), tr.n.median()), axis=1)
        pg = np.exp(gbm.predict(te[FEATURES]))
        rows.append({"known": k, "test_n": len(te), "train_median_len": tr.n.median(),
                     "train_first_step_pct": 100 * (tr.step == 0).mean(),
                     **{f"median_{a}": b for a, b in score(te.n, pm).items()},
                     **{f"gbm_{a}": b for a, b in score(te.n, pg).items()}})
    lc = pd.DataFrame(rows)
    lc.to_csv(OUT / "learning_curve.csv", index=False)
    fig, ax = plt.subplots(figsize=(9, 4.6), dpi=150)
    ax.plot(lc.known, lc.gbm_tau, marker="o", color="#2348a8", label="online model (features)")
    ax.plot(lc.known, lc.median_tau, marker="o", color="#6f9be0", label="online class × step median")
    ax.axhline(0.32, color="#c2410c", ls="--", lw=1)
    ax.text(lc.known.max(), 0.327, "frozen offline table: 0.32", color="#c2410c", fontsize=8.5, ha="right")
    ax.axhline(0, color="#999", lw=1)
    ax.text(lc.known.max(), 0.008, "fixed 2,048 budget: no ranking (0)", color="#777", fontsize=8.5, ha="right")
    ax.set_xscale("log"); ax.set_xticks(lc.known); ax.set_xticklabels(lc.known)
    ax.set_xlabel("completed requests the estimator has learned from (first K to finish)")
    ax.set_ylabel("Kendall τ-b on all other requests")
    ax.set_title("Learning curve: ranking quality vs history gathered (500-task workload, no future data)", fontsize=10.5)
    ax.spines[["top", "right"]].set_visible(False); ax.grid(axis="y", alpha=0.3); ax.legend(frameon=False, fontsize=9)
    fig.tight_layout(); fig.savefig(OUT / "learning_curve.png")
    with pd.option_context("display.width", 220, "display.float_format", "{:.2f}".format):
        print(lc[["known", "test_n", "train_median_len", "train_first_step_pct", "median_tau", "gbm_tau", "median_MAE",
                  "gbm_MAE", "gbm_median_pct_err", "gbm_under_pct"]].to_string(index=False))
