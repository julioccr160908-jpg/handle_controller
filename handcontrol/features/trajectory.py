"""Trajectory processing, resampling, and Dynamic Time Warping (Section 4.6).

Provides:
- TrajectoryPoint: timestamped normalized coordinate.
- TrajectoryBuffer: rolling history of hand landmark coordinates.
- Spatial resampling and scale/translation normalization.
- Fast Dynamic Time Warping (DTW) distance metric for custom user gestures.
- Geometric swipe and circle classifiers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class TrajectoryPoint:
    """A single normalized point in a trajectory."""

    x: float
    y: float
    timestamp: float


def resample_points(
    points: list[tuple[float, float]], target_count: int = 32
) -> list[tuple[float, float]]:
    """Resample a 2D polyline to have `target_count` equidistant points.

    Args:
        points: List of (x, y) coordinates.
        target_count: Number of equidistant output points.

    Returns:
        List of resampled (x, y) coordinates of length target_count.
    """
    if len(points) < 2:
        if not points:
            return [(0.0, 0.0)] * target_count
        return [points[0]] * target_count

    # Calculate total path length
    seg_lengths: list[float] = []
    total_length = 0.0
    for i in range(len(points) - 1):
        dx = points[i + 1][0] - points[i][0]
        dy = points[i + 1][1] - points[i][1]
        dist = math.hypot(dx, dy)
        seg_lengths.append(dist)
        total_length += dist

    if total_length < 1e-6:
        return [points[0]] * target_count

    interval = total_length / (target_count - 1)
    resampled: list[tuple[float, float]] = [points[0]]
    current_dist = 0.0
    seg_idx = 0
    seg_start_dist = 0.0

    for _ in range(1, target_count - 1):
        current_dist += interval
        while (
            seg_idx < len(seg_lengths) - 1
            and (seg_start_dist + seg_lengths[seg_idx]) < current_dist
        ):
            seg_start_dist += seg_lengths[seg_idx]
            seg_idx += 1

        seg_len = seg_lengths[seg_idx]
        if seg_len < 1e-6:
            resampled.append(points[seg_idx])
        else:
            t = (current_dist - seg_start_dist) / seg_len
            t = max(0.0, min(1.0, t))
            p0 = points[seg_idx]
            p1 = points[seg_idx + 1]
            x = p0[0] + t * (p1[0] - p0[0])
            y = p0[1] + t * (p1[1] - p0[1])
            resampled.append((x, y))

    resampled.append(points[-1])
    return resampled


def normalize_trajectory_box(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Translate centroid to origin (0, 0) and scale bounding box to unit size.

    Ensures matching is position and scale invariant.
    """
    if not points:
        return []

    # Centroid
    cx = sum(p[0] for p in points) / len(points)
    cy = sum(p[1] for p in points) / len(points)
    centered = [(p[0] - cx, p[1] - cy) for p in points]

    # Bounding scale
    max_dist = max(math.hypot(p[0], p[1]) for p in centered)
    scale = max_dist if max_dist > 1e-5 else 1.0

    return [(p[0] / scale, p[1] / scale) for p in centered]


def dtw_distance(
    seq_a: list[tuple[float, float]],
    seq_b: list[tuple[float, float]],
) -> float:
    """Compute Dynamic Time Warping (DTW) distance between two normalized 2D point sequences.

    Returns:
        Average point-to-point Euclidean alignment distance.
    """
    n = len(seq_a)
    m = len(seq_b)
    if n == 0 or m == 0:
        return 999.0

    # DP cost matrix
    inf = float("inf")
    dp = [[inf] * (m + 1) for _ in range(n + 1)]
    dp[0][0] = 0.0

    for i in range(1, n + 1):
        ax, ay = seq_a[i - 1]
        for j in range(1, m + 1):
            bx, by = seq_b[j - 1]
            dist = math.hypot(ax - bx, ay - by)
            dp[i][j] = dist + min(dp[i - 1][j], dp[i][j - 1], dp[i - 1][j - 1])

    total_cost = dp[n][m]
    # Normalize by alignment path length approximation
    return total_cost / max(n, m)


