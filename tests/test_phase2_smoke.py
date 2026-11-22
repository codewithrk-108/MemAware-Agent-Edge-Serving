import unittest

from phase2.smoke import quota_config, run_config


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

    def test_quota_policy_has_no_resume_age_cutoff(self):
        run = run_config("quota", "trace.jsonl", 50, 4, 32768, 2)
        config = quota_config(run)
        self.assertEqual(config.cache_grace_s, 0.5)
        self.assertEqual(config.max_consecutive_resume_priorities, 1)
        self.assertEqual(config.resume_priority_head_age_limit_s, 0)
        self.assertEqual(config.eviction_policy, "slot_aware")


if __name__ == "__main__":
    unittest.main()
