"""Tests for performance metrics module."""

from __future__ import annotations

import threading
import time

from handcontrol.metrics.performance import PerformanceMetrics


class TestPerformanceMetrics:
    def test_initial_state(self):
        m = PerformanceMetrics()
        assert m.fps == 0.0
        assert m.avg_latency_ms == 0.0

    def test_fps_calculation(self):
        m = PerformanceMetrics(window_size=10)
        # Simulate 10 frames at exactly 30 fps.
        for i in range(10):
            m._frame_times.append(i * (1.0 / 30.0))
        fps = m.fps
        assert 29.0 < fps < 31.0, f"Expected ~30 fps, got {fps}"

    def test_fps_single_frame(self):
        m = PerformanceMetrics()
        m.record_frame()
        assert m.fps == 0.0  # need at least 2 samples

    def test_latency_average(self):
        m = PerformanceMetrics()
        m.record_latency(10.0)
        m.record_latency(20.0)
        m.record_latency(30.0)
        assert m.avg_latency_ms == 20.0

    def test_latency_single(self):
        m = PerformanceMetrics()
        m.record_latency(42.0)
        assert m.avg_latency_ms == 42.0

    def test_reset_clears_all(self):
        m = PerformanceMetrics()
        m.record_frame()
        m.record_latency(10.0)
        m.reset()
        assert m.fps == 0.0
        assert m.avg_latency_ms == 0.0

    def test_window_overflow(self):
        """Old values should be evicted when window overflows."""
        m = PerformanceMetrics(window_size=5)
        for i in range(10):
            m.record_latency(float(i))
        # Should only keep the last 5 values: 5, 6, 7, 8, 9
        assert m.avg_latency_ms == 7.0

    def test_thread_safety(self):
        """Concurrent reads and writes should not crash."""
        m = PerformanceMetrics()

        def writer():
            for _ in range(100):
                m.record_frame()
                m.record_latency(5.0)
                time.sleep(0.001)

        def reader():
            for _ in range(100):
                _ = m.fps
                _ = m.avg_latency_ms
                time.sleep(0.001)

        threads = [threading.Thread(target=writer) for _ in range(2)]
        threads += [threading.Thread(target=reader) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # Reaching here without exceptions = pass

    def test_action_latency(self):
        m = PerformanceMetrics()
        assert m.avg_action_latency_ms == 0.0
        m.record_action_latency(2.0)
        m.record_action_latency(4.0)
        assert m.avg_action_latency_ms == 3.0

    def test_queue_size_tracking(self):
        m = PerformanceMetrics()
        assert m.queue_size == 0
        m.record_queue_size(1)
        assert m.queue_size == 1

    def test_memory_tracking_and_summary(self):
        m = PerformanceMetrics()
        summary = m.get_summary()
        assert "fps" in summary
        assert "pipeline_latency_ms" in summary
        assert "action_latency_ms" in summary
        assert "queue_size" in summary
        assert "memory_rss_mb" in summary
        assert "memory_private_mb" in summary
        assert "memory_heap_mb" in summary
        # Process memory should be non-negative (on Windows > 0)
        assert m.memory_rss_mb >= 0.0
        assert m.memory_heap_mb >= 0.0
