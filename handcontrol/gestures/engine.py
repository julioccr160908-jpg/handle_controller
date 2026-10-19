"""Gesture engine — orchestrates all gesture recognizers (Section 4.6).

The engine:
1. Receives ``HandFeatures`` per frame (from ``FeatureExtractor``).
2. Evaluates geometric rules for each registered gesture.
3. Feeds results into per-gesture state machines.
4. Emits ``GestureEvent`` objects that callers consume.

It is completely independent of UI, mapping, actions, and Windows.
Hand loss (empty features) cancels all active gestures (Rule 3).
"""

from __future__ import annotations

import logging

from handcontrol.gestures.config import GestureConfig
from handcontrol.gestures.dynamic import DynamicGestureRecognizer
from handcontrol.gestures.rules import (
    rule_fist,
    rule_pinch_index,
    rule_pinch_middle,
    rule_point,
    rule_two_fingers,
    rule_two_open_palms,
)
from handcontrol.gestures.state_machine import GestureState, GestureStateMachine
from handcontrol.tracking.models import GestureEvent, HandFeatures

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default gesture configurations (Section 5)
# ---------------------------------------------------------------------------

# Pinch thresholds (normalised by hand scale):
#   enter < 0.30  →  thumb and finger tip are close
#   exit  < 0.38  →  hysteresis band keeps pinch alive a bit longer
_PINCH_ENTER = 0.30
_PINCH_EXIT = 0.38


def _default_configs() -> list[GestureConfig]:
    """Return the default gesture configurations (Section 5 table)."""
    return [
        # --- Right-hand gestures (or "preferred" hand) ---
        GestureConfig(
            gesture_id="point",
            hand="Preferred",
            min_confirm_time_s=0.05,
            min_release_time_s=0.06,
            cooldown_s=0.08,
            is_continuous=True,
            priority=1,
        ),
        GestureConfig(
            gesture_id="two_fingers",
            hand="Preferred",
            min_confirm_time_s=0.05,
            min_release_time_s=0.06,
            cooldown_s=0.08,
            is_continuous=True,
            priority=2,
        ),
        GestureConfig(
            gesture_id="pinch_index",
            hand="Preferred",
            enter_threshold=_PINCH_ENTER,
            exit_threshold=_PINCH_EXIT,
            min_confirm_time_s=0.04,
            min_release_time_s=0.04,
            cooldown_s=0.15,
            is_continuous=False,  # single-shot click
            priority=10,
        ),
        GestureConfig(
            gesture_id="pinch_middle",
            hand="Preferred",
            enter_threshold=_PINCH_ENTER,
            exit_threshold=_PINCH_EXIT,
            min_confirm_time_s=0.04,
            min_release_time_s=0.04,
            cooldown_s=0.15,
            is_continuous=False,  # single-shot click
            priority=10,
        ),
        GestureConfig(
            gesture_id="pinch_sustained",
            hand="Preferred",
            enter_threshold=_PINCH_ENTER,
            exit_threshold=_PINCH_EXIT,
            min_confirm_time_s=0.40,  # spec: > 400 ms hold
            min_release_time_s=0.04,
            cooldown_s=0.10,
            is_continuous=True,
            priority=12,
        ),
        # --- Left-hand gestures ---
        GestureConfig(
            gesture_id="fist",
            hand="Left",
            min_confirm_time_s=0.08,
            min_release_time_s=0.08,
            cooldown_s=0.10,
            is_continuous=True,
            priority=5,
        ),
        # --- Two-hand gestures ---
        GestureConfig(
            gesture_id="two_open_palms",
            hand="Both",
            min_confirm_time_s=1.0,  # spec: 1 s hold
            min_release_time_s=0.1,
            cooldown_s=0.5,
            is_continuous=False,
            priority=20,
        ),
    ]


# ---------------------------------------------------------------------------
# Gesture Engine
# ---------------------------------------------------------------------------


