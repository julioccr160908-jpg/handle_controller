"""Dynamic gesture recognizer for swipes, circles, and custom user trajectories (Section 4.6).

Features:
- Directional swipes: swipe_left, swipe_right, swipe_up, swipe_down.
- Circular gestures: circle_cw, circle_ccw.
- Custom trajectory template matching via DTW (Dynamic Time Warping).
- Independent tracking per hand with per-gesture cooldowns.
- Emits standard GestureEvent objects with STARTED phase and trajectory payload.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from handcontrol.features.trajectory import (
    TrajectoryBuffer,
    dtw_distance,
    normalize_trajectory_box,
    resample_points,
)
from handcontrol.tracking.models import GestureEvent, GesturePhase, HandFeatures

logger = logging.getLogger(__name__)


@dataclass
class CustomTrajectoryTemplate:
    """User-recorded trajectory template for dynamic gesture recognition.

    Attributes:
        gesture_id: Identifier of the custom gesture.
        hand: Expected hand ("Left", "Right", "Any").
        points: Resampled and normalized points [(x, y), ...].
        max_dtw_distance: Maximum allowable DTW distance to match (default 0.35).
        cooldown_s: Cooldown period after activation.
    """

    gesture_id: str
    hand: str
    points: list[tuple[float, float]]
    max_dtw_distance: float = 0.35
    cooldown_s: float = 0.5


class DynamicGestureRecognizer:
    """Recognizes dynamic gestures (swipes, circles, custom paths) from landmark trajectories."""

    def __init__(self, dtw_match_threshold: float = 0.35) -> None:
        self.dtw_match_threshold = dtw_match_threshold
        self._buffers: dict[str, TrajectoryBuffer] = {
            "Left": TrajectoryBuffer(max_duration_s=1.2),
            "Right": TrajectoryBuffer(max_duration_s=1.2),
        }
        self._custom_templates: list[CustomTrajectoryTemplate] = []
        self._last_trigger_times: dict[tuple[str, str], float] = {}
        self._default_cooldown_s = 0.45

    def add_custom_template(self, template: CustomTrajectoryTemplate) -> None:
        """Register a custom recorded gesture template."""
        # Ensure template points are resampled and normalized
        pts = resample_points(template.points, target_count=32)
        norm_pts = normalize_trajectory_box(pts)
        self._custom_templates.append(
            CustomTrajectoryTemplate(
                gesture_id=template.gesture_id,
                hand=template.hand,
                points=norm_pts,
                max_dtw_distance=template.max_dtw_distance,
                cooldown_s=template.cooldown_s,
            )
        )
        logger.info(
            "Registered custom trajectory gesture '%s' (%d points)",
            template.gesture_id,
            len(norm_pts),
        )

    def clear_templates(self) -> None:
        """Clear all registered custom templates."""
        self._custom_templates.clear()

    def reset(self) -> None:
        """Reset all trajectory buffers and cooldowns."""
        for buf in self._buffers.values():
            buf.clear()
        self._last_trigger_times.clear()

    def update(
        self,
        features_by_hand: dict[str, HandFeatures],
        landmarks_by_hand: dict[str, list[tuple[float, float]]] | None,
        timestamp: float,
    ) -> list[GestureEvent]:
        """Update trajectories with current frame landmarks and evaluate dynamic gestures.

        Args:
            features_by_hand: Dict of HandFeatures per hand.
            landmarks_by_hand: Dict of raw normalized landmark coordinates [(x, y), ...] per hand.
            timestamp: Monotonic frame timestamp.

        Returns:
            List of detected GestureEvents.
        """
        events: list[GestureEvent] = []

        for hand_label in ("Left", "Right"):
            features = features_by_hand.get(hand_label)
            buf = self._buffers[hand_label]

            # If hand is not present or invalid, reset buffer
            if not features or not features.is_valid:
                buf.clear()
                continue

            # Extract tracking reference point: prefer index tip (landmark 8) or wrist (0)
            lms = (landmarks_by_hand or {}).get(hand_label)
            if lms and len(lms) > 8:
                rx, ry = lms[8]  # index fingertip
            elif lms and len(lms) > 0:
                rx, ry = lms[0]  # wrist
            else:
                continue

            buf.add_point(rx, ry, timestamp)

            # 1. Check custom trajectory templates (higher priority)
            if self._custom_templates and len(buf.points) >= 12:
                recent = buf.get_recent_points(duration_s=1.0, current_time=timestamp)
                if len(recent) >= 8:
                    candidate_resampled = resample_points(recent, target_count=32)
                    candidate_norm = normalize_trajectory_box(candidate_resampled)

                    matched_tmpl = False
                    for tmpl in self._custom_templates:
                        if tmpl.hand != "Any" and tmpl.hand != hand_label:
                            continue
                        if not self._can_trigger(
                            tmpl.gesture_id, hand_label, timestamp, tmpl.cooldown_s
                        ):
                            continue

                        dist = dtw_distance(candidate_norm, tmpl.points)
                        if dist <= tmpl.max_dtw_distance:
                            self._record_trigger(tmpl.gesture_id, hand_label, timestamp)
                            buf.clear()
                            events.append(
                                GestureEvent(
                                    gesture_id=tmpl.gesture_id,
                                    phase=GesturePhase.STARTED,
                                    hand=hand_label,
                                    timestamp=timestamp,
                                    confidence=max(0.0, min(1.0, 1.0 - dist)),
                                    payload={"dtw_distance": dist, "type": "custom_trajectory"},
                                )
                            )
                            matched_tmpl = True
                            break

                    if matched_tmpl:
                        continue

            # 2. Check directional swipes
            swipe_gesture, dx, dy = buf.detect_swipe(current_time=timestamp)
            if swipe_gesture and self._can_trigger(
                swipe_gesture, hand_label, timestamp, self._default_cooldown_s
            ):
                self._record_trigger(swipe_gesture, hand_label, timestamp)
                buf.clear()  # Clear buffer after gesture fired to avoid duplicate triggers
                events.append(
                    GestureEvent(
                        gesture_id=swipe_gesture,
                        phase=GesturePhase.STARTED,
                        hand=hand_label,
                        timestamp=timestamp,
                        confidence=0.9,
                        payload={"dx": dx, "dy": dy, "type": "swipe"},
                    )
                )
                continue

            # 3. Check circular gestures
            circle_gesture, angle = buf.detect_circle(current_time=timestamp)
            if circle_gesture and self._can_trigger(circle_gesture, hand_label, timestamp, 0.6):
                self._record_trigger(circle_gesture, hand_label, timestamp)
                buf.clear()
                events.append(
                    GestureEvent(
                        gesture_id=circle_gesture,
                        phase=GesturePhase.STARTED,
                        hand=hand_label,
                        timestamp=timestamp,
                        confidence=0.85,
                        payload={"total_angle": angle, "type": "circle"},
                    )
                )
                continue

        return events

    def _can_trigger(self, gesture_id: str, hand: str, timestamp: float, cooldown: float) -> bool:
        key = (gesture_id, hand)
        last_t = self._last_trigger_times.get(key, -999.0)
        return (timestamp - last_t) >= cooldown

    def _record_trigger(self, gesture_id: str, hand: str, timestamp: float) -> None:
        key = (gesture_id, hand)
        self._last_trigger_times[key] = timestamp
