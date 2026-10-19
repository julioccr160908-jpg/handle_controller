"""Feature extraction from hand landmarks (Section 4.5).

Computes geometric features from raw ``HandObservation`` data:

- Finger states (extended / folded) for all 5 fingers.
- Normalized distances between fingertips (relative to palm width).
- Palm orientation (roll, pitch, yaw estimates).
- Frame-to-frame velocity of the wrist (normalized).
- Data validity flag.

All measurements are relative to the hand's own scale (palm width or
wrist-to-middle-base distance) — never absolute pixel coordinates
(Section 4.5, Rule).
"""

from __future__ import annotations

import math

from handcontrol.tracking.models import (
    FingerState,
    HandFeatures,
    HandObservation,
    LandmarkPoint,
)

# ---------------------------------------------------------------------------
# MediaPipe Hand Landmark indices (21 landmarks)
# ---------------------------------------------------------------------------
# Reference: https://ai.google.dev/mediapipe/solutions/vision/hand_landmarker
WRIST = 0
THUMB_CMC = 1
THUMB_MCP = 2
THUMB_IP = 3
THUMB_TIP = 4
INDEX_MCP = 5
INDEX_PIP = 6
INDEX_DIP = 7
INDEX_TIP = 8
MIDDLE_MCP = 9
MIDDLE_PIP = 10
MIDDLE_DIP = 11
MIDDLE_TIP = 12
RING_MCP = 13
RING_PIP = 14
RING_DIP = 15
RING_TIP = 16
PINKY_MCP = 17
PINKY_PIP = 18
PINKY_DIP = 19
PINKY_TIP = 20


# ---------------------------------------------------------------------------
# Helper geometry
# ---------------------------------------------------------------------------


def _dist2d(a: LandmarkPoint, b: LandmarkPoint) -> float:
    """Euclidean distance in the XY plane (normalized coords)."""
    return math.hypot(a.x - b.x, a.y - b.y)


def _dist3d(a: LandmarkPoint, b: LandmarkPoint) -> float:
    """Euclidean distance in 3D (normalized coords)."""
    return math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2)


def _palm_width(lms: list[LandmarkPoint]) -> float:
    """Distance between INDEX_MCP and PINKY_MCP — proxy for palm width."""
    return _dist2d(lms[INDEX_MCP], lms[PINKY_MCP])


def _wrist_to_middle_base(lms: list[LandmarkPoint]) -> float:
    """Distance from WRIST to MIDDLE_MCP — proxy for palm length."""
    return _dist2d(lms[WRIST], lms[MIDDLE_MCP])


def _hand_scale(lms: list[LandmarkPoint]) -> float:
    """Reference scale for normalization.

    Uses the larger of palm width and wrist-to-middle-base distance
    to be robust against different hand orientations.
    """
    return max(_palm_width(lms), _wrist_to_middle_base(lms), 1e-6)


# ---------------------------------------------------------------------------
# Finger state detection
# ---------------------------------------------------------------------------

# Each non-thumb finger: (MCP, PIP, DIP, TIP) indices.
_FINGER_JOINTS: list[tuple[int, int, int, int]] = [
    (INDEX_MCP, INDEX_PIP, INDEX_DIP, INDEX_TIP),
    (MIDDLE_MCP, MIDDLE_PIP, MIDDLE_DIP, MIDDLE_TIP),
    (RING_MCP, RING_PIP, RING_DIP, RING_TIP),
    (PINKY_MCP, PINKY_PIP, PINKY_DIP, PINKY_TIP),
]


def _is_finger_extended(
    lms: list[LandmarkPoint],
    mcp: int,
    pip_: int,
    dip: int,
    tip: int,
) -> bool:
    """Determine if a non-thumb finger is extended.

    A finger is considered extended when the tip is farther from the wrist
    than the PIP joint (in 2D).  This is a simple but robust geometric
    criterion that works across different hand orientations.
    """
    return _dist2d(lms[WRIST], lms[tip]) > _dist2d(lms[WRIST], lms[pip_])


