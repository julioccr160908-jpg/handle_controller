"""Per-gesture state machine (Section 4.6).

Implements the lifecycle: IDLE → CANDIDATE → ACTIVE → RELEASING → IDLE

- IDLE: Gesture is not detected.
- CANDIDATE: Geometric rule matched, waiting for ``min_confirm_time_s``.
- ACTIVE: Gesture confirmed. Emits STARTED, then HELD (if continuous).
- RELEASING: Geometric rule no longer matched, waiting for ``min_release_time_s``.
  If rule matches again before timeout → back to ACTIVE (hysteresis).

Timing:
- ``min_confirm_time_s``: prevents accidental activations.
- ``min_release_time_s``: prevents flicker at the exit boundary.
- ``cooldown_s``: prevents re-triggering immediately after ENDED.
- Hysteresis: ``enter_threshold`` ≠ ``exit_threshold``.

This module is independent of UI, mapping, actions, and Windows.
"""

from __future__ import annotations

import logging
from enum import StrEnum

from handcontrol.gestures.config import GestureConfig
from handcontrol.tracking.models import GestureEvent, GesturePhase

logger = logging.getLogger(__name__)


class GestureState(StrEnum):
    """Internal state of a gesture recognizer."""

    IDLE = "IDLE"
    CANDIDATE = "CANDIDATE"
    ACTIVE = "ACTIVE"
    RELEASING = "RELEASING"
    COOLDOWN = "COOLDOWN"


class GestureStateMachine:
    """State machine for a single gesture.

    Call ``update(matched, timestamp, confidence)`` each frame.
    Returns a list of ``GestureEvent`` objects (0 or 1 per call typically).
    """

    def __init__(self, config: GestureConfig, hand: str) -> None:
        """Initialize the state machine.

        Args:
            config: Gesture configuration with thresholds and timing.
            hand: The actual hand this state machine is tracking
                  ("Left" or "Right").
        """
        self.config = config
        self.hand = hand
        self._state = GestureState.IDLE
        self._entered_at: float = 0.0  # timestamp of entering current state
        self._cooldown_until: float = 0.0

    @property
    def state(self) -> GestureState:
        """Current state of the gesture."""
        return self._state

    @property
    def is_active(self) -> bool:
        """True if the gesture is in ACTIVE or RELEASING state."""
        return self._state in (GestureState.ACTIVE, GestureState.RELEASING)

    def update(
        self,
        matched: bool,
        timestamp: float,
        confidence: float = 1.0,
        payload: dict | None = None,
    ) -> list[GestureEvent]:
        """Advance the state machine by one frame.

        Args:
            matched: Whether the geometric rule matches this frame.
            timestamp: Current monotonic timestamp.
            confidence: Hand detection confidence.
            payload: Extra data to include in events (e.g. displacement).

        Returns:
            List of ``GestureEvent`` objects emitted this frame (usually 0 or 1).
        """
        events: list[GestureEvent] = []
        payload = payload or {}

        if self._state == GestureState.IDLE:
            events.extend(self._handle_idle(matched, timestamp))

        elif self._state == GestureState.COOLDOWN:
            events.extend(self._handle_cooldown(matched, timestamp))

        elif self._state == GestureState.CANDIDATE:
            events.extend(self._handle_candidate(matched, timestamp, confidence, payload))

        elif self._state == GestureState.ACTIVE:
            events.extend(self._handle_active(matched, timestamp, confidence, payload))

        elif self._state == GestureState.RELEASING:
            events.extend(self._handle_releasing(matched, timestamp, confidence, payload))

        return events

    def cancel(self, timestamp: float) -> list[GestureEvent]:
        """Force-cancel the gesture (e.g. hand lost, pause, emergency).

        Returns any CANCELLED event.
        """
        events: list[GestureEvent] = []

        if self._state in (GestureState.ACTIVE, GestureState.RELEASING):
            events.append(self._emit(GesturePhase.CANCELLED, timestamp, 0.0, {}))
            self._transition(GestureState.COOLDOWN, timestamp)
        elif self._state == GestureState.CANDIDATE:
            self._transition(GestureState.IDLE, timestamp)

        return events

    # ---------------------------------------------------------------- states

    def _handle_idle(self, matched: bool, timestamp: float) -> list[GestureEvent]:
        if matched:
            self._transition(GestureState.CANDIDATE, timestamp)
        return []

    def _handle_cooldown(self, matched: bool, timestamp: float) -> list[GestureEvent]:
        if timestamp >= self._cooldown_until:
            self._transition(GestureState.IDLE, timestamp)
            # Re-evaluate in IDLE state immediately.
            if matched:
                self._transition(GestureState.CANDIDATE, timestamp)
        return []

    def _handle_candidate(
        self,
        matched: bool,
        timestamp: float,
        confidence: float,
        payload: dict,
    ) -> list[GestureEvent]:
        events: list[GestureEvent] = []

        if not matched:
            # Lost match before confirmation — go back to IDLE.
            self._transition(GestureState.IDLE, timestamp)
            return events

        elapsed = timestamp - self._entered_at
        if elapsed >= self.config.min_confirm_time_s:
            # Confirmed! Transition to ACTIVE, emit STARTED.
            self._transition(GestureState.ACTIVE, timestamp)
            events.append(self._emit(GesturePhase.STARTED, timestamp, confidence, payload))

        return events

    def _handle_active(
        self,
        matched: bool,
        timestamp: float,
        confidence: float,
        payload: dict,
    ) -> list[GestureEvent]:
        events: list[GestureEvent] = []

        if not matched:
            # Start releasing.
            self._transition(GestureState.RELEASING, timestamp)
            return events

        # Still active — emit HELD for continuous gestures.
        if self.config.is_continuous:
            events.append(self._emit(GesturePhase.HELD, timestamp, confidence, payload))

        return events

    def _handle_releasing(
        self,
        matched: bool,
        timestamp: float,
        confidence: float,
        payload: dict,
    ) -> list[GestureEvent]:
        events: list[GestureEvent] = []

        if matched:
            # Hysteresis: re-matched before release timeout → back to ACTIVE.
            self._transition(GestureState.ACTIVE, timestamp)
            if self.config.is_continuous:
                events.append(self._emit(GesturePhase.HELD, timestamp, confidence, payload))
            return events

        elapsed = timestamp - self._entered_at
        if elapsed >= self.config.min_release_time_s:
            # Release confirmed — emit ENDED.
            events.append(self._emit(GesturePhase.ENDED, timestamp, confidence, payload))
            self._transition(GestureState.COOLDOWN, timestamp)

        return events

    # --------------------------------------------------------------- helpers

    def _transition(self, new_state: GestureState, timestamp: float) -> None:
        old = self._state
        self._state = new_state
        self._entered_at = timestamp

        if new_state == GestureState.COOLDOWN:
            self._cooldown_until = timestamp + self.config.cooldown_s

        logger.debug(
            "Gesture '%s' [%s]: %s → %s",
            self.config.gesture_id,
            self.hand,
            old,
            new_state,
        )

    def _emit(
        self,
        phase: GesturePhase,
        timestamp: float,
        confidence: float,
        payload: dict,
    ) -> GestureEvent:
        return GestureEvent(
            gesture_id=self.config.gesture_id,
            phase=phase,
            hand=self.hand,
            timestamp=timestamp,
            confidence=confidence,
            payload=payload,
        )
