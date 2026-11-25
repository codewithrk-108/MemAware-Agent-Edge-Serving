"""Fit the swap and recompute cost models from data/measurements.csv.

  swap out (GPU->CPU):  T_out(L) = a_out * L + b_out           L = tokens in the slot (n_saved)
  swap in  (CPU->GPU):  T_in(L)  = a_in  * L + b_in            L = tokens restored (n_restored)
  recompute (prefill):  T_rc(L)  = c + d * L + e * L^2         L = prompt tokens computed (prompt_n)

All times in milliseconds, L in tokens. Ordinary least squares on every measurement (all repetitions).
Each model is fitted twice:
  server  the runtime's own timing (save_ms, restore_ms, prompt_ms): the work itself
  client  wall time seen by the scheduler: also includes waiting for the server loop between batches and HTTP
          (for recompute this is TTFT, which for a full prefill is prefill plus the first token)
Quality: R^2, RMSE, MAPE on the training data, and 5-fold cross-validation grouped by request, so the
repetitions of one request never sit on both sides of a split.

Writes params.json, fit_report.md and fit.png next to this script.
"""

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent

MODELS = {
    # name: (target column (server), target column (client), length column, polynomial degree, param names)
    "swap_out": ("save_ms", "save_client_ms", "saved_tokens", 1, ("a_out", "b_out")),
    "swap_in": ("restore_ms", "restore_client_ms", "restored_tokens", 1, ("a_in", "b_in")),
    "recompute": ("prefill_ms", "ttft_ms", "prefill_tokens", 2, ("c", "d", "e")),
}


def design(L: np.ndarray, degree: int) -> np.ndarray:
    return np.column_stack([L ** k for k in range(degree + 1)])   # 1, L, L^2


def fit(L, y, degree):
    coef, *_ = np.linalg.lstsq(design(L, degree), y, rcond=None)
    return coef


def metrics(y, pred) -> dict:
    res = y - pred
    return {"r2": float(1 - (res ** 2).sum() / ((y - y.mean()) ** 2).sum()),
            "rmse_ms": float(np.sqrt((res ** 2).mean())),
            "mape_pct": float(np.mean(np.abs(res) / y) * 100)}


def cross_validate(L, y, groups, degree, k=5, seed=2) -> dict:
    ids = np.unique(groups)
    fold_of = dict(zip(np.random.default_rng(seed).permutation(ids), np.arange(len(ids)) % k))
    folds = np.array([fold_of[g] for g in groups])
    pred = np.empty_like(y)
    for f in range(k):
        tr, te = folds != f, folds == f
        pred[te] = design(L[te], degree) @ fit(L[tr], y[tr], degree)
    return {f"cv_{m}": v for m, v in metrics(y, pred).items()}


def to_params(coef, names, degree) -> dict:
    # coef is [intercept, slope, (quadratic)]; return in the user's naming
    if degree == 1:
        return {names[0]: float(coef[1]), names[1]: float(coef[0])}
    return {names[0]: float(coef[0]), names[1]: float(coef[1]), names[2]: float(coef[2])}


def contention_features(df: pd.DataFrame) -> pd.DataFrame:
    """For every successful request, what the other workers were doing during its prefill.

    Phase windows per row, from the recorded timings (seconds since the repetition started):
      prefill [t_start, t_start + ttft], decode [t_start + ttft, t_start + request_s].
    For the prefill window of row i, over the other rows j of the same repetition:
      n_dec   sum of the fraction of the window that j spent decoding
      n_pre   same for prefilling
      dec_ctx sum of (fraction decoding) x (j's prompt + half its output): the context those decoders attend over
    """
    d = df.copy()
    d["pre0"], d["pre1"] = d.t_start, d.t_start + d.ttft_ms / 1e3
    d["gen1"] = d.t_start + d.request_s
    rows = []
    for i, r in d[d.error.isna()].iterrows():
        o = d[(d.index != i) & (d.rep == r.rep)]
        a, b = r.pre0, r.pre1
        span = max(b - a, 1e-6)
        ov = lambda s, e: ((np.minimum(e, b) - np.maximum(s, a)).clip(lower=0) / span)
        dec, pre = ov(o.pre1, o.gen1), ov(o.pre0, o.pre1)
        rows.append({"idx": i, "n_dec": dec.sum(), "n_pre": pre.sum(),
                     "dec_ctx": (dec * (o.L_prompt + o.n_gen / 2)).sum()})
    return pd.DataFrame(rows).set_index("idx")


