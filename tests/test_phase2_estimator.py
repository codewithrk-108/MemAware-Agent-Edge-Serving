import tempfile
import unittest
from pathlib import Path

import pandas as pd

from phase2.estimator import RuntimeEstimator, fit_runtime_estimator


class RuntimeEstimatorTests(unittest.TestCase):
    def test_fit_predict_and_round_trip(self):
        rows = []
        for prompt, output in ((100, 10), (200, 20), (300, 30), (400, 40)):
            rows.append({"n_prompt": prompt, "cache_n": 0, "n_predict_req": output,
                         "e2e_s": 1 + 0.002 * prompt + 0.01 * output, "error": None})
        estimator = fit_runtime_estimator(pd.DataFrame(rows), ("requests.csv",), "abc")
        predicted, fallback = estimator.predict(250, 25)
        self.assertAlmostEqual(predicted, 1.75, places=6)
        self.assertFalse(fallback)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "model.json"
            estimator.save(path)
            self.assertEqual(RuntimeEstimator.load(path), estimator)

    def test_cached_tokens_reduce_prefill_prediction(self):
        estimator = RuntimeEstimator(1, 0.01, 0.1, 99)
        full, _ = estimator.predict(100, 10)
        cached, _ = estimator.predict(100, 10, cached_prompt_tokens=80)
        self.assertAlmostEqual(full - cached, 0.8)

    def test_invalid_input_uses_fallback(self):
        estimator = RuntimeEstimator(1, 0.01, 0.1, 99)
        self.assertEqual(estimator.predict(-1, 10), (99, True))

    def test_fit_rejects_insufficient_data(self):
        frame = pd.DataFrame([{"n_prompt": 10, "n_predict_req": 1, "e2e_s": 1}])
        with self.assertRaisesRegex(ValueError, "at least three"):
            fit_runtime_estimator(frame)


if __name__ == "__main__":
    unittest.main()

