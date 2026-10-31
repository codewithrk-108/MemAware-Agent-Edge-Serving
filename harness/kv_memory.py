"""Physical KV-cell occupancy from the pinned runtime's read-only metrics patch.

Counts include retained idle cache and count shared physical cells once. They
describe occupied KV capacity, not CUDA allocation or scheduler reservations.
"""

import math
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field


class KvMetricsUnavailable(ValueError):
    """The runtime does not expose the supported physical-cell counters."""


def parse_kv_metrics(text: str) -> dict:
    metrics = {}
    for line in text.splitlines():
        if line.startswith('llamacpp:') and '{' not in line:
            name, value = line.split()
            metrics[name.removeprefix('llamacpp:')] = float(value)
    required = {'kv_cells_used', 'kv_cells_capacity'}
    if metrics.get('kv_cells_supported') != 1 or not required <= metrics.keys():
        raise KvMetricsUnavailable('physical KV-cell metrics unavailable; use the instrumented b11780 runtime')
    used, capacity = metrics['kv_cells_used'], metrics['kv_cells_capacity']
    if (not all(math.isfinite(x) and x.is_integer() for x in (used, capacity))
            or capacity <= 0 or used < 0 or used > capacity):
        raise ValueError(f'invalid physical KV-cell counts: used={used}, capacity={capacity}')
    processing = metrics.get('requests_processing')
    if processing is not None and (not math.isfinite(processing) or processing < 0 or not processing.is_integer()):
        raise ValueError(f'invalid processing count: {processing}')
    return {'used_cells': int(used), 'capacity_cells': int(capacity),
            'usage_ratio': used / capacity,
            'requests_processing': int(processing) if processing is not None else None}


def kv_memory_sample(url: str, timeout: float = 5) -> dict:
    with urllib.request.urlopen(url.rstrip('/') + '/metrics', timeout=timeout) as response:
        result = parse_kv_metrics(response.read().decode())
    return {'t': time.monotonic(), **result}


@dataclass
class KvMemSampler:
    url: str
    interval: float = 1.0
    samples: list = field(default_factory=list)
    error: str = ''
    error_count: int = 0

    def __post_init__(self):
        self._stop = threading.Event()
        self._thread = None

    def _sample(self) -> bool:
        try:
            self.samples.append(kv_memory_sample(self.url))
            return True
        except (OSError, ValueError) as exc:
            self.error = f'{type(exc).__name__}: {exc}'
            self.error_count += 1
            if isinstance(exc, ValueError):
                self._stop.set()
            return False

    def _run(self):
        while not self._stop.wait(self.interval):
            self._sample()

    def __enter__(self):
        self._sample()
        if not self._stop.is_set():
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *exc):
        stopped = self._stop.is_set()
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        if not stopped:
            self._sample()

    def summary(self) -> dict:
        return {
            'kv_measurement': 'physical_cells' if self.samples else None,
            'kv_peak_used_cells': max((s['used_cells'] for s in self.samples), default=None),
            'kv_capacity_cells': self.samples[0]['capacity_cells'] if self.samples else None,
            'kv_peak_usage_ratio': max((s['usage_ratio'] for s in self.samples), default=None),
            'kv_sample_count': len(self.samples),
            'kv_sampling_interval_s': self.interval,
            'kv_sampling_error': self.error or None,
            'kv_sampling_error_count': self.error_count,
        }