def policy_models(ok: pd.DataFrame, swap_ok: pd.DataFrame, report: list) -> dict:
    """The richer models the KV policy uses.

    swap_out_split  T_out = b + a_p * P + a_g * G      P = prompt (prefilled, contiguous) tokens, G = generated tokens
    swap_in_split   T_in  = b + a_p * P + a_g * G
    recompute_contention
                    T_rc  = c + d * L + f_dec * L * n_dec + f_pre * L * n_pre + g * dec_ctx
    """
    out = {}

    def ols(name, y, cols, names, groups):
        X = np.column_stack([np.ones(len(y))] + cols)
        coef, *_ = np.linalg.lstsq(X, y, rcond=None)
        pred = X @ coef
        ids = np.unique(groups)
        fold_of = dict(zip(np.random.default_rng(2).permutation(ids), np.arange(len(ids)) % 5))
        folds = np.array([fold_of[g] for g in groups])
        cv = np.empty_like(y)
        for f in range(5):
            tr, te = folds != f, folds == f
            c, *_ = np.linalg.lstsq(X[tr], y[tr], rcond=None)
            cv[te] = X[te] @ c
        out[name] = {"params": dict(zip(names, map(float, coef))), **metrics(y, pred),
                     **{f"cv_{k}": v for k, v in metrics(y, cv).items()}, "n": len(y)}
        r = out[name]
        report.append(f"- **{name}**: {r['params']} | R2 {r['r2']:.3f} MAPE {r['mape_pct']:.1f}% "
                      f"| CV R2 {r['cv_r2']:.3f} CV MAPE {r['cv_mape_pct']:.1f}% | n {r['n']}")

    P, G = swap_ok.L_prompt.to_numpy(float), swap_ok.n_gen.to_numpy(float)
    g = swap_ok.request_index.to_numpy()
    ols("swap_out_split", swap_ok.save_ms.to_numpy(float), [P, G], ("b", "a_p", "a_g"), g)
    ols("swap_in_split", swap_ok.restore_ms.to_numpy(float), [P, G], ("b", "a_p", "a_g"), g)

    f = contention_features(ok).reindex(ok.index)
    L = ok.prefill_tokens.to_numpy(float)
    ols("recompute_contention", ok.prefill_ms.to_numpy(float),
        [L, L * f.n_dec.to_numpy(), L * f.n_pre.to_numpy(), f.dec_ctx.to_numpy()],
        ("c", "d", "f_dec", "f_pre", "g"), ok.request_index.to_numpy())
    return out


