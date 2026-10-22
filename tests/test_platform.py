import os
import sys
import unittest
from unittest.mock import patch

from harness import memory, platform
from harness.server import _BREAKDOWN

MiB = memory.MiB

METAL_LOG = """load_tensors:  MTL0_Mapped model buffer size =  1252.41 MiB
load_tensors:   CPU_Mapped model buffer size =   266.16 MiB
llama_kv_cache: size =  256.00 MiB (  8192 cells
sched_reserve:       MTL0 compute buffer size =    72.01 MiB
sched_reserve:        CPU compute buffer size =    24.02 MiB"""

CUDA_TWO_GPU_LOG = """load_tensors:        CUDA0 model buffer size =   600.00 MiB
load_tensors:        CUDA1 model buffer size =   652.41 MiB
load_tensors:   CPU_Mapped model buffer size =   266.16 MiB
llama_kv_cache: size =  256.00 MiB (  8192 cells
sched_reserve:      CUDA0 compute buffer size =    80.00 MiB
sched_reserve:  CUDA_Host compute buffer size =    20.00 MiB"""


def breakdown(text):
    return {k: sum({d: float(v) for d, v in rx.findall(text)}.values()) for k, rx in _BREAKDOWN.items()}


class PlatformTests(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("AES_PLATFORM", None)

    def test_default_is_macos(self):
        os.environ.pop("AES_PLATFORM", None)
        self.assertEqual(platform.current(), "macos")

    def test_wrong_os_is_rejected(self):
        wrong = "linux-cuda" if sys.platform == "darwin" else "macos"
        with self.assertRaises(SystemExit):
            platform.set_platform(wrong)

    def test_metal_breakdown(self):
        b = breakdown(METAL_LOG)
        self.assertAlmostEqual(b["model_gpu_mib"], 1252.41)
        self.assertAlmostEqual(b["model_cpu_mib"], 266.16)
        self.assertAlmostEqual(b["kv_mib"], 256.0)

    def test_cuda_breakdown_sums_gpus(self):
        b = breakdown(CUDA_TWO_GPU_LOG)
        self.assertAlmostEqual(b["model_gpu_mib"], 1252.41)
        self.assertAlmostEqual(b["compute_gpu_mib"], 80.0)
        self.assertAlmostEqual(b["compute_cpu_mib"], 20.0)

    def test_linux_cuda_total_is_host_plus_gpu(self):
        os.environ["AES_PLATFORM"] = "linux-cuda"
        with patch.object(memory, "_vm_hwm", return_value=900 * MiB), \
             patch.object(memory, "_nvml_used", return_value=(3000 * MiB, True, 7000 * MiB)):
            s = memory.sample(os.getpid())
        self.assertEqual(s.gpu, 3000 * MiB)
        self.assertEqual(s.footprint, s.rss + s.gpu)
        self.assertEqual(memory.gpu_scope(), "process")

    def test_linux_cuda_falls_back_to_device_usage(self):
        os.environ["AES_PLATFORM"] = "linux-cuda"
        with patch.object(memory, "_vm_hwm", return_value=0), \
             patch.object(memory, "_nvml_used", return_value=(0, False, 7000 * MiB)):
            s = memory.sample(os.getpid())
        self.assertEqual(s.gpu, 7000 * MiB)
        self.assertEqual(memory.gpu_scope(), "device")


if __name__ == "__main__":
    unittest.main()
