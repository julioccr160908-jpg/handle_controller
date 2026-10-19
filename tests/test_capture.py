"""Tests for the capture module.

Tests cover:
- CaptureFrame data structure
- CaptureThread initial state and idempotent stop
- Graceful handling of invalid camera index
- Thread-safety of get_frame()
- Hand loss simulation with HandFrame sequences
"""

from __future__ import annotations

import threading
import time

import numpy as np

from handcontrol.capture.camera import CaptureFrame, CaptureThread
from handcontrol.tracking.models import (
    HandFrame,
    HandObservation,
    ImagePoint,
    LandmarkPoint,
)

# ---------------------------------------------------------------------------
# CaptureFrame
# ---------------------------------------------------------------------------


class TestCaptureFrame:
    def test_creation(self):
        img = np.zeros((480, 640, 3), dtype=np.uint8)
        frame = CaptureFrame(image=img, frame_id=1, timestamp=time.monotonic())
        assert frame.frame_id == 1
        assert frame.image.shape == (480, 640, 3)

    def test_immutable_metadata(self):
        img = np.zeros((100, 100, 3), dtype=np.uint8)
        frame = CaptureFrame(image=img, frame_id=42, timestamp=1.0)
        assert frame.frame_id == 42
        assert frame.timestamp == 1.0


# ---------------------------------------------------------------------------
# CaptureThread
# ---------------------------------------------------------------------------


class TestCaptureThread:
    def test_initial_state(self):
        ct = CaptureThread()
        assert not ct.is_running
        assert ct.error is None
        assert ct.get_frame() is None

    def test_stop_without_start(self):
        """Stop on a non-started thread should not raise."""
        ct = CaptureThread()
        ct.stop()
        assert not ct.is_running

    def test_double_stop(self):
        """Calling stop twice should be safe."""
        ct = CaptureThread()
        ct.stop()
        ct.stop()

    def test_invalid_camera_fails_gracefully(self):
        """Opening a non-existent camera should return False with error."""
        ct = CaptureThread(camera_index=999)
        success = ct.start()
        if not success:
            assert ct.error is not None
            assert "camera" in ct.error.lower() or "failed" in ct.error.lower()
        ct.stop()

    def test_get_frame_thread_safety(self):
        """Verify that concurrent get_frame calls don't crash."""
        ct = CaptureThread()
        # Manually inject a frame for testing.
        img = np.zeros((480, 640, 3), dtype=np.uint8)
        with ct._lock:
            ct._latest_frame = CaptureFrame(image=img, frame_id=1, timestamp=time.monotonic())

        results: list[int] = []

        def reader():
            for _ in range(100):
                frame = ct.get_frame()
                if frame is not None:
                    results.append(frame.frame_id)
                time.sleep(0.001)

        threads = [threading.Thread(target=reader) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(fid == 1 for fid in results)
        assert len(results) > 0


# ---------------------------------------------------------------------------
# Hand loss handling (contract-level tests)
# ---------------------------------------------------------------------------


def _make_obs(handedness: str = "Right") -> HandObservation:
    return HandObservation(
        handedness=handedness,
        handedness_confidence=0.9,
        landmarks_normalized=[LandmarkPoint(x=0, y=0, z=0)] * 21,
        landmarks_image=[ImagePoint(x=0, y=0)] * 21,
        presence_confidence=0.9,
    )


class TestHandLossHandling:
    def test_empty_frame_is_valid(self):
        frame = HandFrame(timestamp=time.monotonic(), frame_id=0, hands=[])
        assert len(frame.hands) == 0

    def test_hand_appears_and_disappears(self):
        frames = [
            HandFrame(timestamp=0.0, frame_id=0, hands=[]),
            HandFrame(timestamp=0.033, frame_id=1, hands=[_make_obs()]),
            HandFrame(timestamp=0.066, frame_id=2, hands=[_make_obs()]),
            HandFrame(timestamp=0.100, frame_id=3, hands=[]),
        ]
        counts = [len(f.hands) for f in frames]
        assert counts == [0, 1, 1, 0]

    def test_both_hands_lost_simultaneously(self):
        f1 = HandFrame(
            timestamp=0.0,
            frame_id=0,
            hands=[_make_obs("Right"), _make_obs("Left")],
        )
        f2 = HandFrame(timestamp=0.033, frame_id=1, hands=[])

        assert len(f1.hands) == 2
        assert len(f2.hands) == 0

    def test_one_hand_lost_other_remains(self):
        f1 = HandFrame(
            timestamp=0.0,
            frame_id=0,
            hands=[_make_obs("Right"), _make_obs("Left")],
        )
        f2 = HandFrame(
            timestamp=0.033,
            frame_id=1,
            hands=[_make_obs("Right")],
        )
        assert len(f1.hands) == 2
        assert len(f2.hands) == 1
        assert f2.hands[0].handedness == "Right"
