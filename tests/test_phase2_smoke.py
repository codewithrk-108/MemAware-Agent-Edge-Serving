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

    def test_age360_config_changes_only_aging_threshold(self):
        run = run_config("test", "trace.jsonl", 50, 4, 32768, 2,
                         arrival_rate=0.2)
        baseline = quota_config(run)
        age360 = quota_config(run, aging_threshold_s=360)
        self.assertEqual(age360.aging_threshold_s, 360)
        self.assertEqual(age360.max_consecutive_resume_priorities, 1)
        self.assertEqual(age360.resume_priority_head_age_limit_s, 0)
        self.assertEqual(
            {key: value for key, value in vars(age360).items() if key != "aging_threshold_s"},
            {key: value for key, value in vars(baseline).items() if key != "aging_threshold_s"},
        )

    def test_cost_aware_policy_settings(self):
        run = run_config("cost", "trace.jsonl", 50, 4, 32768, 2,
                         arrival_rate=0.2)
        config = quota_config(run)
        config.max_consecutive_resume_priorities = 0
        config.cost_aware_resume = True
        self.assertEqual(config.aging_threshold_s, 30)
        self.assertEqual(config.resume_queue_cost_weight, 0.25)

    def test_r4_workload_settings(self):
        run = run_config("r4", "trace.jsonl", 500, 4, 32768, 2,
                         arrival_rate=0.5)
        self.assertEqual(run.n_tasks, 500)
        self.assertEqual(run.concurrency, 4)
        self.assertEqual(run.arrival_rate, 0.5)
        self.assertEqual(run.server.ctx, 32768)

    def test_r5_age30_workload_and_policy_settings(self):
        run = run_config("r5", "trace.jsonl", 500, 16, 32768, 2,
                         arrival_rate=0.5)
        config = quota_config(run, aging_threshold_s=30)
        config.max_consecutive_resume_priorities = 0
        config.cost_aware_resume = True
        self.assertEqual(config.run.n_tasks, 500)
        self.assertEqual(config.run.concurrency, 16)
        self.assertEqual(config.run.arrival_rate, 0.5)
        self.assertEqual(config.aging_threshold_s, 30)
        self.assertEqual(config.run.server.ctx, 32768)

    def test_r5_age360_changes_only_aging_threshold(self):
        run = run_config("r5", "trace.jsonl", 500, 16, 32768, 2,
                         arrival_rate=0.5)
        age30 = quota_config(run, aging_threshold_s=30)
        age360 = quota_config(run, aging_threshold_s=360)
        for config in (age30, age360):
            config.max_consecutive_resume_priorities = 0
            config.cost_aware_resume = True
        self.assertEqual(age360.aging_threshold_s, 360)
        self.assertEqual(
            {key: value for key, value in vars(age360).items() if key != "aging_threshold_s"},
            {key: value for key, value in vars(age30).items() if key != "aging_threshold_s"},
        )


if __name__ == "__main__":
    unittest.main()
