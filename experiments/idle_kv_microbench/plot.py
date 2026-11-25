"""Figure for the idle-KV microbenchmark: time per token vs idle cells, and the count-vs-range test."""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

HERE = Path(__file__).resolve().parent
d = pd.read_csv(HERE / "data" / "measurements.csv")
r = pd.read_csv(HERE / "data" / "range_measurements.csv")
fig, (a, b) = plt.subplots(1, 2, figsize=(13, 4.6), gridspec_kw={"width_ratios": [1.6, 1]})
for (B, P, o), s in d.groupby(["B", "P", "order"]):
    m = s.groupby("X").tpot_ms.median()
    a.plot(m.index / 1024, m.values, marker="o" if o == "active_first" else "x",
           ls="-" if o == "active_first" else "--", label=f"{B} active, {P // 1024}K context, {o.replace('_', ' ')}")
a.set(xlabel="idle cells in the pool (K)", ylabel="time per output token (ms)",
      title="Decode slows linearly with idle KV (~0.45 ms per 1K cells)")
a.grid(alpha=0.3)
a.legend(fontsize=7.5)
order = ["none", "keep_low", "keep_top", "drop_top", "full"]
m = r.groupby("case").agg(tpot=("tpot_ms", "median"), kv=("kv_used_before", "median")).loc[order]
bars = b.bar(range(len(order)), m.tpot, color=["#888", "#4a8", "#c44", "#c84", "#c44"])
b.set_xticks(range(len(order)), [f"{c}\n{k / 1024:.1f}K cells" for c, k in zip(order, m.kv)], fontsize=8)
b.set(ylabel="time per output token (ms)", title="Same 5.1K cells: slow if they reach the top of the pool")
b.grid(axis="y", alpha=0.3)
fig.tight_layout()
fig.savefig(HERE / "idle_kv.png", dpi=130)