def main():
    df = pd.read_csv(HERE / "data" / "measurements.csv")
    ok = df[df.error.isna()]
    swap_ok = ok[ok.get("swap_error", pd.Series(index=ok.index, dtype=object)).isna()] if "swap_error" in ok else ok
    out = {"units": {"time": "ms", "L": "tokens"},
           "data": {"measurements": len(df), "request_errors": int(df.error.notna().sum()),
                    "swap_errors": int(df.swap_error.notna().sum()) if "swap_error" in df else 0,
                    "requests": int(df.request_index.nunique()), "reps": int(df.rep.nunique()),
                    "kv_bytes_per_token": float((swap_ok.saved_bytes / swap_ok.saved_tokens).median()),
                    "restore_fully_reused": int((swap_ok.verify_prompt_n == 1).sum()), "restore_checked": int(swap_ok.verify_prompt_n.notna().sum())}}
    report = ["# Fit report", "", f"Data: {out['data']}", ""]
    fig, axes = plt.subplots(1, 3, figsize=(16, 4.6))

    for ax, (name, (col_srv, col_cli, col_L, degree, names)) in zip(axes, MODELS.items()):
        d = (swap_ok if name.startswith("swap") else ok).dropna(subset=[col_srv, col_cli, col_L])
        L = d[col_L].to_numpy(float)
        groups = d.request_index.to_numpy()
        out[name] = {}
        for variant, col in (("server", col_srv), ("client", col_cli)):
            y = d[col].to_numpy(float)
            coef = fit(L, y, degree)
            pred = design(L, degree) @ coef
            out[name][variant] = {"params": to_params(coef, names, degree), **metrics(y, pred),
                                  **cross_validate(L, y, groups, degree), "n": len(y),
                                  "L_range": [int(L.min()), int(L.max())]}
            r = out[name][variant]
            report.append(f"- **{name} / {variant}**: {r['params']} | R2 {r['r2']:.3f} RMSE {r['rmse_ms']:.1f} ms "
                          f"MAPE {r['mape_pct']:.1f}% | CV R2 {r['cv_r2']:.3f} CV MAPE {r['cv_mape_pct']:.1f}% | n {r['n']}")
            ax.scatter(L, y, s=8, alpha=0.45, label=f"{variant} measured")
            grid = np.linspace(0, L.max(), 200)
            ax.plot(grid, design(grid, degree) @ coef, lw=2, label=f"{variant} fit")
        ax.set(title=name, xlabel="L (tokens)", ylabel="time (ms)")
        ax.legend(fontsize=8)

    # recompute with e fixed at 0 (used when the free fit gives e < 0, which turns over at large L)
    d = ok.dropna(subset=["prefill_ms", "prefill_tokens"])
    L, y = d.prefill_tokens.to_numpy(float), d.prefill_ms.to_numpy(float)
    coef = fit(L, y, 1)
    out["recompute"]["server_linear_e0"] = {"params": {"c": float(coef[0]), "d": float(coef[1]), "e": 0.0},
                                            **metrics(y, design(L, 1) @ coef),
                                            **cross_validate(L, y, d.request_index.to_numpy(), 1)}
    r = out["recompute"]["server_linear_e0"]
    report.append(f"- **recompute / server, e = 0**: {r['params']} | R2 {r['r2']:.3f} MAPE {r['mape_pct']:.1f}% "
                  f"| CV R2 {r['cv_r2']:.3f} CV MAPE {r['cv_mape_pct']:.1f}%")

    # no-contention reference: full prefills from the solo evict run on the same 50 tasks, if present
    solo = HERE.parent / "results" / "solo_SLO" / "smoke50_v1" / "evict" / "requests.csv"
    if solo.exists():
        s = pd.read_csv(solo)
        s = s[s.error.isna() & (s.cache_n == 0)]
        L, y = s.prompt_n.to_numpy(float), s.prompt_ms.to_numpy(float)
        coef = fit(L, y, 2)
        out["recompute_solo_reference"] = {"params": to_params(coef, ("c", "d", "e"), 2),
                                           **metrics(y, design(L, 2) @ coef), "n": len(y),
                                           "source": str(solo.relative_to(HERE.parent))}
        r = out["recompute_solo_reference"]
        report.append(f"- **recompute / solo reference (no contention)**: {r['params']} | R2 {r['r2']:.4f} "
                      f"MAPE {r['mape_pct']:.1f}% | n {r['n']}")
        axes[2].plot(np.linspace(0, L.max(), 200), design(np.linspace(0, L.max(), 200), 2) @ coef,
                     "k--", lw=1.5, label="solo (no contention) fit")
        axes[2].legend(fontsize=8)

    if "swap_out" in out:
        gbps = lambda a: out["data"]["kv_bytes_per_token"] / (a * 1e-3) / 1e9
        out["effective_bandwidth_GBps"] = {"swap_out": gbps(out["swap_out"]["server"]["params"]["a_out"]),
                                           "swap_in": gbps(out["swap_in"]["server"]["params"]["a_in"])}
        report.append(f"\nEffective bandwidth (server slope): {out['effective_bandwidth_GBps']}")

    report.append("\n## Policy models (split length, contention)\n")
    out["policy_models"] = policy_models(ok, swap_ok, report)

    fig.suptitle("KV swap and recompute cost under FIFO N4 (50-task requests)")
    fig.tight_layout()
    fig.savefig(HERE / "fit.png", dpi=130)
    (HERE / "params.json").write_text(json.dumps(out, indent=2))
    (HERE / "fit_report.md").write_text("\n".join(report) + "\n")
    print("\n".join(report))


if __name__ == "__main__":
    main()
