"""Figures and a summary table for the microbenchmarks. Benchmarks without results are skipped."""

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
MICRO = ROOT / "results/micro"
FIGS = MICRO / "figures"


def load(name: str):
    f = MICRO / name / "raw.csv"
    return pd.read_csv(f) if f.exists() else None


def env(name: str) -> dict:
    f = MICRO / name / "meta.json"
    return json.loads(f.read_text()).get("env", {}) if f.exists() else {}


def savefig(fig, name: str):
    FIGS.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(FIGS / f"{name}.png", dpi=150)
    plt.close(fig)
    print(f"wrote figures/{name}.png")


def m1(lines: list):
    d = load("m1_memory")
    if d is None:
        return
    c = d[d.name.str.startswith("ctx")].sort_values("ctx")
    fig, ax = plt.subplots(1, 2, figsize=(11, 4))
    ax[0].plot(c.ctx, c.kv_mib, "k--", label="KV cache (runtime report)")
    ax[0].plot(c.ctx, c.idle_footprint_mib, "o-", label="footprint, idle")
    ax[0].plot(c.ctx, c.filled_footprint_mib, "s-", label="footprint, context full")
    ax[0].plot(c.ctx, c.idle_rss_mib, "^:", label="RSS, idle (incl. mapped weights)")
    ax[0].set(xscale="log", xlabel="context size -c (tokens)", ylabel="MiB", title="Memory vs context size (1 slot)")
    ax[0].set_xticks(c.ctx, [f"{v // 1024}k" for v in c.ctx])
    ax[0].minorticks_off()
    ax[0].legend(fontsize=8)
    o = d[~d.name.str.startswith("ctx")]
    ax[1].bar(o.name, o.filled_footprint_mib, label="footprint, filled")
    ax[1].bar(o.name, o.kv_mib, alpha=0.6, label="KV cache")
    ax[1].set(ylabel="MiB", title="Slots and load mode")
    ax[1].tick_params(axis="x", rotation=30)
    ax[1].legend(fontsize=8)
    savefig(fig, "m1_memory")
    kv_per_tok = (c.kv_mib / c.ctx).median() * 1024
    lines.append(f"| M1 | KV cost | {kv_per_tok:.0f} KiB/token, allocated at startup for the full -c |")
    lines.append(f"| M1 | footprint growth idle -> full context | {(c.filled_footprint_mib - c.idle_footprint_mib).max():.0f} MiB max |")


def m2(lines: list):
    d = load("m2_prefill")
    if d is None:
        return
    d = d[d.error.isna()]
    g = d.groupby("n_prompt").agg(ttft=("ttft_s", "median"), tps=("prefill_tok_s", "median")).reset_index()
    fig, ax = plt.subplots(1, 2, figsize=(11, 4))
    ax[0].plot(g.n_prompt, g.ttft * 1e3, "o-")
    ax[0].set(xscale="log", yscale="log", xlabel="prompt tokens", ylabel="TTFT (ms)", title="Prefill latency")
    ax[1].plot(g.n_prompt, g.tps, "o-")
    ax[1].set(xscale="log", xlabel="prompt tokens", ylabel="tokens/s", title="Prefill throughput")
    savefig(fig, "m2_prefill")
    lines.append(f"| M2 | prefill throughput | peak {g.tps.max():.0f} tok/s, {g.tps.iloc[-1]:.0f} tok/s at {g.n_prompt.iloc[-1]} tokens |")


def m3(lines: list):
    d = load("m3_concurrency")
    if d is None:
        return
    d = d[d.error.isna()]
    g = d.groupby(["prompt_len", "concurrency"]).agg(tpot=("tpot_ms", "median"), thru=("agg_gen_tok_s", "median"),
                                                      ttft=("ttft_s", "median")).reset_index()
    fig, ax = plt.subplots(1, 3, figsize=(14, 4))
    for L, gg in g.groupby("prompt_len"):
        ax[0].plot(gg.concurrency, gg.thru, "o-", label=f"context {L}")
        ax[1].plot(gg.concurrency, gg.tpot, "o-", label=f"context {L}")
        ax[2].plot(gg.concurrency, gg.ttft * 1e3, "o-", label=f"context {L}")
    ax[0].set(xscale="log", xlabel="concurrent requests", ylabel="generated tok/s", title="Aggregate throughput")
    ax[1].set(xscale="log", xlabel="concurrent requests", ylabel="ms/token", title="Time per output token (p50)")
    ax[2].set(xscale="log", yscale="log", xlabel="concurrent requests", ylabel="ms", title="TTFT (p50)")
    for a in ax:
        a.legend(fontsize=8)
    savefig(fig, "m3_concurrency")
    for L, gg in g.groupby("prompt_len"):
        b1, bm = gg.iloc[0], gg.loc[gg.thru.idxmax()]
        lines.append(f"| M3 | batching, context {L} | {b1.thru:.0f} tok/s at B=1 -> {bm.thru:.0f} tok/s at B={bm.concurrency:.0f} "
                     f"({bm.thru / b1.thru:.1f}x), TPOT {b1.tpot:.0f} -> {bm.tpot:.0f} ms |")


