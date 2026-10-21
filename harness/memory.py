"""Per-process memory measurement, per platform (see harness/platform.py).

macos: `phys_footprint` is what Activity Monitor reports as "Memory" and what the kernel uses for memory-pressure
accounting. Unlike RSS it includes Metal (GPU) allocations in unified memory and excludes clean file-backed pages.
The kernel also tracks the lifetime peak, so peaks between samples are not missed.

linux-cuda: host and GPU memory are separate. host = RSS (peak from the kernel's VmHWM), gpu = the process's
memory on all NVIDIA GPUs from NVML (nvidia-smi as fallback). footprint = host + gpu, so both platforms report
one comparable total. If NVML cannot see the server's pid (pid namespaces in containers), the whole-device usage
is used instead and `gpu_scope()` returns "device": keep the GPU otherwise idle in that case.
"""

import ctypes
import subprocess
import threading
import time
from dataclasses import dataclass, field

import psutil

from . import platform

MiB = 1024 * 1024


@dataclass
class MemSample:
    t: float               # seconds, monotonic
    footprint: int         # bytes: macOS phys_footprint, linux host RSS + GPU
    peak_footprint: int    # bytes, lifetime peak (kernel-tracked part where the OS offers it)
    rss: int               # bytes, host resident set
    gpu: int = 0           # bytes, NVIDIA GPU memory of the process (linux-cuda only)


# --- macOS -----------------------------------------------------------------------------------------------------

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


_libproc = None


def _sample_macos(pid: int) -> MemSample:
    global _libproc
    if _libproc is None:
        _libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
    rss = psutil.Process(pid).memory_info().rss
    info = _RusageInfoV4()
    if _libproc.proc_pid_rusage(pid, _RUSAGE_INFO_V4, ctypes.byref(info)) != 0:
        raise OSError(f"proc_pid_rusage failed for pid {pid}")
    return MemSample(time.monotonic(), info.ri_phys_footprint, info.ri_lifetime_max_phys_footprint, rss)


# --- Linux + NVIDIA ----------------------------------------------------------------------------------------------

_nvml = None          # module, or False if unavailable
_gpu_scope = "process"
_gpu_peak: dict = {}  # pid -> max GPU bytes seen, NVML has no per-process peak


def gpu_scope() -> str:
    return _gpu_scope


def _nvml_used(pid: int):
    global _nvml
    if _nvml is None:
        try:
            import pynvml
            pynvml.nvmlInit()
            _nvml = pynvml
        except Exception:
            _nvml = False
    if not _nvml:
        return None
    n = _nvml
    total, found, device_used = 0, False, 0
    for i in range(n.nvmlDeviceGetCount()):
        h = n.nvmlDeviceGetHandleByIndex(i)
        device_used += n.nvmlDeviceGetMemoryInfo(h).used
        for p in n.nvmlDeviceGetComputeRunningProcesses(h):
            if p.pid == pid:
                total += p.usedGpuMemory or 0
                found = True
    return total, found, device_used


def _smi_used(pid: int):
    try:
        out = subprocess.check_output(["nvidia-smi", "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"],
                                      text=True, timeout=5)
        dev = subprocess.check_output(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                                      text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return 0, False, 0
    total, found = 0, False
    for line in out.strip().splitlines():
        p, used = [x.strip() for x in line.split(",")]
        if p.isdigit() and int(p) == pid:
            total += int(used) * MiB
            found = True
    return total, found, sum(int(x) for x in dev.split()) * MiB


def _vm_hwm(pid: int) -> int:
    with open(f"/proc/{pid}/status") as f:
        for line in f:
            if line.startswith("VmHWM:"):
                return int(line.split()[1]) * 1024
    return 0


def _sample_linux_cuda(pid: int) -> MemSample:
    global _gpu_scope
    rss = psutil.Process(pid).memory_info().rss
    r = _nvml_used(pid) or _smi_used(pid)
    gpu, found, device_used = r
    if not found:
        _gpu_scope, gpu = "device", device_used
    _gpu_peak[pid] = max(_gpu_peak.get(pid, 0), gpu)
    return MemSample(time.monotonic(), rss + gpu, _vm_hwm(pid) + _gpu_peak[pid], rss, gpu)


def sample(pid: int) -> MemSample:
    return _sample_macos(pid) if platform.current() == "macos" else _sample_linux_cuda(pid)


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


if __name__ == "__main__":
    import argparse
    import os
    ap = argparse.ArgumentParser()
    platform.add_argument(ap)
    platform.apply(ap.parse_args())
    s = sample(os.getpid())
    print(f"[{platform.current()}] self: footprint {s.footprint / MiB:.1f} MiB, peak {s.peak_footprint / MiB:.1f} MiB, "
          f"rss {s.rss / MiB:.1f} MiB, gpu {s.gpu / MiB:.1f} MiB")
