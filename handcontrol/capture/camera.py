"""Webcam capture thread with size-1 buffer (Section 4.3).

The capture thread reads frames from the webcam as fast as possible but only
keeps the most recent frame.  Consumers call ``get_frame()`` to retrieve the
latest frame without blocking.  This guarantees the pipeline never accumulates
stale frames (Rule 7 — no unbounded queues).
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

import cv2
import numpy as np

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CaptureFrame:
    """An immutable captured frame with metadata."""

    image: np.ndarray
    frame_id: int
    timestamp: float  # time.monotonic()


class CaptureThread:
    """Webcam capture in a dedicated thread.

    Maintains only the most recent frame (buffer size 1).
    Never accumulates frames (Rule 7).

    Parameters:
        camera_index: OpenCV camera device index.
        width: Requested capture width in pixels.
        height: Requested capture height in pixels.
        fps: Requested capture rate.  The driver may choose the closest
            supported rate.
    """

    def __init__(
        self,
        camera_index: int = 0,
        width: int = 640,
        height: int = 480,
        fps: int = 30,
    ) -> None:
        self._camera_index = camera_index
        self._width = width
        self._height = height
        self._fps = fps

        self._cap: cv2.VideoCapture | None = None
        self._latest_frame: CaptureFrame | None = None
        self._frame_id: int = 0
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._error: str | None = None
        self._error_lock = threading.Lock()

    # -- public properties ---------------------------------------------------

    @property
    def is_running(self) -> bool:
        """Whether the capture loop is currently active."""
        return self._running

    @property
    def error(self) -> str | None:
        """Latest error message, or None if no error."""
        with self._error_lock:
            return self._error

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> bool:
        """Open the camera and start the capture thread.

        Returns:
            True if the camera was opened successfully.
        """
        if self._running:
            logger.warning("Capture thread already running")
            return True

        try:
            self._cap = cv2.VideoCapture(self._camera_index, cv2.CAP_DSHOW)
            if not self._cap.isOpened():
                self._set_error(f"Failed to open camera index {self._camera_index}")
                return False

            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
            self._cap.set(cv2.CAP_PROP_FPS, self._fps)
            # Minimize internal OpenCV buffer to reduce latency.
            self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

            actual_w = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            actual_h = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            actual_fps = self._cap.get(cv2.CAP_PROP_FPS)
            logger.info(
                "Camera opened: %dx%d @ %.1f FPS (requested %dx%d @ %d FPS)",
                actual_w,
                actual_h,
                actual_fps,
                self._width,
                self._height,
                self._fps,
            )

            self._running = True
            self._frame_id = 0
            self._clear_error()
            self._thread = threading.Thread(
                target=self._capture_loop,
                name="CaptureThread",
                daemon=True,
            )
            self._thread.start()
            return True

        except Exception:
            logger.exception("Camera initialization error")
            self._set_error("Camera initialization error")
            self._release_camera()
            return False

    def stop(self) -> None:
        """Stop the capture thread and release the camera."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        self._release_camera()
        with self._lock:
            self._latest_frame = None
        logger.info("Capture thread stopped")

    def get_frame(self) -> CaptureFrame | None:
        """Return the latest captured frame, or ``None`` if unavailable.

        Thread-safe.  Multiple consumers may call this concurrently.
        """
        with self._lock:
            return self._latest_frame

    # -- internal ------------------------------------------------------------

    def _capture_loop(self) -> None:
        """Main capture loop — runs in the dedicated thread."""
        logger.info("Capture loop started")
        consecutive_failures = 0
        max_failures = 30  # roughly 1 second at 30 fps

        while self._running:
            if self._cap is None or not self._cap.isOpened():
                self._set_error("Camera disconnected")
                self._running = False
                break

            ret, frame = self._cap.read()

            if not ret or frame is None:
                consecutive_failures += 1
                if consecutive_failures >= max_failures:
                    self._set_error("Camera read failed repeatedly — device may be disconnected")
                    self._running = False
                    break
                time.sleep(0.01)
                continue

            consecutive_failures = 0
            self._clear_error()
            self._frame_id += 1

            capture_frame = CaptureFrame(
                image=frame,
                frame_id=self._frame_id,
                timestamp=time.monotonic(),
            )

            # Buffer size 1: overwrite the previous frame.
            with self._lock:
                self._latest_frame = capture_frame

        logger.info("Capture loop ended")

    def _release_camera(self) -> None:
        """Release the OpenCV camera resource."""
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                logger.exception("Error releasing camera")
            finally:
                self._cap = None
            logger.info("Camera released")

    def _set_error(self, msg: str) -> None:
        with self._error_lock:
            self._error = msg
        logger.error(msg)

    def _clear_error(self) -> None:
        with self._error_lock:
            self._error = None
