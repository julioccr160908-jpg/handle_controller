"""Gesture configuration data structures (Section 4.6).

Defines the parameters for individual gesture recognizers: thresholds,
timing, hysteresis, cooldown, and hand requirements.

These configs are independent of UI, actions, and Windows.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class GestureConfig:
    """Configuration for a single gesture recognizer.

    Attributes:
        gesture_id: Unique identifier (e.g. "pinch_index", "point", "fist").
        hand: Required hand — "Left", "Right", "Preferred", or "Both".
        enter_threshold: Activation threshold (e.g. pinch distance ratio).
        exit_threshold: Deactivation threshold (must be > enter for hysteresis).
        min_confirm_time_s: Min time in CANDIDATE before transitioning to ACTIVE.
        min_release_time_s: Min time in RELEASING before transitioning to IDLE.
        cooldown_s: Min time after ENDED/CANCELLED before reactivation.
        min_confidence: Minimum hand detection confidence to consider.
        is_continuous: If True, emits HELD events while active. If False,
            emits only STARTED and ENDED (single-shot like a click).
        priority: Higher priority wins in conflict resolution.
        enabled: Whether this gesture is active.
    """

    gesture_id: str
    hand: str = "Preferred"
    enter_threshold: float = 0.0
    exit_threshold: float = 0.0
    min_confirm_time_s: float = 0.05
    min_release_time_s: float = 0.05
    cooldown_s: float = 0.1
    min_confidence: float = 0.5
    is_continuous: bool = False
    priority: int = 0
    enabled: bool = True
    extra: dict = field(default_factory=dict)
