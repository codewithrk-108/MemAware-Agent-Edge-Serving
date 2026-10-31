from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from phase1 import runner


class KvRunnerTest(unittest.TestCase):
    def test_required_metrics_abort_before_any_workload_is_replayed(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(runner, 'RESULTS', Path(tmp)), \
             patch.object(runner, 'Server'), \
             patch.object(runner, 'GpuMemSampler'), \
             patch.object(runner, 'KvMemSampler') as sampler, \
             patch.object(runner, 'run', new_callable=AsyncMock) as run:
            sampler.return_value.__enter__.return_value.samples = []
            sampler.return_value.__enter__.return_value.error = 'physical counters unavailable'
            with self.assertRaisesRegex(RuntimeError, 'physical counters unavailable'):
                runner.execute(runner.RunConfig(name='missing-kv', require_kv_metrics=True))
            run.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
