"""Performance metrics tracking (Section 11.2).

Thread-safe FPS, latency, and memory tracker using a sliding window.
Displayed in the main panel and recorded in ``docs/BENCHMARKS.md``.
"""

from __future__ import annotations

import ctypes
import logging
import threading
import time
import tracemalloc
from collections import deque
from ctypes import wintypes

logger = logging.getLogger(__name__)


# ------------------------------------------------ Windows Memory Structures ---


class ProcessMemoryCountersEx(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


_PSAPI_AVAILABLE = False
try:
    kernel32 = ctypes.windll.kernel32
    psapi = ctypes.windll.psapi
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ProcessMemoryCountersEx),
        wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    _PSAPI_AVAILABLE = True
except Exception:
    logger.debug("Windows PSAPI not available", exc_info=True)


def _get_windows_process_memory() -> tuple[float, float]:
    """Return (working_set_mb, private_bytes_mb) via Windows PSAPI.

    Returns (0.0, 0.0) on failure or non-Windows platforms.
    """
    if not _PSAPI_AVAILABLE:
        return 0.0, 0.0
    try:
        pmc = ProcessMemoryCountersEx()
        pmc.cb = ctypes.sizeof(ProcessMemoryCountersEx)
        h_process = kernel32.GetCurrentProcess()

        if psapi.GetProcessMemoryInfo(h_process, ctypes.byref(pmc), pmc.cb):
            rss_mb = pmc.WorkingSetSize / (1024.0 * 1024.0)
            private_mb = pmc.PrivateUsage / (1024.0 * 1024.0)
            return rss_mb, private_mb
    except Exception:
        logger.debug("Failed to retrieve Windows process memory", exc_info=True)
    return 0.0, 0.0


# ---------------------------------------------------- Performance Metrics ---


class PerformanceMetrics:
    """Thread-safe sliding-window FPS, latency, and memory tracker.

    Parameters:
        window_size: Number of recent samples to keep for averaging.
        enable_tracemalloc: Whether to initialize tracemalloc for Python heap tracking.
    """

    def __init__(self, window_size: int = 60, enable_tracemalloc: bool = False) -> None:
        self._window_size = window_size
        self._frame_times: deque[float] = deque(maxlen=window_size)
        self._latencies: deque[float] = deque(maxlen=window_size)
        self._action_latencies: deque[float] = deque(maxlen=window_size)
        self._queue_size: int = 0
        self._lock = threading.RLock()

        if enable_tracemalloc and not tracemalloc.is_tracing():
            try:
                tracemalloc.start()
            except Exception:
                logger.debug("Failed to start tracemalloc", exc_info=True)

    def record_frame(self) -> None:
        """Record the current timestamp for FPS calculation."""
        with self._lock:
            self._frame_times.append(time.monotonic())

    def record_latency(self, latency_ms: float) -> None:
        """Record a pipeline latency measurement in milliseconds (capture -> gesture)."""
        with self._lock:
            self._latencies.append(latency_ms)

    def record_action_latency(self, latency_ms: float) -> None:
        """Record an action execution latency in milliseconds (gesture -> action)."""
        with self._lock:
            self._action_latencies.append(latency_ms)

    def record_queue_size(self, size: int) -> None:
        """Record current pending frame queue size (must stay <= 1)."""
        with self._lock:
            self._queue_size = size

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
        """Average pipeline latency in milliseconds (capture -> gesture)."""
        with self._lock:
            if not self._latencies:
                return 0.0
            return sum(self._latencies) / len(self._latencies)

    @property
    def avg_action_latency_ms(self) -> float:
        """Average action execution latency in milliseconds (gesture -> action)."""
        with self._lock:
            if not self._action_latencies:
                return 0.0
            return sum(self._action_latencies) / len(self._action_latencies)

    @property
    def queue_size(self) -> int:
        """Pending frame queue size."""
        with self._lock:
            return self._queue_size

    @property
    def memory_rss_mb(self) -> float:
        """Process Working Set Size (physical RAM) in MB."""
        rss, _ = _get_windows_process_memory()
        return rss

    @property
    def memory_private_mb(self) -> float:
        """Process Commit Charge (private memory) in MB."""
        _, priv = _get_windows_process_memory()
        return priv

    @property
    def memory_heap_mb(self) -> float:
        """Python heap memory in MB tracked by tracemalloc."""
        if tracemalloc.is_tracing():
            current, _ = tracemalloc.get_traced_memory()
            return current / (1024.0 * 1024.0)
        return 0.0

    def get_summary(self) -> dict[str, float]:
        """Return a snapshot dictionary of all tracked metrics."""
        rss, priv = _get_windows_process_memory()
        heap = self.memory_heap_mb
        with self._lock:
            return {
                "fps": self.fps,
                "pipeline_latency_ms": self.avg_latency_ms,
                "action_latency_ms": self.avg_action_latency_ms,
                "queue_size": float(self._queue_size),
                "memory_rss_mb": rss,
                "memory_private_mb": priv,
                "memory_heap_mb": heap,
            }

    def reset(self) -> None:
        """Clear all recorded metrics."""
        with self._lock:
            self._frame_times.clear()
            self._latencies.clear()
            self._action_latencies.clear()
            self._queue_size = 0