def m4(lines: list):
    d = load("m4_prefix_reuse")
    if d is None:
        return
    d = d[d.error.isna()]
    g = d.groupby(["context", "cache_prompt"]).ttft_s.median().unstack()
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.plot(g.index, g[True] * 1e3, "o-", label="prefix reused (cache_prompt)")
    ax.plot(g.index, g[False] * 1e3, "s-", label="full recompute")
    ax.set(xscale="log", yscale="log", xlabel="context before the step (tokens)", ylabel="next-step TTFT (ms)",
           title="Agent next step: reuse vs recompute")
    ax.legend(fontsize=8)
    savefig(fig, "m4_prefix_reuse")
    c = g.index.max()
    lines.append(f"| M4 | reuse speedup of next step | {g.loc[c, False] / g.loc[c, True]:.1f}x at {c} tokens, "
                 f"{g.iloc[0][False] / g.iloc[0][True]:.1f}x at {g.index.min()} tokens |")


def m5(lines: list):
    d = load("m5_retention")
    if d is None:
        return
    inter = d[(d.experiment == "inter") & (d.step > 0) & d.error.isna()]
    g = inter.groupby(["config", "n_sessions"]).agg(hit=("hit_ratio", "mean"), ttft=("ttft_s", "median")).reset_index()
    grow = d[d.experiment == "inter"].groupby(["config", "n_sessions"]).apply(
        lambda x: x.footprint_mib.max() - x.base_footprint_mib.iloc[0], include_groups=False).rename("grow").reset_index()
    g = g.merge(grow, on=["config", "n_sessions"])
    fig, ax = plt.subplots(1, 3, figsize=(15, 4))
    for cfg, gg in g.groupby("config"):
        ax[0].plot(gg.n_sessions, gg.hit, "o-", label=cfg)
        ax[1].plot(gg.n_sessions, gg.ttft * 1e3, "o-", label=cfg)
        ax[2].plot(gg.n_sessions, gg.grow, "o-", label=cfg)
    slots = json.loads((MICRO / "m5_retention/meta.json").read_text()).get("slots", 4)
    for a in ax:
        a.axvline(slots, color="grey", ls=":", lw=1)
        a.set(xscale="log", xlabel="interleaved agent sessions")
    ax[0].set(ylabel="prompt tokens reused", title=f"Context retained across idle steps ({slots} slots)")
    ax[1].set(yscale="log", ylabel="ms", title="Next-step TTFT (p50)")
    ax[2].set(ylabel="MiB", title="Footprint growth (host prompt cache)")
    ax[0].legend(fontsize=7)
    savefig(fig, "m5_retention")
    for cfg, gg in g.groupby("config"):
        top = gg.loc[gg.n_sessions.idxmax()]
        lines.append(f"| M5 | {cfg}, {top.n_sessions:.0f} sessions | hit {top.hit:.2f}, TTFT {top.ttft * 1e3:.0f} ms, +{top.grow:.0f} MiB |")
    gap = d[(d.experiment == "gap") & (d.step > 0) & d.error.isna()]
    for (cfg, gp), gg in gap.groupby(["config", "gap_s"]):
        lines.append(f"| M5 | idle gap {gp:.0f} s, {cfg} | hit {gg.hit_ratio.mean():.2f} |")


def m6(lines: list):
    d = load("m6_capacity")
    if d is None:
        return
    order = ["ok", "truncated", "rejected_context", "timeout", "other"]
    fig, axes = plt.subplots(1, 2, figsize=(13, 4), sharey=True)
    for ax, (mode, dm) in zip(axes, d.groupby("mode")):
        t = dm.groupby(["prompt_len", "concurrency"]).outcome.value_counts().unstack(fill_value=0)
        t = t.reindex(columns=[o for o in order if o in t.columns])
        (t.div(t.sum(axis=1), axis=0)).plot.bar(stacked=True, ax=ax, width=0.85)
        ax.set(title=f"{mode} KV pool", xlabel="(prompt tokens, concurrent requests)", ylabel="share of requests")
        ax.legend(fontsize=7)
    savefig(fig, "m6_capacity")
    for mode, dm in d.groupby("mode"):
        fail = dm[dm.outcome != "ok"]
        kinds = ", ".join(f"{k} {v}" for k, v in fail.outcome.value_counts().items()) or "none"
        lines.append(f"| M6 | {mode}: failed requests | {len(fail)}/{len(dm)} ({kinds}) |")


def main():
    lines = ["| bench | metric | result |", "|---|---|---|"]
    for f in (m1, m2, m3, m4, m5, m6):
        try:
            f(lines)
        except Exception as e:  # a partial result set should still produce the other figures
            print(f"{f.__name__}: skipped ({type(e).__name__}: {e})")
    e = next((env(n) for n in ("m1_memory", "m2_prefill", "m3_concurrency") if env(n)), {})
    head = (f"Machine: {e.get('machine')} ({e.get('memory_gib')} GiB), power {e.get('power', '?')}, "
            f"llama.cpp {e.get('llama_cpp_commit')}, model {e.get('model')}\n\n")
    (MICRO / "summary.md").write_text("# Microbenchmark summary\n\n" + head + "\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
