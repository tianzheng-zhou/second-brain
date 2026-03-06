"""
metrics.py — Lightweight in-memory metrics collection.
Uses collections.deque for recent N samples, no external dependencies.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Any


class Metrics:
    def __init__(self, history_size: int = 100) -> None:
        self._history_size = history_size
        # Counters: {name: {label: count}}
        self._counters: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        # Duration samples: {name: deque[float]}
        self._durations: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=history_size)
        )
        # Single values
        self._gauges: dict[str, Any] = {}

    def increment(self, name: str, label: str = "total", amount: int = 1) -> None:
        self._counters[name][label] += amount

    def record_duration(self, name: str, duration_ms: float) -> None:
        self._durations[name].append(duration_ms)

    def set_gauge(self, name: str, value: Any) -> None:
        self._gauges[name] = value

    def _percentile(self, data: list[float], p: float) -> float:
        if not data:
            return 0.0
        sorted_data = sorted(data)
        idx = int(len(sorted_data) * p / 100)
        idx = min(idx, len(sorted_data) - 1)
        return sorted_data[idx]

    def get_summary(self) -> dict[str, Any]:
        summary: dict[str, Any] = {}
        for name, labels in self._counters.items():
            summary[name] = dict(labels)
        for name, samples in self._durations.items():
            lst = list(samples)
            summary[f"{name}_p50_ms"] = self._percentile(lst, 50)
            summary[f"{name}_p95_ms"] = self._percentile(lst, 95)
        summary.update(self._gauges)
        return summary


# Global singleton
_metrics = Metrics()


def get_metrics() -> Metrics:
    return _metrics


class timer:
    """Context manager to record duration."""
    def __init__(self, metric_name: str) -> None:
        self._name = metric_name
        self._start = 0.0

    def __enter__(self) -> "timer":
        self._start = time.monotonic()
        return self

    def __exit__(self, *_: Any) -> None:
        duration_ms = (time.monotonic() - self._start) * 1000
        _metrics.record_duration(self._name, duration_ms)
