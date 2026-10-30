import subprocess
import unittest
from unittest.mock import patch

from harness.memory import gpu_memory_sample


class GpuMemoryTest(unittest.TestCase):
    @patch('harness.memory.subprocess.run')
    def test_reads_device_wide_nvidia_memory(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, 'GPU-abc, 4489, 6144\n', '')
        sample = gpu_memory_sample()
        self.assertEqual(sample['gpu_uuid'], 'GPU-abc')
        self.assertEqual(sample['used_mib'], 4489)
        self.assertEqual(sample['total_mib'], 6144)

    @patch('harness.memory.subprocess.run', side_effect=FileNotFoundError())
    def test_missing_nvidia_smi_is_reported(self, run):
        with self.assertRaises(FileNotFoundError):
            gpu_memory_sample()


if __name__ == '__main__':
    unittest.main()
