import unittest

from phase1.sweep import deduplicate_configs, grid


class AgentTraceSweepTest(unittest.TestCase):
    def test_agenttrace_configs_use_controlled_workload_and_aws_limits(self):
        configs = [cfg for name, group in grid().items() if name.endswith('_agenttrace') for cfg in group]
        self.assertTrue(configs)
        for cfg in configs:
            with self.subTest(name=cfg.name):
                self.assertEqual(cfg.trace, 'data/traces/agenttrace_50_seed2.jsonl')
                self.assertEqual(cfg.n_tasks, 50)
                self.assertGreater(cfg.arrival_rate, 0)
                self.assertLessEqual(cfg.arrival_rate, 0.1)
                self.assertEqual(cfg.server.ctx, 32768)
                self.assertGreaterEqual(cfg.deadline_s, 14400)
                self.assertGreaterEqual(cfg.request_timeout_s, 900)

    def test_concurrency_comparison_uses_same_arrival_schedule(self):
        configs = grid()['concurrency_agenttrace']
        self.assertEqual([cfg.concurrency for cfg in configs], [1, 2, 4, 8])
        self.assertEqual(len({(cfg.seed, cfg.n_tasks, cfg.arrival_rate) for cfg in configs}), 1)
        self.assertTrue(all(cfg.arrival_rate == 0.025 for cfg in configs))

    def test_arrival_comparison_spans_thirty_to_thirty_five_minutes(self):
        configs = grid()['arrival_agenttrace']
        self.assertEqual([cfg.arrival_rate for cfg in configs], [1 / 38, 1 / 40, 1 / 42])
        self.assertEqual(len({(cfg.seed, cfg.n_tasks, cfg.concurrency) for cfg in configs}), 1)
        import numpy as np
        for cfg in configs:
            rng = np.random.default_rng(cfg.seed)
            rng.choice(cfg.n_tasks, size=cfg.n_tasks, replace=False)
            gaps = rng.exponential(1 / cfg.arrival_rate, size=cfg.n_tasks)
            gaps[0] = 0
            self.assertGreaterEqual(gaps.sum(), 30 * 60)
            self.assertLessEqual(gaps.sum(), 35 * 60)

    def test_starting_dataset_matches_seeded_selection(self):
        import json
        from pathlib import Path
        import numpy as np
        source = [json.loads(s) for s in Path('data/traces/agenttrace_all_tool_delay_150x_max10s.jsonl').read_text().splitlines()]
        selected = [json.loads(s) for s in Path('data/traces/agenttrace_50_seed2.jsonl').read_text().splitlines()]
        indices = np.random.default_rng(2).choice(len(source), size=50, replace=False)
        self.assertEqual(selected, [source[i] for i in indices])
        self.assertEqual(len({t['task_id'] for t in selected}), 50)

    def test_full_agenttrace_sweep_reuses_identical_configs(self):
        groups = grid()
        configs = [c for n, group in groups.items() if n.endswith('_agenttrace') for c in group]
        unique, aliases = deduplicate_configs(configs)
        self.assertEqual(len(configs), 18)
        self.assertEqual(len(unique), 11)
        self.assertEqual(len(aliases), 18)
        for cfg in unique:
            self.assertEqual(aliases[cfg.name], cfg.name)


if __name__ == '__main__':
    unittest.main()
