"""In-process metrics counters.

Simple, dependency-free counters/gauges for observability. Reset on restart.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime


class MetricsRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, int] = {}
        self._gauges: dict[str, float] = {}
        self._started = datetime.now(UTC)

    def incr(self, name: str, by: int = 1) -> None:
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + by

    def gauge(self, name: str, value: float) -> None:
        with self._lock:
            self._gauges[name] = value

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "started_at": self._started.isoformat(),
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
            }


metrics = MetricsRegistry()
