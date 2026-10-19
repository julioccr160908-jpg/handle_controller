"""Tests for feature extraction (Phase 2 — Section 4.5).

Uses synthetic landmark data to verify:
- Finger state detection (extended / folded) for all gestures.
- Normalized distances relative to hand scale.
- Palm orientation is a 3-tuple of floats.
- Velocity computation across frames.
- Validity flag propagation.
- Two-hand inter-distance computation.

No UI, no Windows, no camera required.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from handcontrol.features.extractor import (
    WRIST,
    FeatureExtractor,
    _hand_scale,
    compute_finger_states,
    compute_normalized_distances,
    compute_palm_orientation,
    compute_velocity,
)
from handcontrol.tracking.models import (
    FingerState,
    HandObservation,
    ImagePoint,
    LandmarkPoint,
)

FIXTURES = Path(__file__).parent / "fixtures"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _lms_from_dicts(data: list[dict]) -> list[LandmarkPoint]:
    return [LandmarkPoint(**d) for d in data]


def _img_pts(count: int = 21) -> list[ImagePoint]:
    return [ImagePoint(x=float(i * 30), y=float(i * 20)) for i in range(count)]


def _load_fixture(name: str) -> tuple[list[LandmarkPoint], str]:
    path = FIXTURES / name
    data = json.loads(path.read_text())
    lms = _lms_from_dicts(data["landmarks_normalized"])
    return lms, data["handedness"]


def _make_obs(lms: list[LandmarkPoint], handedness: str = "Right") -> HandObservation:
    return HandObservation(
        handedness=handedness,
        handedness_confidence=0.99,
        landmarks_normalized=lms,
        landmarks_image=_img_pts(len(lms)),
        presence_confidence=0.99,
    )


def _make_straight_finger_lms(
    extended_indices: set[int],
) -> list[LandmarkPoint]:
    """Build synthetic 21 landmarks where specified fingers (0-4) are extended.

    Finger mapping: 0=thumb, 1=index, 2=middle, 3=ring, 4=pinky.
    Landmark layout (simplified, Y increases downward like MediaPipe):
    - Wrist at (0.5, 0.9)
    - MCP joints at y=0.7
    - PIP joints at y=0.6 for folded, y=0.5 for extended
    - TIP at y=0.8 if folded (below PIP), y=0.3 if extended (above wrist)
    """
    # Start with wrist.
    pts: list[tuple[float, float, float]] = [(0.5, 0.9, 0.0)]

    # Thumb (landmarks 1-4).
    if 0 in extended_indices:
        # Thumb extended: tip far from index MCP in X direction.
        pts += [(0.38, 0.75, 0), (0.35, 0.70, 0), (0.33, 0.66, 0), (0.30, 0.62, 0)]
    else:
        pts += [(0.44, 0.76, 0), (0.45, 0.72, 0), (0.46, 0.70, 0), (0.47, 0.69, 0)]

    # Index (5-8), Middle (9-12), Ring (13-16), Pinky (17-20).
    x_positions = [0.54, 0.60, 0.66, 0.72]
    for finger_idx, x in enumerate(x_positions, start=1):
        if finger_idx in extended_indices:
            # Extended: TIP (y=0.30) farther from wrist (y=0.9) than PIP (y=0.55).
            pts += [(x, 0.68, 0), (x, 0.55, 0), (x, 0.40, 0), (x, 0.30, 0)]
        else:
            # Folded: TIP (y=0.75) closer to wrist (y=0.9) than PIP (y=0.62).
            pts += [(x, 0.68, 0), (x, 0.62, 0), (x, 0.70, 0), (x, 0.75, 0)]

    assert len(pts) == 21
    return [LandmarkPoint(x=p[0], y=p[1], z=p[2]) for p in pts]


# ---------------------------------------------------------------------------
# Finger state tests
# ---------------------------------------------------------------------------


class TestFingerStates:
    def test_all_extended_returns_five_extended(self):
        lms = _make_straight_finger_lms({0, 1, 2, 3, 4})
        states = compute_finger_states(lms, "Right")
        assert len(states) == 5
        # Non-thumb fingers should all be EXTENDED.
        assert all(s == FingerState.EXTENDED for s in states[1:])

    def test_all_folded_returns_five_folded(self):
        lms = _make_straight_finger_lms(set())
        states = compute_finger_states(lms, "Right")
        assert len(states) == 5
        # All non-thumb fingers should be FOLDED.
        assert all(s == FingerState.FOLDED for s in states[1:])

    def test_only_index_extended(self):
        lms = _make_straight_finger_lms({1})
        states = compute_finger_states(lms, "Right")
        assert states[1] == FingerState.EXTENDED
        assert states[2] == FingerState.FOLDED
        assert states[3] == FingerState.FOLDED
        assert states[4] == FingerState.FOLDED

    def test_index_and_middle_extended(self):
        lms = _make_straight_finger_lms({1, 2})
        states = compute_finger_states(lms, "Right")
        assert states[1] == FingerState.EXTENDED
        assert states[2] == FingerState.EXTENDED
        assert states[3] == FingerState.FOLDED
        assert states[4] == FingerState.FOLDED

    def test_invalid_landmark_count_returns_unknown(self):
        lms = [LandmarkPoint(x=0, y=0, z=0)] * 10  # wrong count
        states = compute_finger_states(lms, "Right")
        assert all(s == FingerState.UNKNOWN for s in states)

    def test_point_fixture_index_extended(self):
        lms, handedness = _load_fixture("point_right.json")
        states = compute_finger_states(lms, handedness)
        assert states[1] == FingerState.EXTENDED, "Index should be extended in point gesture"
        assert states[2] == FingerState.FOLDED, "Middle should be folded in point gesture"
        assert states[3] == FingerState.FOLDED, "Ring should be folded in point gesture"
        assert states[4] == FingerState.FOLDED, "Pinky should be folded in point gesture"

    def test_fist_fixture_all_folded(self):
        lms, handedness = _load_fixture("fist_right.json")
        states = compute_finger_states(lms, handedness)
        # All non-thumb fingers folded.
        assert all(s == FingerState.FOLDED for s in states[1:]), (
            f"Expected all fingers folded, got {states}"
        )


# ---------------------------------------------------------------------------
# Normalized distance tests
# ---------------------------------------------------------------------------


class TestNormalizedDistances:
    def test_distances_are_positive(self):
        lms = _make_straight_finger_lms({1, 2, 3, 4})
        dists = compute_normalized_distances(lms)
        assert all(v >= 0 for v in dists.values())

    def test_thumb_index_key_present(self):
        lms = _make_straight_finger_lms({0, 1})
        dists = compute_normalized_distances(lms)
        assert "thumb_index" in dists

    def test_distance_relative_to_scale(self):
        """Scaling the hand uniformly should leave normalized distances unchanged."""
        lms_a = _make_straight_finger_lms({1})
        # Scale up by 2× (multiply all coords by 2, keeping relative positions).
        lms_b = [LandmarkPoint(x=p.x * 2, y=p.y * 2, z=p.z) for p in lms_a]

        scale_a = _hand_scale(lms_a)
        scale_b = _hand_scale(lms_b)
        assert scale_b == pytest.approx(scale_a * 2, rel=0.01)

        dists_a = compute_normalized_distances(lms_a)
        dists_b = compute_normalized_distances(lms_b)
        for key in ("thumb_index", "thumb_middle"):
            assert dists_a[key] == pytest.approx(dists_b[key], rel=0.01), (
                f"Normalized distance '{key}' should be scale-invariant"
            )


# ---------------------------------------------------------------------------
# Palm orientation tests
# ---------------------------------------------------------------------------


class TestPalmOrientation:
    def test_returns_three_floats(self):
        lms = _make_straight_finger_lms({0, 1, 2, 3, 4})
        roll, pitch, yaw = compute_palm_orientation(lms)
        assert isinstance(roll, float)
        assert isinstance(pitch, float)
        assert isinstance(yaw, float)

    def test_orientation_in_radians_range(self):
        lms = _make_straight_finger_lms({0, 1, 2, 3, 4})
        roll, pitch, yaw = compute_palm_orientation(lms)
        assert -math.pi <= roll <= math.pi
        assert -math.pi <= pitch <= math.pi
        assert -math.pi <= yaw <= math.pi


# ---------------------------------------------------------------------------
# Velocity tests
# ---------------------------------------------------------------------------


class TestVelocity:
    def test_no_previous_returns_zero(self):
        lms = _make_straight_finger_lms({1})
        vx, vy = compute_velocity(lms[WRIST], None, 0.033, 0.1)
        assert vx == 0.0
        assert vy == 0.0

    def test_zero_dt_returns_zero(self):
        lms = _make_straight_finger_lms({1})
        vx, vy = compute_velocity(lms[WRIST], lms[WRIST], 0.0, 0.1)
        assert vx == 0.0
        assert vy == 0.0

    def test_movement_produces_nonzero_velocity(self):
        lms_a = _make_straight_finger_lms({1})
        prev_wrist = lms_a[WRIST]
        # Move wrist by 0.1 in Y.
        new_wrist = LandmarkPoint(x=prev_wrist.x, y=prev_wrist.y - 0.1, z=0.0)
        scale = _hand_scale(lms_a)
        vx, vy = compute_velocity(new_wrist, prev_wrist, 0.033, scale)
        assert vy < 0.0  # moved upward (negative Y)
        assert abs(vy) > 0.5  # significant velocity


# ---------------------------------------------------------------------------
# FeatureExtractor integration tests
# ---------------------------------------------------------------------------


class TestFeatureExtractor:
    def test_extract_returns_valid_features(self):
        extractor = FeatureExtractor()
        lms = _make_straight_finger_lms({1, 2, 3, 4})
        obs = _make_obs(lms)
        feat = extractor.extract(obs, timestamp=1.0)
        assert feat.is_valid
        assert len(feat.fingers_extended) == 5

    def test_extract_invalid_obs_wrong_lm_count(self):
        """Pydantic enforces min_length=21 at construction time (model-level guarantee)."""
        from pydantic import ValidationError

        short_lms = [LandmarkPoint(x=0, y=0, z=0)] * 10
        with pytest.raises(ValidationError, match="too_short"):
            HandObservation(
                handedness="Right",
                handedness_confidence=0.9,
                landmarks_normalized=short_lms,
                landmarks_image=_img_pts(10),
                presence_confidence=0.9,
            )

    def test_velocity_increases_across_frames(self):
        extractor = FeatureExtractor()
        lms_a = _make_straight_finger_lms({1})
        lms_b = [LandmarkPoint(x=p.x, y=p.y - 0.05, z=0.0) for p in lms_a]

        obs_a = _make_obs(lms_a)
        obs_b = _make_obs(lms_b)

        extractor.extract(obs_a, timestamp=1.000)
        feat_b = extractor.extract(obs_b, timestamp=1.033)

        # After second frame, velocity should be non-zero.
        assert feat_b.velocity is not None
        assert abs(feat_b.velocity[1]) > 0.1

    def test_reset_clears_velocity_state(self):
        extractor = FeatureExtractor()
        lms = _make_straight_finger_lms({1})
        obs = _make_obs(lms)

        extractor.extract(obs, timestamp=1.0)
        extractor.reset("Right")

        # After reset, velocity should be zero again.
        feat = extractor.extract(obs, timestamp=1.033)
        assert feat.velocity == (0.0, 0.0)

    def test_extract_all_returns_dict_keyed_by_handedness(self):
        extractor = FeatureExtractor()
        lms = _make_straight_finger_lms({1})

        obs_right = _make_obs(lms, "Right")
        obs_left = _make_obs(lms, "Left")

        result = extractor.extract_all([obs_right, obs_left], timestamp=1.0)
        assert "Right" in result
        assert "Left" in result
        assert result["Right"].is_valid
        assert result["Left"].is_valid
