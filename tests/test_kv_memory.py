import unittest
from unittest.mock import patch

from harness.kv_memory import KvMetricsUnavailable, KvMemSampler, parse_kv_metrics


class KvMemoryTest(unittest.TestCase):
    def metrics(self, used='1024', capacity='32768'):
        return (f'llamacpp:kv_cells_supported 1\n'
                f'llamacpp:kv_cells_used {used}\n'
                f'llamacpp:kv_cells_capacity {capacity}\n'
                'llamacpp:requests_processing 3\n')

    def test_reads_physical_cells_and_computes_fraction(self):
        result = parse_kv_metrics(self.metrics())
        self.assertEqual(result['used_cells'], 1024)
        self.assertEqual(result['capacity_cells'], 32768)
        self.assertEqual(result['usage_ratio'], 0.03125)
        self.assertEqual(result['requests_processing'], 3)

    def test_zero_occupied_cells_is_a_valid_measurement(self):
        self.assertEqual(parse_kv_metrics(self.metrics(used='0'))['usage_ratio'], 0)

    def test_unpatched_runtime_is_unavailable_not_zero(self):
        with self.assertRaises(KvMetricsUnavailable):
            parse_kv_metrics('llamacpp:n_tokens_max 12000\n')

    def test_unsupported_cache_is_unavailable(self):
        with self.assertRaises(KvMetricsUnavailable):
            parse_kv_metrics('llamacpp:kv_cells_supported 0\n')

    def test_invalid_counts_are_rejected(self):
        for used, capacity in [('NaN', '32768'), ('-1', '32768'), ('2', '1'),
                               ('1.5', '32768'), ('0', '0')]:
            with self.subTest(used=used, capacity=capacity):
                with self.assertRaises(ValueError):
                    parse_kv_metrics(self.metrics(used, capacity))

    @patch('harness.kv_memory.kv_memory_sample')
    def test_sampler_collects_initial_and_final_retained_occupancy(self, sample):
        sample.side_effect = [dict(t=1, used_cells=0, capacity_cells=32, usage_ratio=0),
                              dict(t=2, used_cells=16, capacity_cells=32, usage_ratio=0.5)]
        with KvMemSampler('http://localhost', interval=60) as sampler:
            self.assertEqual(sampler.samples[0]['used_cells'], 0)
        self.assertEqual(sampler.summary()['kv_peak_usage_ratio'], 0.5)
        self.assertEqual(sampler.summary()['kv_sample_count'], 2)

    @patch('harness.kv_memory.kv_memory_sample', side_effect=KvMetricsUnavailable('missing'))
    def test_sampler_reports_unavailable_without_fake_zero(self, sample):
        with KvMemSampler('http://localhost') as sampler:
            pass
        self.assertIsNone(sampler.summary()['kv_peak_used_cells'])
        self.assertEqual(sampler.summary()['kv_sample_count'], 0)
        self.assertEqual(sampler.summary()['kv_sampling_error_count'], 1)

    @patch('harness.kv_memory.kv_memory_sample')
    def test_transient_transport_error_does_not_disguise_missing_samples(self, sample):
        sample.side_effect = [OSError('connection reset'),
                              dict(t=2, used_cells=4, capacity_cells=32, usage_ratio=0.125)]
        with KvMemSampler('http://localhost', interval=60) as sampler:
            pass
        self.assertEqual(sampler.summary()['kv_peak_used_cells'], 4)
        self.assertEqual(sampler.summary()['kv_sampling_error_count'], 1)


if __name__ == '__main__':
    unittest.main()
