"""Start and stop llama-server with a fixed configuration, and sample its memory while it runs."""

import os
import re
import signal
import subprocess
import time
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from .memory import MemSampler, MiB, sample

ROOT = Path(__file__).resolve().parents[1]
LLAMA_SERVER = Path(os.environ.get("LLAMA_SERVER", ROOT.parent / "llama.cpp/build/bin/llama-server"))
MODEL = Path(os.environ.get("MODEL", ROOT.parent / "models/Qwen3-0.6B-Q8_0.gguf"))


@dataclass
class ServerConfig:
    ctx: int = 8192                       # total KV cells (-c)
    parallel: int = 4                     # slots (-np)
    kv_unified: Optional[bool] = True     # one shared KV pool vs a fixed partition per slot
    cache_ram: Optional[int] = None       # host prompt cache in MiB, None = server default (8192), 0 = off
    cache_idle_slots: Optional[bool] = None
    mmap: bool = True
    batch: Optional[int] = None           # -b
    ubatch: Optional[int] = None          # -ub
    extra: list = field(default_factory=list)
    port: int = 8090
    verbose: bool = False                 # -v prints the memory breakdown, too noisy for timed runs

    def args(self) -> list:
        a = [str(LLAMA_SERVER), "-m", str(MODEL), "-c", str(self.ctx), "-np", str(self.parallel),
             "--port", str(self.port), "--metrics", "-ngl", "999"]
        if self.kv_unified is not None:
            a.append("-kvu" if self.kv_unified else "-no-kvu")
        if self.cache_ram is not None:
            a += ["--cache-ram", str(self.cache_ram)]
        if self.cache_idle_slots is not None:
            a.append("--cache-idle-slots" if self.cache_idle_slots else "--no-cache-idle-slots")
        if not self.mmap:
            a += ["--load-mode", "none"]
        if self.batch:
            a += ["-b", str(self.batch)]
        if self.ubatch:
            a += ["-ub", str(self.ubatch)]
        if self.verbose:
            a.append("-v")
        return a + list(self.extra)


# lines of the -v startup log that give the static memory breakdown
_BREAKDOWN = {
    "model_gpu_mib":   re.compile(r"MTL0(?:_Mapped)? model buffer size =\s+([\d.]+) MiB"),
    "model_cpu_mib":   re.compile(r"CPU(?:_Mapped)? model buffer size =\s+([\d.]+) MiB"),
    "kv_mib":          re.compile(r"llama_kv_cache: size =\s+([\d.]+) MiB"),
    "compute_gpu_mib": re.compile(r"MTL0 compute buffer size =\s+([\d.]+) MiB"),
    "compute_cpu_mib": re.compile(r"CPU compute buffer size =\s+([\d.]+) MiB"),
}


class Server:
    def __init__(self, cfg: ServerConfig, log_path: Path, sample_interval: float = 0.05):
        self.cfg = cfg
        self.log_path = Path(log_path)
        self.sample_interval = sample_interval
        self.proc: Optional[subprocess.Popen] = None
        self.mem: Optional[MemSampler] = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.cfg.port}"

    def __enter__(self):
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log = open(self.log_path, "w")
        self.proc = subprocess.Popen(self.cfg.args(), stdout=self._log, stderr=subprocess.STDOUT)
        self._wait_ready()
        self.mem = MemSampler(self.proc.pid, self.sample_interval).start()
        return self

    def __exit__(self, *exc):
        if self.mem:
            self.mem.stop()
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGINT)
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self._log.close()

    def _wait_ready(self, timeout: float = 180):
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            if self.proc.poll() is not None:
                raise RuntimeError(f"llama-server exited with {self.proc.returncode}, see {self.log_path}")
            try:
                with urllib.request.urlopen(self.url + "/health", timeout=1) as r:
                    if r.status == 200:
                        return
            except OSError:
                pass
            time.sleep(0.2)
        raise TimeoutError(f"llama-server not ready after {timeout} s, see {self.log_path}")

    def memory_now(self) -> dict:
        s = sample(self.proc.pid)
        return {"footprint_mib": s.footprint / MiB, "peak_footprint_mib": s.peak_footprint / MiB, "rss_mib": s.rss / MiB}

    def breakdown(self) -> dict:
        """Static allocations from the startup log (needs verbose=True)."""
        # generated text in the -v log can hold partial multi-byte characters
        text = self.log_path.read_text(errors="replace")
        out = {}
        for key, rx in _BREAKDOWN.items():
            vals = [float(v) for v in rx.findall(text)]
            if vals:
                # the server sizes the model in a dry run first, the last match is the real load
                out[key] = vals[-1]
        return out

    def metrics(self) -> dict:
        with urllib.request.urlopen(self.url + "/metrics", timeout=5) as r:
            text = r.read().decode()
        out = {}
        for line in text.splitlines():
            if line.startswith("llamacpp:") and "{" not in line:
                name, value = line.split()
                out[name.removeprefix("llamacpp:")] = float(value)
        return out

    def describe(self) -> dict:
        d = asdict(self.cfg)
        d["args"] = " ".join(self.cfg.args())
        return d
