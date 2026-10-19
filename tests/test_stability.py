"""Tests for stability and leak detection module (Phase 8)."""

from __future__ import annotations

from handcontrol.metrics.stability import (
    StabilityReport,
    StabilitySample,
    StabilityTestRunner,
)


class TestStabilityModule:
    def test_stability_runner_short_run(self):
        """Runner processes frames and returns a valid StabilityReport."""
        runner = StabilityTestRunner()
        report = runner.run(
            duration_seconds=2.0,
            target_fps=30.0,
            sample_interval_seconds=0.5,
            warmup_seconds=0.5,
        )
        assert isinstance(report, StabilityReport)
        assert report.total_frames > 30
        assert report.avg_fps > 20.0
        assert report.avg_pipeline_latency_ms >= 0.0
        assert report.avg_action_latency_ms >= 0.0
        assert report.initial_rss_mb > 0.0
        assert report.final_rss_mb > 0.0
        assert len(report.samples) >= 2
        assert not report.has_continuous_growth

    def test_stability_report_markdown(self):
        """Report converts to properly formatted Markdown."""
        sample = StabilitySample(
            elapsed_seconds=60.0,
            fps=30.0,
            pipeline_latency_ms=1.5,
            action_latency_ms=0.1,
            memory_rss_mb=45.0,
            memory_heap_mb=1.2,
            queue_size=1,
        )
        report = StabilityReport(
            duration_seconds=60.0,
            total_frames=1800,
            avg_fps=30.0,
            avg_pipeline_latency_ms=1.5,
            avg_action_latency_ms=0.1,
            initial_rss_mb=44.0,
            final_rss_mb=45.0,
            peak_rss_mb=46.0,
            growth_rate_mb_per_min=0.01,
            has_continuous_growth=False,
            samples=[sample],
        )
        md = report.to_markdown()
        assert "Relatorio do Teste de Estabilidade" in md
        assert "ESTAVEL" in md
        assert "1,800" in md

    def test_leak_detection_flag(self):
        """Growth flag triggers if total delta > 3MB and growth rate > 0.15 MB/min."""
        report_leaking = StabilityReport(
            duration_seconds=1800.0,
            total_frames=54000,
            avg_fps=30.0,
            avg_pipeline_latency_ms=2.0,
            avg_action_latency_ms=0.2,
            initial_rss_mb=50.0,
            final_rss_mb=120.0,
            peak_rss_mb=125.0,
            growth_rate_mb_per_min=2.33,
            has_continuous_growth=True,
            samples=[],
        )
        md = report_leaking.to_markdown()
        assert "TENDENCIA DE ALTA" in md