def _is_thumb_extended(lms: list[LandmarkPoint], handedness: str) -> bool:
    """Determine if the thumb is extended.

    The thumb is extended when its tip is farther from the palm center
    (MIDDLE_MCP) in the X-axis direction than the THUMB_IP joint.
    Handedness matters because the thumb sticks out in opposite
    X-directions for left and right hands.
    """
    # Compare thumb tip vs thumb IP distance from the index MCP in X.
    # For a right hand (user's right), extended thumb tip is farther left (lower X).
    # For a left hand, extended thumb tip is farther right (higher X).
    if handedness == "Right":
        return lms[THUMB_TIP].x < lms[THUMB_IP].x
    else:
        return lms[THUMB_TIP].x > lms[THUMB_IP].x


def compute_finger_states(
    lms: list[LandmarkPoint],
    handedness: str,
) -> list[FingerState]:
    """Return the state of all 5 fingers: [thumb, index, middle, ring, pinky].

    Args:
        lms: 21 normalized landmarks.
        handedness: "Left" or "Right".

    Returns:
        List of 5 ``FingerState`` values.
    """
    if len(lms) != 21:
        return [FingerState.UNKNOWN] * 5

    states: list[FingerState] = []

    # Thumb.
    try:
        thumb_ext = _is_thumb_extended(lms, handedness)
        states.append(FingerState.EXTENDED if thumb_ext else FingerState.FOLDED)
    except Exception:
        states.append(FingerState.UNKNOWN)

    # Other four fingers.
    for mcp, pip_, dip, tip in _FINGER_JOINTS:
        try:
            ext = _is_finger_extended(lms, mcp, pip_, dip, tip)
            states.append(FingerState.EXTENDED if ext else FingerState.FOLDED)
        except Exception:
            states.append(FingerState.UNKNOWN)

    return states


# ---------------------------------------------------------------------------
# Normalized distances
# ---------------------------------------------------------------------------


def compute_normalized_distances(lms: list[LandmarkPoint]) -> dict[str, float]:
    """Compute key distances normalized by hand scale.

    Returns a dict with entries like:
    - ``thumb_index``: thumb tip to index tip
    - ``thumb_middle``: thumb tip to middle tip
    - ``thumb_ring``: thumb tip to ring tip
    - ``thumb_pinky``: thumb tip to pinky tip
    - ``index_middle``: index tip to middle tip
    - ``palm_width``: raw palm width (unnormalized, but included for reference)
    """
    scale = _hand_scale(lms)

    return {
        "thumb_index": _dist2d(lms[THUMB_TIP], lms[INDEX_TIP]) / scale,
        "thumb_middle": _dist2d(lms[THUMB_TIP], lms[MIDDLE_TIP]) / scale,
        "thumb_ring": _dist2d(lms[THUMB_TIP], lms[RING_TIP]) / scale,
        "thumb_pinky": _dist2d(lms[THUMB_TIP], lms[PINKY_TIP]) / scale,
        "index_middle": _dist2d(lms[INDEX_TIP], lms[MIDDLE_TIP]) / scale,
        "palm_width_raw": _palm_width(lms),
    }


# ---------------------------------------------------------------------------
# Palm orientation (simplified roll / pitch / yaw)
# ---------------------------------------------------------------------------


def compute_palm_orientation(
    lms: list[LandmarkPoint],
) -> tuple[float, float, float]:
    """Estimate palm orientation as (roll, pitch, yaw) in radians.

    Uses vectors formed by wrist→middle_mcp and index_mcp→pinky_mcp
    to approximate the palm plane.

    Returns:
        (roll, pitch, yaw) tuple in radians.
    """
    # Vector from wrist to middle MCP (up direction of palm).
    dx = lms[MIDDLE_MCP].x - lms[WRIST].x
    dy = lms[MIDDLE_MCP].y - lms[WRIST].y
    dz = lms[MIDDLE_MCP].z - lms[WRIST].z

    # Yaw: rotation around vertical axis.
    yaw = math.atan2(dx, -dy)  # 0 when hand points up

    # Pitch: tilt forward / backward (uses Z depth).
    pitch = math.atan2(dz, math.hypot(dx, dy))

    # Roll: lateral tilt — use the cross-palm vector.
    cx = lms[PINKY_MCP].x - lms[INDEX_MCP].x
    cy = lms[PINKY_MCP].y - lms[INDEX_MCP].y
    roll = math.atan2(cy, cx)

    return (roll, pitch, yaw)


