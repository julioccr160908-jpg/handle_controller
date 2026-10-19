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
import sys

import cv2
import numpy as np

# On Windows, if the path contains non-ASCII characters (such as accents in folder names),
# MediaPipe's C++ framework fails to locate graph files unless converted to 8.3 short paths.
# When running inside PyInstaller, virtual/pyc files do not exist as standalone files,
# so we locate the existing ancestor folder and build the short path from it.
if sys.platform == "win32":
    try:
        import ctypes
        import os

        import mediapipe.python.solution_base as _sb

        _target = os.path.abspath(_sb.__file__)
        _parts_to_append: list[str] = []
        _curr = _target
        while _curr and not os.path.exists(_curr):
            _curr, _tail = os.path.split(_curr)
            if _tail:
                _parts_to_append.append(_tail)
            else:
                break

        if _curr and os.path.exists(_curr):
            _buf = ctypes.create_unicode_buffer(1000)
            if ctypes.windll.kernel32.GetShortPathNameW(_curr, _buf, 1000):
                _short_base = _buf.value
                if _parts_to_append:
                    _sb.__file__ = os.path.join(_short_base, *reversed(_parts_to_append))
                else:
                    _sb.__file__ = _short_base
    except Exception:
        pass

import mediapipe as mp

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
    ) -> None:
        self._mp_hands = mp.solutions.hands
        self._hands = self._mp_hands.Hands(
            static_image_mode=False,
            max_num_hands=max_hands,
            min_detection_confidence=min_detection_confidence,
            min_tracking_confidence=min_tracking_confidence,
        )
        self._mp_drawing = mp.solutions.drawing_utils
        self._mp_drawing_styles = mp.solutions.drawing_styles
        logger.info(
            "HandTracker initialized: max_hands=%d, det_conf=%.2f, track_conf=%.2f",
            max_hands,
            min_detection_confidence,
            min_tracking_confidence,
        )

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

        # MediaPipe expects RGB input.
        rgb = cv2.cvtColor(flipped, cv2.COLOR_BGR2RGB)
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

                # Swap handedness because the image is mirror-flipped (D002).
                raw_label = classification.label
                actual_label = "Left" if raw_label == "Right" else "Right"

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
                    self._mp_drawing_styles.get_default_hand_landmarks_style(),
                    self._mp_drawing_styles.get_default_hand_connections_style(),
                )

        return hand_frame, annotated

    def close(self) -> None:
        """Release MediaPipe resources."""
        if self._hands is not None:
            self._hands.close()
            self._hands = None
        logger.info("HandTracker closed")
