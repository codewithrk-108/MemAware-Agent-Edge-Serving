import unittest

from phase1.sweep import grid


class T4LoadSweepTest(unittest.TestCase):
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
