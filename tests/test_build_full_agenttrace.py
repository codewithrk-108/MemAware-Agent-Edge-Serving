import collections
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from workloads.build_full_agenttrace import select_tasks, build, problem_key


SOURCE = Path('data/traces/agenttrace_all_tool_delay_150x_max10s.jsonl')


class FullAgentTraceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = [json.loads(line) for line in SOURCE.read_text().splitlines()]

    def test_selects_exact_step_class_quotas_without_duplicate_problems(self):
        selected = select_tasks(self.rows, seed=2)
        self.assertEqual(len(selected), 500)
        self.assertEqual(len({r['task_id'] for r in selected}), 500)
        self.assertEqual(len({problem_key(r) for r in selected}), 500)
        self.assertEqual(collections.Counter(r['cls'] for r in selected), {'mbpp': 350, 'nl2bash': 150})
        bucket = lambda r: str(len(r['steps'])) if len(r['steps']) < 4 else '4-5' if len(r['steps']) < 6 else '6-11'
        self.assertEqual(collections.Counter(bucket(r) for r in selected),
                         {'1': 165, '2': 230, '3': 60, '4-5': 28, '6-11': 17})
        original = {r['task_id']: r for r in self.rows}
        self.assertTrue(all(r == original[r['task_id']] for r in selected))
        self.assertEqual(selected, select_tasks(self.rows, seed=2))

    def test_writes_replayable_file_and_verifiable_manifest(self):
        from workloads.trace import load
        before = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'full.jsonl'
            manifest = build(SOURCE, out, seed=2)
            tasks = load(out)
            self.assertEqual(len(tasks), 500)
            self.assertEqual(manifest['dataset_sha256'], hashlib.sha256(out.read_bytes()).hexdigest())
            self.assertEqual(manifest['source_sha256'], before)
            self.assertEqual(manifest['counts']['model_requests'], sum(t.n_steps for t in tasks))
            self.assertGreater(manifest['counts']['tool_calls'], 0)
            self.assertEqual(len(manifest['selection']['task_ids']), 500)
            self.assertTrue(all(t.total_tokens <= 16384 for t in tasks))
            self.assertTrue(any(len(t.steps) > 3 for t in tasks))
            self.assertTrue(any(t.steps[0].n_gen < 512 for t in tasks))
            self.assertTrue(any(s.n_gen >= 4096 for t in tasks for s in t.steps))
            for task in tasks:
                self.assertIsNone(task.steps[-1].tool_delay_s)
                self.assertTrue(all(0 <= s.tool_delay_s <= 10 for s in task.steps[:-1]))
        self.assertEqual(hashlib.sha256(SOURCE.read_bytes()).hexdigest(), before)

    def test_refuses_to_overwrite_source(self):
        with self.assertRaises(ValueError):
            build(SOURCE, SOURCE, seed=2)
