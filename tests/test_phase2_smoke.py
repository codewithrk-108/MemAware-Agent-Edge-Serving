import unittest

from phase2.smoke import run_config


class Phase2SmokeConfigTests(unittest.TestCase):
    def test_poisson_config_preserves_requested_arrival_rate(self):
        config = run_config("poisson", "trace.jsonl", 50, 4, 32768, 2,
                            arrival_rate=0.2)
        self.assertEqual(config.arrival_rate, 0.2)
        self.assertEqual(config.concurrency, 4)
        self.assertEqual(config.server.ctx, 32768)
        self.assertEqual(config.seed, 2)

    def test_burst_remains_default(self):
        config = run_config("burst", "trace.jsonl", 50, 4, 32768, 2)
        self.assertEqual(config.arrival_rate, 0.0)


if __name__ == "__main__":
    unittest.main()
