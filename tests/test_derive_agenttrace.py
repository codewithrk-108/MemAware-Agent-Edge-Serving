"""The committed Phase 1 workloads must be reproducible from the committed source trace."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from workloads.derive_agenttrace import read_rows, sample, scale_delays, sha256, write_rows

ROOT = Path(__file__).resolve().parents[1]
SOURCE_150X = ROOT / "data/traces/agenttrace_all_tool_delay_150x_max10s.jsonl"
SMOKE_50 = ROOT / "data/traces/agenttrace_50_seed2.jsonl"


class DeriveTests(unittest.TestCase):
    def test_scale_caps_delays_and_keeps_nulls(self):
        rows = [{"task_id": "t", "cls": "mbpp", "source": "agenttrace",
                 "steps": [{"input": [1], "n_gen": 2, "tool_delay_s": 0.01},
                           {"input": [3], "n_gen": 4, "tool_delay_s": 1.0},
                           {"input": [5], "n_gen": 6, "tool_delay_s": None}]}]
        out, counts = scale_delays(rows, 150, 10.0)
        self.assertEqual([s["tool_delay_s"] for s in out[0]["steps"]], [0.01 * 150, 10.0, None])
        self.assertEqual(counts, {"tasks": 1, "model_requests": 3, "tool_calls": 2, "tool_calls_capped": 1})
        self.assertEqual(rows[0]["steps"][0]["tool_delay_s"], 0.01, "input rows must not be modified")

    def test_smoke_sample_matches_committed_file(self):
        chosen = sample(read_rows(SOURCE_150X), 50, 2)
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / "sample.jsonl"
            write_rows(out, chosen)
            self.assertEqual(sha256(out), sha256(SMOKE_50))


if __name__ == "__main__":
    unittest.main()
