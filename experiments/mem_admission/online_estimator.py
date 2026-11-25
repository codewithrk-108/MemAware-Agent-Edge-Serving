"""Experiment 7: output-length estimator learned online, inside the run, from completed requests only.

Used by scheduler.py with --order online_median (E7a) or --order online_gbm (E7b). Nothing is loaded from outside the
run: no lookup table, no offline data. The estimator learns from a request (one agent step) only after it has
completed, using the number of tokens the server actually generated for it, and the features the request had when it
became ready. Until MIN_FIT steps have completed there is no model and the scheduler orders by need (as Exp 4).
After that it refits every REFIT completions.

Features of a request when it becomes ready (all known to a server at that moment):
  cls          1 for mbpp, 0 for nl2bash (the task's type)
  sb           step index capped at 2 (first, second, third or later)
  prev         generated length of this task's previous step (-1 for a first step)
  meanprev     mean generated length of this task's previous steps (-1 for a first step)
  err          1 if the tool result just appended contains "Error" or "Traceback"
  toollen      characters of that tool result
  hist         tokens in the history (prompt) at that moment
  task_chars   characters of the task text
  n_tests      number of "assert" test cases in the task text
E7a (online_median): median completed length per (cls, sb), overall completed median for an unseen cell.
E7b (online_gbm): gradient-boosted trees on log length over all features (scikit-learn; run with /opt/pytorch).
"""

import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
READABLE = ROOT / "data/source/agenttrace_readable.jsonl"
FEATURES = ["cls", "sb", "prev", "meanprev", "err", "toollen", "hist", "task_chars", "n_tests"]
MIN_FIT, REFIT = 30, 20


class OnlineEstimator:
    def __init__(self, kind: str):
        assert kind in ("online_median", "online_gbm")
        self.kind = kind
        self.readable = {r["task_id"]: r for r in map(json.loads, READABLE.open())}
        self.X, self.y = [], []
        self.fitted_n, self.refits = 0, 0
        self.model = None                     # median table (dict) or fitted regressor
        self.overall = None

    def features(self, task_id: str, cls: str, step: int, prev_lens: list, hist_tokens: int) -> dict:
        r = self.readable[task_id]
        res = (r["steps"][step - 1]["tool"] or {}).get("result_text", "") if step else ""
        txt = r["initial_prompt"]
        return {"cls": int(cls == "mbpp"), "sb": min(step, 2), "prev": prev_lens[-1] if prev_lens else -1,
                "meanprev": float(np.mean(prev_lens)) if prev_lens else -1.0,
                "err": int(("Error" in res) or ("Traceback" in res)), "toollen": len(res), "hist": hist_tokens,
                "task_chars": len(txt), "n_tests": txt.count("assert")}

    @property
    def ready(self) -> bool:
        return self.model is not None

    def observe(self, feat: dict, generated: int) -> bool:
        """Add one completed request. Returns True if the model was refitted."""
        self.X.append([feat[f] for f in FEATURES])
        self.y.append(max(int(generated), 1))
        n = len(self.y)
        if n < MIN_FIT or (self.fitted_n and n - self.fitted_n < REFIT):
            return False
        X, y = np.asarray(self.X, float), np.asarray(self.y, float)
        self.overall = float(np.median(y))
        if self.kind == "online_median":
            table = {}
            for key in {(int(a), int(b)) for a, b in X[:, :2]}:
                m = (X[:, 0] == key[0]) & (X[:, 1] == key[1])
                table[key] = float(np.median(y[m]))
            self.model = table
        else:
            from sklearn.ensemble import HistGradientBoostingRegressor
            self.model = HistGradientBoostingRegressor(max_iter=200, learning_rate=0.05,
                                                       min_samples_leaf=min(30, max(5, n // 10)),
                                                       random_state=0).fit(X, np.log(y))
        self.fitted_n, self.refits = n, self.refits + 1
        return True

    def predict(self, feat: dict) -> float:
        if self.kind == "online_median":
            return self.model.get((feat["cls"], feat["sb"]), self.overall)
        return float(np.exp(self.model.predict(np.asarray([[feat[f] for f in FEATURES]], float))[0]))
