"""Tests for the hand tracker module.

Tests that work without a real webcam by using synthetic (blank / random) frames.
"""

from __future__ import annotations

import numpy as np
import pytest

from handcontrol.tracking.models import HandFrame
from handcontrol.tracking.tracker import HandTracker


class TestHandTracker:
    def test_initialization(self):
        tracker = HandTracker()
        assert tracker is not None
        tracker.close()

    def test_process_blank_frame_no_hands(self):
        """A solid-color frame should return zero hands."""
        tracker = HandTracker()
        try:
            blank = np.zeros((480, 640, 3), dtype=np.uint8)
            hand_frame, annotated = tracker.process(blank, frame_id=0, timestamp=0.0)

            assert isinstance(hand_frame, HandFrame)
            assert hand_frame.frame_id == 0
            assert len(hand_frame.hands) == 0
            assert annotated.shape == (480, 640, 3)
        finally:
            tracker.close()

    def test_annotated_frame_same_dimensions(self):
        """The annotated frame must have the same dimensions as the input."""
        tracker = HandTracker()
        try:
            frame = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)
            _, annotated = tracker.process(frame, frame_id=1, timestamp=1.0)
            assert annotated.shape == (480, 640, 3)
        finally:
            tracker.close()

    def test_close_idempotent(self):
        """Closing the tracker multiple times must not raise."""
        tracker = HandTracker()
        tracker.close()
        tracker.close()  # second close should be safe

    def test_handframe_fields(self):
        """Verify all HandFrame fields are populated correctly."""
        tracker = HandTracker()
        try:
            blank = np.zeros((480, 640, 3), dtype=np.uint8)
            hand_frame, _ = tracker.process(blank, frame_id=42, timestamp=123.456)

            assert hand_frame.frame_id == 42
            assert hand_frame.timestamp == pytest.approx(123.456)
            assert isinstance(hand_frame.hands, list)
        finally:
            tracker.close()

    def test_different_resolutions(self):
        """Tracker should handle various input resolutions."""
        tracker = HandTracker()
        try:
            for h, w in [(240, 320), (480, 640), (720, 1280)]:
                frame = np.zeros((h, w, 3), dtype=np.uint8)
                hand_frame, annotated = tracker.process(frame, frame_id=0, timestamp=0.0)
                assert annotated.shape == (h, w, 3)
                assert isinstance(hand_frame, HandFrame)
        finally:
            tracker.close()