class TrajectoryBuffer:
    """Rolling buffer of timestamped points for trajectory classification."""

    def __init__(self, max_duration_s: float = 1.5, max_points: int = 120) -> None:
        self.max_duration_s = max_duration_s
        self.max_points = max_points
        self._points: list[TrajectoryPoint] = []

    def add_point(self, x: float, y: float, timestamp: float) -> None:
        """Add a new observation and prune stale points."""
        self._points.append(TrajectoryPoint(x, y, timestamp))
        self._prune(timestamp)

    def clear(self) -> None:
        """Clear all buffered points."""
        self._points.clear()

    @property
    def points(self) -> list[TrajectoryPoint]:
        """List of active points in the buffer."""
        return list(self._points)

    def get_recent_points(
        self, duration_s: float, current_time: float
    ) -> list[tuple[float, float]]:
        """Return (x, y) coordinates within the last `duration_s` seconds."""
        cutoff = current_time - duration_s
        return [(p.x, p.y) for p in self._points if p.timestamp >= cutoff]

    def _prune(self, current_time: float) -> None:
        cutoff = current_time - self.max_duration_s
        while self._points and (
            self._points[0].timestamp < cutoff or len(self._points) > self.max_points
        ):
            self._points.pop(0)

    def detect_swipe(
        self,
        current_time: float,
        duration_s: float = 0.35,
        min_displacement: float = 0.12,
        velocity_ratio: float = 2.0,
    ) -> tuple[str | None, float, float]:
        """Detect a rapid directional swipe in the recent trajectory window.

        Args:
            current_time: Current timestamp.
            duration_s: Lookback window in seconds.
            min_displacement: Minimum normalized displacement to qualify as a swipe.
            velocity_ratio: Dominance ratio of primary axis over secondary axis.

        Returns:
            (gesture_name, dx, dy) where gesture_name is one of
            "swipe_left", "swipe_right", "swipe_up", "swipe_down", or None.
        """
        pts = self.get_recent_points(duration_s, current_time)
        if len(pts) < 4:
            return None, 0.0, 0.0

        p_start = pts[0]
        p_end = pts[-1]
        dx = p_end[0] - p_start[0]
        dy = p_end[1] - p_start[1]
        abs_dx = abs(dx)
        abs_dy = abs(dy)

        # Check path straightness: direct displacement vs total distance traveled
        path_len = 0.0
        for i in range(len(pts) - 1):
            path_len += math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])

        disp = math.hypot(dx, dy)
        if path_len > 1e-4 and (disp / path_len) < 0.70:
            # Trajectory has significant turns/bends — not a straight swipe
            return None, dx, dy

        # Check horizontal swipe
        if abs_dx >= min_displacement and abs_dx > velocity_ratio * (abs_dy + 1e-4):
            # In mirrored coordinates or standard normalized coords:
            # dx > 0 = right, dx < 0 = left
            gesture = "swipe_right" if dx > 0 else "swipe_left"
            return gesture, dx, dy

        # Check vertical swipe
        if abs_dy >= min_displacement and abs_dy > velocity_ratio * (abs_dx + 1e-4):
            # dy > 0 = down, dy < 0 = up
            gesture = "swipe_down" if dy > 0 else "swipe_up"
            return gesture, dx, dy

        return None, dx, dy

    def detect_circle(
        self,
        current_time: float,
        duration_s: float = 0.8,
        min_radius: float = 0.04,
        min_points: int = 12,
    ) -> tuple[str | None, float]:
        """Detect a circular motion (clockwise or counter-clockwise).

        Calculates cumulative angular sweep around the trajectory centroid.

        Returns:
            (circle_type, total_angle_radians) where circle_type is
            "circle_cw", "circle_ccw", or None.
        """
        pts = self.get_recent_points(duration_s, current_time)
        if len(pts) < min_points:
            return None, 0.0

        # Centroid
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)

        # Average radius from centroid
        avg_radius = sum(math.hypot(p[0] - cx, p[1] - cy) for p in pts) / len(pts)
        if avg_radius < min_radius:
            return None, 0.0

        # Accumulate signed angle changes between consecutive vectors from centroid
        total_angle = 0.0
        prev_angle = math.atan2(pts[0][1] - cy, pts[0][0] - cx)

        for i in range(1, len(pts)):
            angle = math.atan2(pts[i][1] - cy, pts[i][0] - cx)
            diff = angle - prev_angle
            # Normalize to [-pi, pi]
            while diff > math.pi:
                diff -= 2 * math.pi
            while diff < -math.pi:
                diff += 2 * math.pi
            total_angle += diff
            prev_angle = angle

        # A full circle requires at least ~280 degrees (4.9 radians)
        full_circle_threshold = 4.8  # ~275 degrees
        if total_angle >= full_circle_threshold:
            # Positive angular sweep = clockwise in screen coords (Y down)
            return "circle_cw", total_angle
        elif total_angle <= -full_circle_threshold:
            return "circle_ccw", total_angle

        return None, total_angle
