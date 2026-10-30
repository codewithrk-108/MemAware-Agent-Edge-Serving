"""Per-process memory measurement on macOS.

`phys_footprint` is what Activity Monitor reports as "Memory" and what the kernel uses for memory-pressure
accounting. Unlike RSS it includes Metal (GPU) allocations in unified memory and excludes clean file-backed pages.
The kernel also tracks the lifetime peak of the footprint, so peaks between our samples are not missed.
"""

import ctypes
import csv
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field

import psutil

_RUSAGE_INFO_V4 = 4


class _RusageInfoV4(ctypes.Structure):
    _fields_ = [("ri_uuid", ctypes.c_uint8 * 16)] + [
        (name, ctypes.c_uint64)
        for name in (
            "ri_user_time", "ri_system_time", "ri_pkg_idle_wkups", "ri_interrupt_wkups", "ri_pageins",
            "ri_wired_size", "ri_resident_size", "ri_phys_footprint", "ri_proc_start_abstime",
            "ri_proc_exit_abstime", "ri_child_user_time", "ri_child_system_time", "ri_child_pkg_idle_wkups",
            "ri_child_interrupt_wkups", "ri_child_pageins", "ri_child_elapsed_abstime", "ri_diskio_bytesread",
            "ri_diskio_byteswritten", "ri_cpu_time_qos_default", "ri_cpu_time_qos_maintenance",
            "ri_cpu_time_qos_background", "ri_cpu_time_qos_utility", "ri_cpu_time_qos_legacy",
            "ri_cpu_time_qos_user_initiated", "ri_cpu_time_qos_user_interactive", "ri_billed_system_time",
            "ri_serviced_system_time", "ri_logical_writes", "ri_lifetime_max_phys_footprint", "ri_instructions",
            "ri_cycles", "ri_billed_energy", "ri_serviced_energy", "ri_interval_max_phys_footprint",
            "ri_runnable_time",
        )
    ]


_libproc = ctypes.CDLL("/usr/lib/libproc.dylib") if sys.platform == "darwin" else None

MiB = 1024 * 1024


@dataclass
class MemSample:
    t: float               # seconds, monotonic
    footprint: int         # bytes
    peak_footprint: int    # bytes, kernel-tracked lifetime max
    rss: int               # bytes


def sample(pid: int) -> MemSample:
    rss = psutil.Process(pid).memory_info().rss
    if _libproc is None:
        return MemSample(time.monotonic(), rss, rss, rss)
    info = _RusageInfoV4()
    if _libproc.proc_pid_rusage(pid, _RUSAGE_INFO_V4, ctypes.byref(info)) != 0:
        raise OSError(f"proc_pid_rusage failed for pid {pid}")
    return MemSample(time.monotonic(), info.ri_phys_footprint, info.ri_lifetime_max_phys_footprint, rss)


@dataclass
class MemSampler:
    """Samples a process in a background thread."""
    pid: int
    interval: float = 0.05
    samples: list = field(default_factory=list)

    def __post_init__(self):
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            try:
                self.samples.append(sample(self.pid))
            except (psutil.NoSuchProcess, OSError):
                return
            self._stop.wait(self.interval)

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        self._thread.join()

    def window(self, t0: float, t1: float) -> list:
        return [s for s in self.samples if t0 <= s.t <= t1]

    def peak(self, t0: float = float("-inf"), t1: float = float("inf")) -> int:
        w = self.window(t0, t1)
        return max((s.footprint for s in w), default=0)


def gpu_memory_sample() -> dict:
    """Sample device-wide NVIDIA GPU 0 memory, in MiB (not per-process memory)."""
    result = subprocess.run(
        ["nvidia-smi", "--id=0", "--query-gpu=uuid,memory.used,memory.total",
         "--format=csv,noheader,nounits"],
        capture_output=True, text=True, check=True, timeout=5,
    )
    uuid, used, total = next(csv.reader(result.stdout.splitlines()))
    return {"t": time.monotonic(), "gpu_uuid": uuid.strip(), "used_mib": int(used.strip()),
            "total_mib": int(total.strip())}


@dataclass
class GpuMemSampler:
    """Sample GPU 0 once per interval, including server startup."""
    interval: float = 1.0
    samples: list = field(default_factory=list)
    error: str = ""

    def __post_init__(self):
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            try:
                self.samples.append(gpu_memory_sample())
            except (OSError, subprocess.SubprocessError, ValueError, StopIteration) as exc:
                self.error = f"{type(exc).__name__}: {exc}"
                return
            self._stop.wait(self.interval)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join()


if __name__ == "__main__":
    import os
    s = sample(os.getpid())
    print(f"self: footprint {s.footprint / MiB:.1f} MiB, peak {s.peak_footprint / MiB:.1f} MiB, rss {s.rss / MiB:.1f} MiB")