# ---------------------------------------------------------------------------
# Velocity computation
# ---------------------------------------------------------------------------


def compute_velocity(
    current_wrist: LandmarkPoint,
    previous_wrist: LandmarkPoint | None,
    dt: float,
    scale: float,
) -> tuple[float, float]:
    """Compute wrist velocity in normalized units per second.

    Args:
        current_wrist: Current wrist position (normalized).
        previous_wrist: Previous wrist position, or None.
        dt: Time delta in seconds.
        scale: Hand scale for normalization.

    Returns:
        (vx, vy) velocity in hand-scale units per second.
    """
    if previous_wrist is None or dt <= 0 or scale < 1e-6:
        return (0.0, 0.0)

    vx = (current_wrist.x - previous_wrist.x) / (scale * dt)
    vy = (current_wrist.y - previous_wrist.y) / (scale * dt)
    return (vx, vy)


# ---------------------------------------------------------------------------
# Two-hand distance
# ---------------------------------------------------------------------------


def compute_inter_hand_distance(
    obs_a: HandObservation,
    obs_b: HandObservation,
) -> float:
    """Compute normalized distance between two hands.

    Uses the wrist-to-wrist distance normalized by the average of both
    hands' scales.
    """
    lms_a = obs_a.landmarks_normalized
    lms_b = obs_b.landmarks_normalized
    avg_scale = (_hand_scale(lms_a) + _hand_scale(lms_b)) / 2.0
    if avg_scale < 1e-6:
        return 0.0
    return _dist2d(lms_a[WRIST], lms_b[WRIST]) / avg_scale


# ---------------------------------------------------------------------------
# Main extractor
# ---------------------------------------------------------------------------


class FeatureExtractor:
    """Extracts ``HandFeatures`` from ``HandObservation`` objects.

    Maintains per-hand state for velocity computation across frames.
    Fully independent of UI, actions, and Windows (Section 4.6 requirement).
    """

    def __init__(self) -> None:
        # Previous wrist positions keyed by handedness.
        self._prev_wrist: dict[str, LandmarkPoint] = {}
        self._prev_timestamp: dict[str, float] = {}

    def extract(
        self,
        obs: HandObservation,
        timestamp: float,
    ) -> HandFeatures:
        """Extract features from a single hand observation.

        Args:
            obs: A ``HandObservation`` with 21 landmarks.
            timestamp: Monotonic timestamp of the frame.

        Returns:
            Populated ``HandFeatures`` with all computed values.
        """
        lms = obs.landmarks_normalized
        handedness = obs.handedness

        if len(lms) != 21:
            return HandFeatures(
                fingers_extended=[FingerState.UNKNOWN] * 5,
                is_valid=False,
            )

        # Finger states.
        fingers = compute_finger_states(lms, handedness)

        # Normalized distances.
        distances = compute_normalized_distances(lms)

        # Palm orientation.
        orientation = compute_palm_orientation(lms)

        # Velocity.
        scale = _hand_scale(lms)
        prev_w = self._prev_wrist.get(handedness)
        prev_t = self._prev_timestamp.get(handedness, timestamp)
        dt = timestamp - prev_t
        vel = compute_velocity(lms[WRIST], prev_w, dt, scale)

        # Update state.
        self._prev_wrist[handedness] = lms[WRIST]
        self._prev_timestamp[handedness] = timestamp

        return HandFeatures(
            fingers_extended=fingers,
            normalized_distances=distances,
            palm_orientation=orientation,
            velocity=vel,
            is_valid=True,
        )

    def reset(self, handedness: str | None = None) -> None:
        """Reset velocity tracking state.

        Args:
            handedness: Reset only a specific hand. ``None`` resets all.
        """
        if handedness is None:
            self._prev_wrist.clear()
            self._prev_timestamp.clear()
        else:
            self._prev_wrist.pop(handedness, None)
            self._prev_timestamp.pop(handedness, None)

    def extract_all(
        self,
        observations: list[HandObservation],
        timestamp: float,
    ) -> dict[str, HandFeatures]:
        """Extract features for all observed hands.

        Returns:
            Dict keyed by handedness ("Left"/"Right") → HandFeatures.
        """
        result: dict[str, HandFeatures] = {}
        for obs in observations:
            result[obs.handedness] = self.extract(obs, timestamp)
        return result