class GestureEngine:
    """Evaluates gesture rules and manages state machines for all gestures.

    Parameters:
        preferred_hand: Which physical hand to treat as "Preferred".
            Defaults to "Right".
        configs: List of gesture configurations.  If None, the default
            set from Section 5 is used.
    """

    def __init__(
        self,
        preferred_hand: str = "Right",
        configs: list[GestureConfig] | None = None,
    ) -> None:
        self._preferred_hand = preferred_hand
        self._configs: list[GestureConfig] = configs if configs is not None else _default_configs()
        # One state machine per (gesture_id, hand) pair.
        self._machines: dict[tuple[str, str], GestureStateMachine] = {}
        self.dynamic_recognizer = DynamicGestureRecognizer()
        self._build_machines()

    # ---------------------------------------------------------------- public

    @property
    def preferred_hand(self) -> str:
        return self._preferred_hand

    @preferred_hand.setter
    def preferred_hand(self, value: str) -> None:
        self._preferred_hand = value

    def update(
        self,
        features_by_hand: dict[str, HandFeatures],
        timestamp: float,
        landmarks_by_hand: dict[str, list[tuple[float, float]]] | None = None,
    ) -> list[GestureEvent]:
        """Process one frame and return all emitted gesture events.

        Args:
            features_by_hand: Dict mapping "Left"/"Right" → HandFeatures.
                              Pass an empty dict when no hands are detected.
            timestamp: Current monotonic timestamp.
            landmarks_by_hand: Optional raw normalized coordinates for dynamic gestures.

        Returns:
            List of ``GestureEvent`` objects (may be empty).
        """
        events: list[GestureEvent] = []

        # If no hands detected: cancel all active gestures (Rule 3).
        if not features_by_hand:
            events.extend(self._cancel_all(timestamp))
            return events

        feat_left = features_by_hand.get("Left")
        feat_right = features_by_hand.get("Right")

        for cfg in self._configs:
            if not cfg.enabled:
                continue

            actual_hand = self._resolve_hand(cfg.hand)

            if cfg.hand == "Both":
                # Two-hand gesture.
                matched = rule_two_open_palms(feat_left, feat_right)
                key = (cfg.gesture_id, "Both")
                sm = self._get_machine(key, cfg, "Both")
                events.extend(sm.update(matched, timestamp))

            elif cfg.hand == "Any":
                # Single-hand gesture valid for either hand.
                for hand_label in ("Left", "Right"):
                    features = features_by_hand.get(hand_label)
                    key = (cfg.gesture_id, hand_label)
                    if features is None:
                        if key in self._machines:
                            events.extend(self._machines[key].cancel(timestamp))
                        continue
                    matched = self._evaluate_rule(cfg, features)
                    sm = self._get_machine(key, cfg, hand_label)
                    payload = self._build_payload(cfg, features)
                    events.extend(
                        sm.update(matched, timestamp, features.is_valid and 1.0 or 0.0, payload)
                    )

            else:
                # Single-hand gesture — only evaluate if the hand is present.
                features = features_by_hand.get(actual_hand)
                if features is None:
                    # Cancel if active.
                    key = (cfg.gesture_id, actual_hand)
                    if key in self._machines:
                        events.extend(self._machines[key].cancel(timestamp))
                    continue

                matched = self._evaluate_rule(cfg, features)
                key = (cfg.gesture_id, actual_hand)
                sm = self._get_machine(key, cfg, actual_hand)
                payload = self._build_payload(cfg, features)
                events.extend(
                    sm.update(matched, timestamp, features.is_valid and 1.0 or 0.0, payload)
                )

        # Dynamic gestures evaluation
        if landmarks_by_hand:
            dyn_events = self.dynamic_recognizer.update(
                features_by_hand, landmarks_by_hand, timestamp
            )
            events.extend(dyn_events)

        return events

    def cancel_all(self, timestamp: float) -> list[GestureEvent]:
        """Public interface to cancel all active gestures (e.g. on pause)."""
        return self._cancel_all(timestamp)

    def get_active_gestures(self) -> list[str]:
        """Return IDs of currently active gestures."""
        return [
            f"{gesture_id}/{hand}"
            for (gesture_id, hand), sm in self._machines.items()
            if sm.state == GestureState.ACTIVE
        ]

    # --------------------------------------------------------------- private

    def _resolve_hand(self, hand_spec: str) -> str:
        """Resolve "Preferred" to an actual hand label."""
        if hand_spec == "Preferred":
            return self._preferred_hand
        return hand_spec

    def _build_machines(self) -> None:
        """Pre-build state machines for all single-hand gestures."""
        for cfg in self._configs:
            if cfg.hand == "Both":
                key = (cfg.gesture_id, "Both")
                self._machines[key] = GestureStateMachine(cfg, "Both")
            elif cfg.hand == "Any":
                for hand in ("Left", "Right"):
                    key = (cfg.gesture_id, hand)
                    self._machines[key] = GestureStateMachine(cfg, hand)
            else:
                for hand in ("Left", "Right"):
                    # Only build for the resolved hand.
                    actual = self._resolve_hand(cfg.hand)
                    if hand == actual:
                        key = (cfg.gesture_id, hand)
                        self._machines[key] = GestureStateMachine(cfg, hand)

    def _get_machine(
        self,
        key: tuple[str, str],
        cfg: GestureConfig,
        hand: str,
    ) -> GestureStateMachine:
        if key not in self._machines:
            self._machines[key] = GestureStateMachine(cfg, hand)
        return self._machines[key]

    def _evaluate_rule(self, cfg: GestureConfig, features: HandFeatures) -> bool:
        """Apply the geometric rule for a given gesture config."""
        gid = cfg.gesture_id

        if gid == "point":
            return rule_point(features)

        if gid == "fist":
            return rule_fist(features)

        if gid == "two_fingers":
            return rule_two_fingers(features)

        if gid in ("pinch_index", "pinch_sustained"):
            key = (gid, self._resolve_hand(cfg.hand))
            sm = self._machines.get(key)
            currently = sm is not None and sm.is_active
            return rule_pinch_index(
                features,
                cfg.enter_threshold,
                cfg.exit_threshold,
                currently,
            )

        if gid == "pinch_middle":
            key = (gid, self._resolve_hand(cfg.hand))
            sm = self._machines.get(key)
            currently = sm is not None and sm.is_active
            return rule_pinch_middle(
                features,
                cfg.enter_threshold,
                cfg.exit_threshold,
                currently,
            )

        logger.warning("Unknown gesture id '%s'", gid)
        return False

    def _build_payload(self, cfg: GestureConfig, features: HandFeatures) -> dict:
        """Build the payload dict for gesture events."""
        payload: dict = {}
        if features.velocity:
            payload["vx"] = features.velocity[0]
            payload["vy"] = features.velocity[1]
        if cfg.gesture_id in ("pinch_index", "pinch_middle", "pinch_sustained"):
            dist_key = "thumb_index" if cfg.gesture_id != "pinch_middle" else "thumb_middle"
            dist = features.normalized_distances.get(dist_key, 0.0)
            payload["pinch_dist"] = dist
        return payload

    def _cancel_all(self, timestamp: float) -> list[GestureEvent]:
        events: list[GestureEvent] = []
        for sm in self._machines.values():
            events.extend(sm.cancel(timestamp))
        self.dynamic_recognizer.reset()
        return events
