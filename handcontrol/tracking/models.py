"""Data contracts for HandControl (Section 4.2).

These models define the data structures exchanged between pipeline stages.
They are defined in Phase 1 and must remain stable across all phases.
"""

from __future__ import annotations

import time
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Landmark primitives
# ---------------------------------------------------------------------------


class LandmarkPoint(BaseModel):
    """A single 3D normalized landmark point (values in [0,1] range from MediaPipe)."""

    x: float
    y: float
    z: float


class ImagePoint(BaseModel):
    """A 2D point in image coordinates (pixels)."""

    x: float
    y: float


# ---------------------------------------------------------------------------
# HandObservation & HandFrame  (used from Phase 1)
# ---------------------------------------------------------------------------


class HandObservation(BaseModel):
    """Observation of a single hand in a frame.

    Attributes:
        handedness: "Left" or "Right" (corrected for mirror flip).
        handedness_confidence: MediaPipe classification confidence [0, 1].
        landmarks_normalized: 21 MediaPipe hand landmarks in normalized coords.
        landmarks_image: 21 landmarks converted to pixel coordinates.
        presence_confidence: Confidence that the hand is actually present.
    """

    handedness: str
    handedness_confidence: float = Field(ge=0.0, le=1.0)
    landmarks_normalized: list[LandmarkPoint] = Field(min_length=21, max_length=21)
    landmarks_image: list[ImagePoint] = Field(min_length=21, max_length=21)
    presence_confidence: float = Field(ge=0.0, le=1.0)


class HandFrame(BaseModel):
    """Complete tracking result for a single video frame.

    Attributes:
        timestamp: Monotonic clock value (time.monotonic()) at capture.
        frame_id: Sequential frame counter (≥ 0).
        hands: List of detected hands (0, 1, or 2).
    """

    timestamp: float
    frame_id: int = Field(ge=0)
    hands: list[HandObservation] = Field(max_length=2)


# ---------------------------------------------------------------------------
# HandFeatures  (Phase 2 — interface defined now for stability)
# ---------------------------------------------------------------------------


class FingerState(StrEnum):
    """State of a single finger."""

    EXTENDED = "EXTENDED"
    FOLDED = "FOLDED"
    UNKNOWN = "UNKNOWN"


class HandFeatures(BaseModel):
    """Computed geometric features for a single hand.

    Populated in Phase 2 by the features module.
    """

    fingers_extended: list[FingerState] = Field(min_length=5, max_length=5)
    normalized_distances: dict[str, float] = Field(default_factory=dict)
    palm_orientation: tuple[float, float, float] | None = None
    velocity: tuple[float, float] | None = None
    is_valid: bool = False


# ---------------------------------------------------------------------------
# GestureEvent  (Phase 2 — interface defined now for stability)
# ---------------------------------------------------------------------------


class GesturePhase(StrEnum):
    """Phase of a gesture's lifecycle."""

    STARTED = "STARTED"
    HELD = "HELD"
    ENDED = "ENDED"
    CANCELLED = "CANCELLED"


class GestureEvent(BaseModel):
    """A recognized gesture event emitted by the gesture engine.

    Attributes:
        gesture_id: Identifier of the gesture type (e.g. "pinch_index").
        phase: Current phase in the gesture lifecycle.
        hand: "Left", "Right", or "Both".
        timestamp: Monotonic timestamp of the event.
        confidence: Recognition confidence [0, 1].
        payload: Extra data (displacement, trajectory, etc.).
    """

    gesture_id: str
    phase: GesturePhase
    hand: str
    timestamp: float
    confidence: float = Field(ge=0.0, le=1.0)
    payload: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# ActionRequest & ActionResult  (Phase 3 — interface defined now for stability)
# ---------------------------------------------------------------------------


class ActionStatus(StrEnum):
    """Result status of an action execution attempt."""

    EXECUTED = "EXECUTED"
    SIMULATED = "SIMULATED"
    REFUSED = "REFUSED"
    FAILED = "FAILED"


class ActionRequest(BaseModel):
    """A request to execute a system action.

    Attributes:
        action_name: Name from the allowlist (e.g. "click_left").
        parameters: Validated parameters for the action.
        source_gesture: The gesture ID that triggered this request.
    """

    action_name: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    source_gesture: str | None = None


class ActionResult(BaseModel):
    """Result of an action execution attempt.

    Attributes:
        action_name: Name of the action attempted.
        status: Outcome (EXECUTED, SIMULATED, REFUSED, FAILED).
        reason: Human-readable explanation of the outcome.
        timestamp: Monotonic timestamp of the result.
    """

    action_name: str
    status: ActionStatus
    reason: str = ""
    timestamp: float = Field(default_factory=time.monotonic)
