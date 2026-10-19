"""Long-term stability and memory leak analysis runner (Section 11.2 & Phase 8).

Runs continuous frame streams through the pipeline, monitors memory growth,
computes linear growth rate (MB/min), and generates markdown reports for docs/BENCHMARKS.md.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass, field

from handcontrol.actions.allowlist import Allowlist
from handcontrol.actions.controller import ActionController
from handcontrol.actions.simulation import SimulatedExecutor
from handcontrol.features.extractor import FeatureExtractor
from handcontrol.gestures.engine import GestureEngine
from handcontrol.mapping.profile import GestureMapper
from handcontrol.metrics.performance import PerformanceMetrics
from handcontrol.tracking.models import (
    HandFrame,
    HandObservation,
    ImagePoint,
    LandmarkPoint,
)

logger = logging.getLogger(__name__)


@dataclass
class StabilitySample:
    """Snapshot of metrics at a specific point in time."""

    elapsed_seconds: float
    fps: float
    pipeline_latency_ms: float
    action_latency_ms: float
    memory_rss_mb: float
    memory_heap_mb: float
    queue_size: int


@dataclass
class StabilityReport:
    """Summary of a stability run."""

    duration_seconds: float
    total_frames: int
    avg_fps: float
    avg_pipeline_latency_ms: float
    avg_action_latency_ms: float
    initial_rss_mb: float
    final_rss_mb: float
    peak_rss_mb: float
    growth_rate_mb_per_min: float
    has_continuous_growth: bool
    samples: list[StabilitySample] = field(default_factory=list)

    def to_markdown(self) -> str:
        """Format the report as GitHub-flavored Markdown."""
        growth_badge = (
            "[FAIL] TENDENCIA DE ALTA"
            if self.has_continuous_growth
            else "[OK] ESTAVEL (Sem tendencia de alta)"
        )
        lines = [
            "### Relatorio do Teste de Estabilidade",
            "",
            f"- **Duracao:** {self.duration_seconds:.1f} s ({self.duration_seconds / 60:.1f} min)",
            f"- **Total de Quadros Processados:** {self.total_frames:,}",
            f"- **FPS Medio:** {self.avg_fps:.1f} (Meta: >= 20, alvo 30)",
            f"- **Latencia Media do Pipeline:** {self.avg_pipeline_latency_ms:.1f} ms (Meta: < 100 ms)",
            f"- **Latencia Media de Execucao da Acao:** {self.avg_action_latency_ms:.2f} ms (Meta: < 50 ms)",
            f"- **RAM Inicial (apos warmup):** {self.initial_rss_mb:.1f} MB",
            f"- **RAM Final:** {self.final_rss_mb:.1f} MB",
            f"- **RAM Pico:** {self.peak_rss_mb:.1f} MB",
            f"- **Taxa de Crescimento de Memoria:** {self.growth_rate_mb_per_min:+.3f} MB/min",
            f"- **Status de Vazamentos:** {growth_badge}",
            "",
            "| Minuto | FPS | Lat. Pipeline (ms) | Lat. Acao (ms) | RAM RSS (MB) | Heap (MB) |",
            "|---|---|---|---|---|---|",
        ]
        for s in self.samples:
            minute_str = f"{s.elapsed_seconds / 60:.1f} min"
            lines.append(
                f"| {minute_str} | {s.fps:.1f} | {s.pipeline_latency_ms:.1f} | "
                f"{s.action_latency_ms:.2f} | {s.memory_rss_mb:.1f} | {s.memory_heap_mb:.2f} |"
            )
        return "\n".join(lines)


class StabilityTestRunner:
    """Executes long-running stability testing on the full gesture/mapping/action pipeline."""

    def __init__(self) -> None:
        self.metrics = PerformanceMetrics()
        self.extractor = FeatureExtractor()
        self.engine = GestureEngine()
        self.allowlist = Allowlist()
        self.mapper = GestureMapper()
        self.executor = SimulatedExecutor()
        self.controller = ActionController(self.executor, allowlist=self.allowlist)

    def _generate_synthetic_hand_frame(self, frame_idx: int, t_monotonic: float) -> HandFrame:
        """Generate alternating realistic hand landmark frames."""
        cycle = frame_idx % 300
        # 0..60: pointing, 61..100: pinch, 101..160: fist, 161..220: open palm, 221..300: moving
        x_base = 0.5 + 0.15 * ((frame_idx % 60) / 60.0)
        y_base = 0.5

        landmarks_norm = [LandmarkPoint(x=x_base, y=y_base, z=0.0) for _ in range(21)]

        if cycle < 60:
            # Pointing: index extended, others folded
            landmarks_norm[8] = LandmarkPoint(x=x_base, y=y_base - 0.25, z=0.0)
            landmarks_norm[6] = LandmarkPoint(x=x_base, y=y_base - 0.15, z=0.0)
        elif cycle < 100:
            # Pinch: thumb tip touching index tip
            landmarks_norm[4] = LandmarkPoint(x=x_base + 0.01, y=y_base, z=0.0)
            landmarks_norm[8] = LandmarkPoint(x=x_base, y=y_base, z=0.0)
        elif cycle < 160:
            # Fist: all fingertips close to wrist/palm
            for tip in (4, 8, 12, 16, 20):
                landmarks_norm[tip] = LandmarkPoint(x=x_base, y=y_base + 0.05, z=0.0)
        else:
            # Open palm: all tips extended upward
            for i, tip in enumerate((4, 8, 12, 16, 20)):
                landmarks_norm[tip] = LandmarkPoint(
                    x=x_base + (i - 2) * 0.05, y=y_base - 0.25, z=0.0
                )

        landmarks_img = [ImagePoint(x=lm.x * 640.0, y=lm.y * 480.0) for lm in landmarks_norm]

        obs = HandObservation(
            handedness="Right",
            handedness_confidence=0.98,
            landmarks_normalized=landmarks_norm,
            landmarks_image=landmarks_img,
            presence_confidence=0.99,
        )
        return HandFrame(
            frame_id=frame_idx,
            hands=[obs],
            timestamp=t_monotonic,
            frame_width=640,
            frame_height=480,
        )

    def run(
        self,
        duration_seconds: float = 30.0,
        target_fps: float = 30.0,
        sample_interval_seconds: float = 5.0,
        warmup_seconds: float = 3.0,
    ) -> StabilityReport:
        """Run the stability test for `duration_seconds`."""
        logger.info("Starting stability test for %.1f seconds", duration_seconds)
        start_time = time.monotonic()
        frame_interval = 1.0 / target_fps
        next_frame_time = start_time
        next_sample_time = start_time + sample_interval_seconds

        samples: list[StabilitySample] = []
        total_frames = 0
        warmup_rss: float | None = None

        while True:
            now = time.monotonic()
            elapsed = now - start_time
            if elapsed >= duration_seconds:
                break

            # Process a frame through the pipeline
            frame_cap_time = now
            hand_frame = self._generate_synthetic_hand_frame(total_frames, frame_cap_time)

            # 1. Feature extraction
            features_by_hand = {
                obs.handedness: self.extractor.extract(obs, hand_frame.timestamp)
                for obs in hand_frame.hands
            }

            # 2. Gesture recognition
            lms_by_hand = {
                obs.handedness: [(p.x, p.y) for p in obs.landmarks_normalized]
                for obs in hand_frame.hands
            }
            gesture_events = self.engine.update(
                features_by_hand,
                hand_frame.timestamp,
                landmarks_by_hand=lms_by_hand,
            )

            # 3. Gesture mapping
            requests = self.mapper.process_events(gesture_events)

            # 4. Action execution
            t_act_start = time.monotonic()
            _ = self.controller.submit_many(requests)
            act_lat = (time.monotonic() - t_act_start) * 1000.0

            pipe_lat = (time.monotonic() - frame_cap_time) * 1000.0

            self.metrics.record_frame()
            self.metrics.record_latency(pipe_lat)
            self.metrics.record_action_latency(act_lat)
            self.metrics.record_queue_size(1)
            total_frames += 1

            if warmup_rss is None and elapsed >= warmup_seconds:
                warmup_rss = self.metrics.memory_rss_mb

            # Periodic sample collection
            if now >= next_sample_time:
                samples.append(
                    StabilitySample(
                        elapsed_seconds=elapsed,
                        fps=self.metrics.fps,
                        pipeline_latency_ms=self.metrics.avg_latency_ms,
                        action_latency_ms=self.metrics.avg_action_latency_ms,
                        memory_rss_mb=self.metrics.memory_rss_mb,
                        memory_heap_mb=self.metrics.memory_heap_mb,
                        queue_size=self.metrics.queue_size,
                    )
                )
                next_sample_time = now + sample_interval_seconds

            # Throttle to target FPS
            next_frame_time += frame_interval
            sleep_time = next_frame_time - time.monotonic()
            if sleep_time > 0:
                time.sleep(sleep_time)
            else:
                next_frame_time = time.monotonic()

        # Final sample
        final_elapsed = time.monotonic() - start_time
        samples.append(
            StabilitySample(
                elapsed_seconds=final_elapsed,
                fps=self.metrics.fps,
                pipeline_latency_ms=self.metrics.avg_latency_ms,
                action_latency_ms=self.metrics.avg_action_latency_ms,
                memory_rss_mb=self.metrics.memory_rss_mb,
                memory_heap_mb=self.metrics.memory_heap_mb,
                queue_size=self.metrics.queue_size,
            )
        )

        initial_rss = warmup_rss if warmup_rss is not None else samples[0].memory_rss_mb
        final_rss = samples[-1].memory_rss_mb
        peak_rss = max(s.memory_rss_mb for s in samples)

        # Compute growth rate (MB / min) via linear regression over samples
        if len(samples) >= 2:
            times_min = [(s.elapsed_seconds - samples[0].elapsed_seconds) / 60.0 for s in samples]
            mems = [s.memory_rss_mb for s in samples]
            n = len(samples)
            x_bar = sum(times_min) / n
            y_bar = sum(mems) / n
            denom = sum((x - x_bar) ** 2 for x in times_min)
            growth_rate = (
                sum((x - x_bar) * (y - y_bar) for x, y in zip(times_min, mems, strict=False))
                / denom
                if denom > 0
                else 0.0
            )
        else:
            growth_rate = 0.0

        # Memory leak check: continuous growth requires both positive trend and meaningful growth (> 3 MB)
        total_delta_mb = final_rss - initial_rss
        has_continuous_growth = (total_delta_mb > 3.0) and (growth_rate > 0.15)

        return StabilityReport(
            duration_seconds=final_elapsed,
            total_frames=total_frames,
            avg_fps=self.metrics.fps,
            avg_pipeline_latency_ms=self.metrics.avg_latency_ms,
            avg_action_latency_ms=self.metrics.avg_action_latency_ms,
            initial_rss_mb=initial_rss,
            final_rss_mb=final_rss,
            peak_rss_mb=peak_rss,
            growth_rate_mb_per_min=growth_rate,
            has_continuous_growth=has_continuous_growth,
            samples=samples,
        )


def main() -> None:
    """CLI entry point for running stability verification."""
    parser = argparse.ArgumentParser(description="HandControl Stability & Leak Test Runner")
    parser.add_argument(
        "--duration",
        type=float,
        default=60.0,
        help="Duration in seconds to run (default: 60s; for 30min use 1800)",
    )
    parser.add_argument(
        "--fps",
        type=float,
        default=30.0,
        help="Target processing FPS (default: 30)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="Sample recording interval in seconds (default: 5s)",
    )
    args = parser.parse_args()

    runner = StabilityTestRunner()
    print(
        f"Running HandControl stability test for {args.duration:.1f}s ({args.duration / 60:.1f} min)..."
    )
    report = runner.run(
        duration_seconds=args.duration,
        target_fps=args.fps,
        sample_interval_seconds=args.interval,
    )
    print("\n" + report.to_markdown())
    if report.has_continuous_growth:
        print("\n[AVISO]: Tendência de alta contínua de memória detectada!")
        sys.exit(1)
    else:
        print("\n[SUCESSO]: Teste de estabilidade concluído sem vazamentos de memória.")
        sys.exit(0)


if __name__ == "__main__":
    main()
