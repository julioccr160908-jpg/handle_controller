"""Performance metrics tracking (Section 11.2).

Thread-safe FPS and latency tracker using a sliding window.
Displayed in the main panel and recorded in ``docs/BENCHMARKS.md``.
"""

from __future__ import annotations

import threading
import time
from collections import deque


class PerformanceMetrics:
    """Thread-safe sliding-window FPS and latency tracker.

    Parameters:
        window_size: Number of recent samples to keep for averaging.
    """

    def __init__(self, window_size: int = 60) -> None:
        self._window_size = window_size
        self._frame_times: deque[float] = deque(maxlen=window_size)
        self._latencies: deque[float] = deque(maxlen=window_size)
        self._lock = threading.Lock()

    def record_frame(self) -> None:
        """Record the current timestamp for FPS calculation."""
        with self._lock:
            self._frame_times.append(time.monotonic())

    def record_latency(self, latency_ms: float) -> None:
        """Record a pipeline latency measurement in milliseconds."""
        with self._lock:
            self._latencies.append(latency_ms)

    @property
    def fps(self) -> float:
        """Current frames per second based on the sliding window."""
        with self._lock:
            if len(self._frame_times) < 2:
                return 0.0
            elapsed = self._frame_times[-1] - self._frame_times[0]
            if elapsed <= 0:
                return 0.0
            return (len(self._frame_times) - 1) / elapsed

    @property
    def avg_latency_ms(self) -> float:
        """Average pipeline latency in milliseconds."""
        with self._lock:
            if not self._latencies:
                return 0.0
            return sum(self._latencies) / len(self._latencies)

    def reset(self) -> None:
        """Clear all recorded metrics."""
        with self._lock:
            self._frame_times.clear()
            self._latencies.clear()
