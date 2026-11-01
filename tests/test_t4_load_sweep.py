import unittest

from phase1.sweep import grid


class T4LoadSweepTest(unittest.TestCase):
    def test_full_grid_matches_seven_approved_configs_and_split(self):
        groups = grid()
        a, b = groups['full500_t4_part_a'], groups['full500_t4_part_b']
        self.assertEqual([(c.concurrency, c.arrival_rate, c.server.ctx) for c in a],
                         [(1, 0.1, 32768), (4, 0.2, 32768), (16, 0.2, 32768)])
        self.assertEqual([(c.concurrency, c.arrival_rate, c.server.ctx) for c in b],
                         [(4, 0.5, 32768), (4, 0, 32768), (16, 0.5, 65536), (16, 0.5, 32768)])
        self.assertEqual(len({c.name for c in a + b}), 7)
        for cfg in a + b:
            self.assertEqual(cfg.trace, 'data/traces/agenttrace_full_500_seed2.jsonl')
            self.assertEqual((cfg.n_tasks, cfg.seed, cfg.delay_scale), (500, 2, 1))
            self.assertEqual(cfg.scheduler, 'request')
            self.assertTrue(cfg.require_kv_metrics)
            self.assertTrue(cfg.cache_prompt)
            self.assertEqual(cfg.server.cache_ram, 0)
            self.assertEqual((cfg.deadline_s, cfg.request_timeout_s), (21600, 1800))

    def test_2s_64k_smoke_changes_only_arrival_rate(self):
        from dataclasses import asdict
        groups = grid()
        reference = asdict(groups['smoke64k_t4_n16'][0])
        faster = asdict(groups['smoke64k_poisson2s_t4_n16'][0])
        self.assertEqual(faster.pop('arrival_rate'), 0.5)
        reference.pop('arrival_rate')
        faster.pop('name')
        reference.pop('name')
        self.assertEqual(faster, reference)

    def test_64k_poisson_smokes_match_32k_workload(self):
        groups = grid()
        for n in (8, 16):
            configs = groups[f'smoke64k_t4_n{n}']
            self.assertEqual(len(configs), 1)
            cfg = configs[0]
            self.assertEqual((cfg.concurrency, cfg.server.ctx, cfg.arrival_rate), (n, 65536, 0.2))
            self.assertEqual((cfg.n_tasks, cfg.seed, cfg.delay_scale), (50, 2, 1))
            self.assertEqual(cfg.trace, 'data/traces/agenttrace_50_seed2.jsonl')
            self.assertTrue(cfg.require_kv_metrics)
            self.assertEqual(cfg.server.cache_ram, 0)

    def test_two_parts_cover_all_twelve_controlled_runs(self):
        groups = grid()
        parts = [groups['load_t4_part_a'], groups['load_t4_part_b']]
        self.assertEqual([len(p) for p in parts], [6, 6])
        self.assertEqual({c.concurrency for c in parts[0]}, {2, 8})
        self.assertEqual({c.concurrency for c in parts[1]}, {4, 16})
        configs = sum(parts, [])
        self.assertEqual({(c.concurrency, c.arrival_rate) for c in configs},
                         {(n, r) for n in (2, 4, 8, 16) for r in (0, 0.1, 0.2)})
        self.assertEqual(len({c.name for c in configs}), 12)
        for cfg in configs:
            with self.subTest(name=cfg.name):
                self.assertEqual(cfg.trace, 'data/traces/agenttrace_50_seed2.jsonl')
                self.assertEqual(cfg.n_tasks, 50)
                self.assertEqual(cfg.seed, 2)
                self.assertEqual(cfg.delay_scale, 1)
                self.assertEqual(cfg.server.ctx, 32768)
                self.assertEqual(cfg.server.cache_ram, 0)
                self.assertTrue(cfg.cache_prompt)
                self.assertTrue(cfg.require_kv_metrics)
                self.assertEqual(cfg.scheduler, 'request')


if __name__ == '__main__':
    unittest.main()
