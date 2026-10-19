"""Tests for data contracts (Section 4.2).

Validates that Pydantic models enforce constraints correctly:
- Confidence bounds [0, 1]
- Landmark counts (exactly 21)
- Maximum 2 hands per frame
- Non-negative frame IDs
- Hand loss scenarios
"""

from __future__ import annotations

import time

import pytest
from pydantic import ValidationError

from handcontrol.tracking.models import (
    ActionRequest,
    ActionResult,
    ActionStatus,
    FingerState,
    GestureEvent,
    GesturePhase,
    HandFeatures,
    HandFrame,
    HandObservation,
    ImagePoint,
    LandmarkPoint,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_landmarks_normalized(count: int = 21) -> list[LandmarkPoint]:
    return [LandmarkPoint(x=i / 21, y=i / 21, z=0.0) for i in range(count)]


def _make_landmarks_image(count: int = 21) -> list[ImagePoint]:
    return [ImagePoint(x=float(i * 30), y=float(i * 20)) for i in range(count)]


def _make_hand_observation(**overrides) -> HandObservation:
    defaults = {
        "handedness": "Right",
        "handedness_confidence": 0.95,
        "landmarks_normalized": _make_landmarks_normalized(),
        "landmarks_image": _make_landmarks_image(),
        "presence_confidence": 0.9,
    }
    defaults.update(overrides)
    return HandObservation(**defaults)


# ---------------------------------------------------------------------------
# LandmarkPoint
# ---------------------------------------------------------------------------


class TestLandmarkPoint:
    def test_creation(self):
        p = LandmarkPoint(x=0.5, y=0.3, z=-0.1)
        assert p.x == 0.5
        assert p.y == 0.3
        assert p.z == pytest.approx(-0.1)

    def test_allows_out_of_range(self):
        """MediaPipe z-values can exceed [0,1], so no bounds check."""
        p = LandmarkPoint(x=1.5, y=-0.5, z=2.0)
        assert p.x == 1.5


# ---------------------------------------------------------------------------
# HandObservation
# ---------------------------------------------------------------------------


class TestHandObservation:
    def test_valid_creation(self):
        obs = _make_hand_observation()
        assert obs.handedness == "Right"
        assert obs.handedness_confidence == 0.95
        assert len(obs.landmarks_normalized) == 21
        assert len(obs.landmarks_image) == 21

    def test_left_hand(self):
        obs = _make_hand_observation(handedness="Left")
        assert obs.handedness == "Left"

    def test_confidence_too_high(self):
        with pytest.raises(ValidationError):
            _make_hand_observation(handedness_confidence=1.5)

    def test_confidence_negative(self):
        with pytest.raises(ValidationError):
            _make_hand_observation(handedness_confidence=-0.1)

    def test_presence_confidence_too_high(self):
        with pytest.raises(ValidationError):
            _make_hand_observation(presence_confidence=2.0)

    def test_too_few_landmarks(self):
        with pytest.raises(ValidationError):
            _make_hand_observation(landmarks_normalized=_make_landmarks_normalized(10))

    def test_too_many_landmarks(self):
        with pytest.raises(ValidationError):
            _make_hand_observation(landmarks_normalized=_make_landmarks_normalized(25))

    def test_too_few_image_landmarks(self):
        with pytest.raises(ValidationError):
            _make_hand_observation(landmarks_image=_make_landmarks_image(5))


# ---------------------------------------------------------------------------
# HandFrame
# ---------------------------------------------------------------------------


class TestHandFrame:
    def test_empty_hands(self):
        frame = HandFrame(timestamp=time.monotonic(), frame_id=0, hands=[])
        assert len(frame.hands) == 0

    def test_one_hand(self):
        frame = HandFrame(
            timestamp=time.monotonic(),
            frame_id=1,
            hands=[_make_hand_observation()],
        )
        assert len(frame.hands) == 1

    def test_two_hands(self):
        frame = HandFrame(
            timestamp=time.monotonic(),
            frame_id=2,
            hands=[
                _make_hand_observation(handedness="Right"),
                _make_hand_observation(handedness="Left"),
            ],
        )
        assert len(frame.hands) == 2
        assert frame.hands[0].handedness == "Right"
        assert frame.hands[1].handedness == "Left"

    def test_max_two_hands(self):
        with pytest.raises(ValidationError):
            HandFrame(
                timestamp=time.monotonic(),
                frame_id=3,
                hands=[_make_hand_observation() for _ in range(3)],
            )

    def test_negative_frame_id(self):
        with pytest.raises(ValidationError):
            HandFrame(timestamp=time.monotonic(), frame_id=-1, hands=[])

    def test_hand_loss_sequence(self):
        """Simulate hand appearing then disappearing between frames."""
        frame_empty = HandFrame(timestamp=1.0, frame_id=0, hands=[])
        frame_detected = HandFrame(timestamp=1.033, frame_id=1, hands=[_make_hand_observation()])
        frame_lost = HandFrame(timestamp=1.066, frame_id=2, hands=[])

        assert len(frame_empty.hands) == 0
        assert len(frame_detected.hands) == 1
        assert len(frame_lost.hands) == 0

    def test_handedness_can_swap(self):
        """Identity is not stable between frames (Section 4.4)."""
        f1 = HandFrame(
            timestamp=1.0,
            frame_id=1,
            hands=[_make_hand_observation(handedness="Right")],
        )
        f2 = HandFrame(
            timestamp=1.033,
            frame_id=2,
            hands=[_make_hand_observation(handedness="Left")],
        )
        assert f1.hands[0].handedness == "Right"
        assert f2.hands[0].handedness == "Left"

    def test_two_hands_appear_and_one_lost(self):
        """Two hands detected, then one disappears."""
        f1 = HandFrame(
            timestamp=1.0,
            frame_id=1,
            hands=[
                _make_hand_observation(handedness="Right"),
                _make_hand_observation(handedness="Left"),
            ],
        )
        f2 = HandFrame(
            timestamp=1.033,
            frame_id=2,
            hands=[_make_hand_observation(handedness="Right")],
        )
        assert len(f1.hands) == 2
        assert len(f2.hands) == 1


# ---------------------------------------------------------------------------
# HandFeatures (Phase 2 — interface tests)
# ---------------------------------------------------------------------------


class TestHandFeatures:
    def test_default_invalid(self):
        features = HandFeatures(fingers_extended=[FingerState.UNKNOWN] * 5)
        assert not features.is_valid

    def test_wrong_finger_count(self):
        with pytest.raises(ValidationError):
            HandFeatures(fingers_extended=[FingerState.EXTENDED] * 3)


# ---------------------------------------------------------------------------
# GestureEvent (Phase 2 — interface tests)
# ---------------------------------------------------------------------------


class TestGestureEvent:
    def test_creation(self):
        event = GestureEvent(
            gesture_id="pinch_index",
            phase=GesturePhase.STARTED,
            hand="Right",
            timestamp=time.monotonic(),
            confidence=0.9,
        )
        assert event.gesture_id == "pinch_index"
        assert event.phase == GesturePhase.STARTED

    def test_all_phases(self):
        for phase in GesturePhase:
            event = GestureEvent(
                gesture_id="test",
                phase=phase,
                hand="Left",
                timestamp=0.0,
                confidence=0.5,
            )
            assert event.phase == phase


# ---------------------------------------------------------------------------
# ActionRequest / ActionResult (Phase 3 — interface tests)
# ---------------------------------------------------------------------------


class TestActionRequestResult:
    def test_request(self):
        req = ActionRequest(
            action_name="click_left",
            parameters={"button": "left"},
            source_gesture="pinch_index",
        )
        assert req.action_name == "click_left"

    def test_result_simulated(self):
        result = ActionResult(
            action_name="click_left",
            status=ActionStatus.SIMULATED,
            reason="Simulation mode active",
        )
        assert result.status == ActionStatus.SIMULATED

    def test_result_refused(self):
        result = ActionResult(
            action_name="format_disk",
            status=ActionStatus.REFUSED,
            reason="Action not in allowlist",
        )
        assert result.status == ActionStatus.REFUSED

    def test_result_has_timestamp(self):
        result = ActionResult(
            action_name="test",
            status=ActionStatus.EXECUTED,
        )
        assert result.timestamp > 0
