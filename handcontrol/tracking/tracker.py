"""MediaPipe hand tracking (Section 4.4).

Wraps ``mp.solutions.hands`` to detect up to two hands in a BGR video frame.
Returns structured ``HandFrame`` data and an annotated frame for display.

Mirror effect handling
---------------------
The webcam image is flipped horizontally before MediaPipe processing so the
user sees a natural mirror view.  Because MediaPipe analyses the flipped
image, its handedness labels are inverted relative to the user's actual
hands.  We swap them: MediaPipe "Right" → user's "Left" and vice-versa.
This decision is documented in ``docs/DECISIONS.md`` (D002).
"""

from __future__ import annotations

import logging

import cv2
import mediapipe as mp
import numpy as np

from handcontrol.tracking.mediapipe_runtime import prepare_mediapipe_resources
from handcontrol.tracking.models import (
    HandFrame,
    HandObservation,
    ImagePoint,
    LandmarkPoint,
)

logger = logging.getLogger(__name__)


class HandTracker:
    """MediaPipe Hands wrapper for real-time hand landmark detection.

    Uses ``mp.solutions.hands`` (bundled model) so no external model file
    download is required — satisfying the offline-only requirement (Rule 9).

    Parameters:
        max_hands: Maximum number of hands to detect (1 or 2).
        min_detection_confidence: Minimum confidence for initial detection.
        min_tracking_confidence: Minimum confidence for frame-to-frame tracking.
    """

    def __init__(
        self,
        max_hands: int = 2,
        min_detection_confidence: float = 0.5,
        min_tracking_confidence: float = 0.5,
        model_complexity: int = 0,
        inference_width: int = 480,
        flip_handedness: bool = False,
    ) -> None:
        if model_complexity not in (0, 1):
            raise ValueError("model_complexity must be 0 (fast) or 1 (full)")
        if inference_width < 160:
            raise ValueError("inference_width must be at least 160 pixels")
        prepare_mediapipe_resources()
        self._flip_handedness = flip_handedness
        self._mp_hands = mp.solutions.hands
        self._hands = self._mp_hands.Hands(
            static_image_mode=False,
            max_num_hands=max_hands,
            min_detection_confidence=min_detection_confidence,
            min_tracking_confidence=min_tracking_confidence,
            model_complexity=model_complexity,
        )
        self._mp_drawing = mp.solutions.drawing_utils
        self._mp_drawing_styles = mp.solutions.drawing_styles
        self._inference_width = inference_width
        self._landmark_style = self._mp_drawing_styles.get_default_hand_landmarks_style()
        self._connection_style = self._mp_drawing_styles.get_default_hand_connections_style()
        logger.info(
            "HandTracker initialized: max_hands=%d, complexity=%d, inference_width=%d, "
            "flip_handedness=%s, det_conf=%.2f, track_conf=%.2f",
            max_hands,
            model_complexity,
            inference_width,
            flip_handedness,
            min_detection_confidence,
            min_tracking_confidence,
        )

    @property
    def flip_handedness(self) -> bool:
        """Whether to invert handedness labels."""
        return self._flip_handedness

    @flip_handedness.setter
    def flip_handedness(self, value: bool) -> None:
        self._flip_handedness = value

    def process(
        self,
        bgr_frame: np.ndarray,
        frame_id: int,
        timestamp: float,
    ) -> tuple[HandFrame, np.ndarray]:
        """Process a BGR frame and return tracking results.

        Args:
            bgr_frame: Raw BGR frame from the webcam (not yet flipped).
            frame_id: Sequential frame identifier.
            timestamp: Monotonic capture timestamp.

        Returns:
            A tuple of (HandFrame, annotated_bgr_frame).  The annotated
            frame has landmarks drawn on the mirror-flipped image.
        """
        # Flip horizontally for mirror view.
        flipped = cv2.flip(bgr_frame, 1)
        h, w = flipped.shape[:2]

        # Landmark coordinates are normalized, so inference can run at a
        # smaller resolution while the preview and its overlay remain sharp.
        # This is a substantial CPU saving on integrated graphics/older CPUs.
        inference_frame = flipped
        if w > self._inference_width:
            scale = self._inference_width / w
            inference_frame = cv2.resize(
                flipped,
                (self._inference_width, max(1, round(h * scale))),
                interpolation=cv2.INTER_AREA,
            )

        # MediaPipe expects RGB input.
        rgb = cv2.cvtColor(inference_frame, cv2.COLOR_BGR2RGB)
        rgb.flags.writeable = False
        results = self._hands.process(rgb)
        rgb.flags.writeable = True

        # Build hand observations.
        hands: list[HandObservation] = []

        if results.multi_hand_landmarks and results.multi_handedness:
            for hand_landmarks, handedness_info in zip(
                results.multi_hand_landmarks,
                results.multi_handedness,
                strict=True,
            ):
                classification = handedness_info.classification[0]

                raw_label = classification.label
                if self._flip_handedness:
                    actual_label = "Left" if raw_label == "Right" else "Right"
                else:
                    actual_label = raw_label

                landmarks_norm = [
                    LandmarkPoint(x=lm.x, y=lm.y, z=lm.z) for lm in hand_landmarks.landmark
                ]

                landmarks_img = [
                    ImagePoint(x=lm.x * w, y=lm.y * h) for lm in hand_landmarks.landmark
                ]

                obs = HandObservation(
                    handedness=actual_label,
                    handedness_confidence=classification.score,
                    landmarks_normalized=landmarks_norm,
                    landmarks_image=landmarks_img,
                    presence_confidence=classification.score,
                )
                hands.append(obs)

        hand_frame = HandFrame(
            timestamp=timestamp,
            frame_id=frame_id,
            hands=hands,
        )

        # Draw landmarks on the flipped frame for display.
        annotated = flipped.copy()
        if results.multi_hand_landmarks:
            for hand_landmarks in results.multi_hand_landmarks:
                self._mp_drawing.draw_landmarks(
                    annotated,
                    hand_landmarks,
                    self._mp_hands.HAND_CONNECTIONS,
                    self._landmark_style,
                    self._connection_style,
                )

        return hand_frame, annotated

    def close(self) -> None:
        """Release MediaPipe resources."""
        if self._hands is not None:
            self._hands.close()
            self._hands = None
        logger.info("HandTracker closed")
