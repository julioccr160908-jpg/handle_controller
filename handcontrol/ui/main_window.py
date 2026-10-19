"""Main application window — Phase 2 (Section 9 subset).

Displays live webcam video with MediaPipe hand landmarks, handedness labels,
FPS, latency, tracking status, and recognized gesture.

Threading model:
    - CaptureThread (Python thread): reads webcam → size-1 buffer.
    - Processing thread (Python thread): MediaPipe → features → gestures → stores result.
    - GUI thread (Qt main thread): QTimer at ≤ 30 Hz polls for latest result and
      updates widgets.  No widget is ever touched outside the GUI thread (Rule 6).
"""

from __future__ import annotations

import logging
import threading
import time

import cv2
import numpy as np
from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from handcontrol.capture.camera import CaptureThread
from handcontrol.features.extractor import FeatureExtractor
from handcontrol.gestures.engine import GestureEngine
from handcontrol.metrics.performance import PerformanceMetrics
from handcontrol.tracking.models import GestureEvent, HandFrame
from handcontrol.tracking.tracker import HandTracker

logger = logging.getLogger(__name__)

# Maximum display refresh rate (Hz) — spec: ≤ 30 updates/s.
_MAX_DISPLAY_FPS = 30


class MainWindow(QMainWindow):
    """HandControl main window with video, landmarks, controls and metrics."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("HandControl — Phase 1")
        self.setMinimumSize(860, 660)

        # --- pipeline components ---
        self._capture = CaptureThread()
        self._tracker = HandTracker()
        self._extractor = FeatureExtractor()
        self._gesture_engine = GestureEngine()
        self._metrics = PerformanceMetrics()

        # --- processing thread state ---
        self._processing = False
        self._processing_thread: threading.Thread | None = None
        # Result now includes gesture events.
        self._latest_result: tuple[HandFrame, np.ndarray, list[GestureEvent]] | None = None
        self._result_lock = threading.Lock()
        self._paused = False
        # Last gesture name shown in UI (updated in display thread only).
        self._last_gesture: str = "—"

        # --- build UI ---
        self._setup_ui()

        # --- display refresh timer (≤ 30 fps) ---
        self._display_timer = QTimer(self)
        self._display_timer.setInterval(1000 // _MAX_DISPLAY_FPS)
        self._display_timer.timeout.connect(self._update_display)

    # ------------------------------------------------------------------ UI --

    def _setup_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setSpacing(10)
        root.setContentsMargins(12, 12, 12, 12)

        # --- Style constants ---
        bg_dark = "#0f0f1a"
        bg_panel = "#1a1a2e"
        bg_accent = "#16213e"
        fg_text = "#e0e0e0"
        accent_blue = "#0f3460"
        accent_hover = "#1a508b"
        accent_pressed = "#0a2647"
        border_color = "#233554"

        self.setStyleSheet(f"""
            QMainWindow {{
                background-color: {bg_dark};
            }}
            QWidget {{
                background-color: {bg_dark};
                color: {fg_text};
            }}
        """)

        # --- Video display ---
        self._video_label = QLabel("📷  Camera not started")
        self._video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._video_label.setStyleSheet(f"""
            QLabel {{
                background-color: {bg_panel};
                color: {fg_text};
                border: 2px solid {border_color};
                border-radius: 10px;
                font-size: 18px;
                padding: 20px;
            }}
        """)
        self._video_label.setMinimumSize(640, 480)
        self._video_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        root.addWidget(self._video_label, stretch=1)

        # --- Metrics bar ---
        metrics_frame = QFrame()
        metrics_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {bg_accent};
                border-radius: 8px;
                padding: 4px;
            }}
        """)
        metrics_layout = QHBoxLayout(metrics_frame)
        metrics_layout.setContentsMargins(16, 8, 16, 8)
        metrics_layout.setSpacing(24)

        label_style = f"""
            QLabel {{
                color: {fg_text};
                font-size: 13px;
                font-weight: bold;
                background: transparent;
            }}
        """

        self._fps_label = QLabel("FPS: —")
        self._latency_label = QLabel("Latência: — ms")
        self._hands_label = QLabel("Mãos: —")
        self._gesture_label = QLabel("Gesto: —")
        self._tracking_label = QLabel("⏹  Parado")

        for lbl in (
            self._fps_label,
            self._latency_label,
            self._hands_label,
            self._gesture_label,
            self._tracking_label,
        ):
            lbl.setStyleSheet(label_style)
            metrics_layout.addWidget(lbl)

        metrics_layout.addStretch()
        root.addWidget(metrics_frame)

        # --- Control buttons ---
        btn_frame = QFrame()
        btn_frame.setStyleSheet("QFrame { background: transparent; }")
        btn_layout = QHBoxLayout(btn_frame)
        btn_layout.setContentsMargins(0, 4, 0, 4)
        btn_layout.setSpacing(12)

        btn_style = f"""
            QPushButton {{
                background-color: {accent_blue};
                color: white;
                border: none;
                border-radius: 8px;
                padding: 12px 28px;
                font-size: 14px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {accent_hover};
            }}
            QPushButton:pressed {{
                background-color: {accent_pressed};
            }}
            QPushButton:disabled {{
                background-color: #2a2a3a;
                color: #555;
            }}
        """

        self._btn_start = QPushButton("▶  Iniciar")
        self._btn_pause = QPushButton("⏸  Pausar")
        self._btn_stop = QPushButton("⏹  Parar")

        self._btn_pause.setEnabled(False)
        self._btn_stop.setEnabled(False)

        for btn in (self._btn_start, self._btn_pause, self._btn_stop):
            btn.setStyleSheet(btn_style)
            btn.setMinimumHeight(44)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_layout.addWidget(btn)

        self._btn_start.clicked.connect(self._on_start)
        self._btn_pause.clicked.connect(self._on_pause)
        self._btn_stop.clicked.connect(self._on_stop)

        root.addWidget(btn_frame)

    # --------------------------------------------------------------- slots --

    @Slot()
    def _on_start(self) -> None:
        """Start or resume capture and tracking."""
        if self._paused:
            self._paused = False
            self._tracking_label.setText("🟢  Rastreando")
            self._btn_pause.setText("⏸  Pausar")
            logger.info("Tracking resumed")
            return

        if self._capture.is_running:
            return

        success = self._capture.start()
        if not success:
            error = self._capture.error or "Erro desconhecido"
            self._video_label.setText(
                f"⚠  Erro de câmera: {error}\n\nVerifique a conexão e tente novamente."
            )
            logger.error("Failed to start capture: %s", error)
            return

        # Start processing thread.
        self._processing = True
        self._processing_thread = threading.Thread(
            target=self._processing_loop,
            name="TrackingThread",
            daemon=True,
        )
        self._processing_thread.start()

        # Start display timer.
        self._display_timer.start()

        # Update button state.
        self._btn_start.setEnabled(False)
        self._btn_pause.setEnabled(True)
        self._btn_stop.setEnabled(True)
        self._tracking_label.setText("🟢  Rastreando")
        logger.info("Tracking started")

    @Slot()
    def _on_pause(self) -> None:
        """Toggle pause state."""
        self._paused = not self._paused
        if self._paused:
            self._tracking_label.setText("⏸  Pausado")
            self._btn_pause.setText("▶  Retomar")
            logger.info("Tracking paused")
        else:
            self._tracking_label.setText("🟢  Rastreando")
            self._btn_pause.setText("⏸  Pausar")
            logger.info("Tracking resumed")

    @Slot()
    def _on_stop(self) -> None:
        """Stop capture and tracking completely."""
        logger.info("Stopping tracking…")
        self._processing = False
        self._display_timer.stop()

        if self._processing_thread is not None:
            self._processing_thread.join(timeout=3.0)
            self._processing_thread = None

        self._capture.stop()
        self._tracker.close()
        # Re-create tracker, extractor and engine for next start.
        self._tracker = HandTracker()
        self._extractor = FeatureExtractor()
        self._gesture_engine = GestureEngine()
        self._metrics.reset()

        # Reset UI.
        self._btn_start.setEnabled(True)
        self._btn_pause.setEnabled(False)
        self._btn_stop.setEnabled(False)
        self._btn_pause.setText("⏸  Pausar")
        self._paused = False
        self._last_gesture = "—"

        self._video_label.setPixmap(QPixmap())
        self._video_label.setText("📷  Câmera parada")
        self._fps_label.setText("FPS: —")
        self._latency_label.setText("Latência: — ms")
        self._hands_label.setText("Mãos: —")
        self._gesture_label.setText("Gesto: —")
        self._tracking_label.setText("⏹  Parado")
        logger.info("Tracking stopped")

    # --------------------------------------------------- processing thread --

    def _processing_loop(self) -> None:
        """Tracking loop — runs in a dedicated thread.

        Grabs the latest capture frame, runs MediaPipe, and stores the result
        for the GUI thread to pick up via ``_update_display``.
        """
        logger.info("Processing loop started")
        last_frame_id = -1

        while self._processing:
            # Check for camera errors.
            if self._capture.error and not self._capture.is_running:
                time.sleep(0.05)
                continue

            if self._paused:
                time.sleep(0.03)
                continue

            capture_frame = self._capture.get_frame()
            if capture_frame is None:
                time.sleep(0.005)
                continue

            # Skip if we already processed this exact frame.
            if capture_frame.frame_id == last_frame_id:
                time.sleep(0.001)
                continue

            last_frame_id = capture_frame.frame_id

            try:
                hand_frame, annotated = self._tracker.process(
                    capture_frame.image,
                    capture_frame.frame_id,
                    capture_frame.timestamp,
                )

                # Phase 2: extract features and evaluate gestures.
                features_by_hand = self._extractor.extract_all(
                    hand_frame.hands,
                    hand_frame.timestamp,
                )
                # Reset extractor state for hands that have been lost.
                present = {obs.handedness for obs in hand_frame.hands}
                for side in ("Left", "Right"):
                    if side not in present:
                        self._extractor.reset(side)

                gesture_events = self._gesture_engine.update(
                    features_by_hand,
                    hand_frame.timestamp,
                )

                latency_ms = (time.monotonic() - capture_frame.timestamp) * 1000
                self._metrics.record_frame()
                self._metrics.record_latency(latency_ms)

                with self._result_lock:
                    self._latest_result = (hand_frame, annotated, gesture_events)

            except Exception:
                logger.exception("Tracking error")
                time.sleep(0.01)

        logger.info("Processing loop ended")

    # ---------------------------------------------------- display refresh ---

    @Slot()
    def _update_display(self) -> None:
        """Refresh the GUI with the latest tracking result.

        Runs on the Qt main thread at ≤ 30 Hz.
        """
        # Camera error feedback.
        if self._capture.error and not self._capture.is_running:
            self._video_label.setText(
                f"⚠  Erro de câmera: {self._capture.error}\n\n"
                "Clique em Parar e depois em Iniciar para reconectar."
            )
            self._tracking_label.setText("🔴  Erro")
            return

        # Consume latest result.
        with self._result_lock:
            result = self._latest_result
            self._latest_result = None

        if result is None:
            return

        hand_frame, annotated, gesture_events = result

        # Update gesture label from events.
        for ev in gesture_events:
            from handcontrol.tracking.models import GesturePhase

            if ev.phase == GesturePhase.STARTED:
                self._last_gesture = ev.gesture_id
            elif ev.phase in (GesturePhase.ENDED, GesturePhase.CANCELLED):
                self._last_gesture = "—"
        self._gesture_label.setText(f"Gesto: {self._last_gesture}")

        # Convert BGR → RGB → QPixmap.
        rgb = cv2.cvtColor(annotated, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        bytes_per_line = ch * w
        q_img = QImage(rgb.data, w, h, bytes_per_line, QImage.Format.Format_RGB888)

        pixmap = QPixmap.fromImage(q_img)
        scaled = pixmap.scaled(
            self._video_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._video_label.setPixmap(scaled)

        # Update metrics.
        fps = self._metrics.fps
        latency = self._metrics.avg_latency_ms
        self._fps_label.setText(f"FPS: {fps:.1f}")
        self._latency_label.setText(f"Latência: {latency:.0f} ms")

        # Update hand info.
        if not hand_frame.hands:
            self._hands_label.setText("Mãos: nenhuma")
        else:
            parts = [
                f"{obs.handedness} ({obs.handedness_confidence:.0%})" for obs in hand_frame.hands
            ]
            self._hands_label.setText(f"Mãos: {', '.join(parts)}")

    # ------------------------------------------------------------ cleanup ---

    def closeEvent(self, event) -> None:  # noqa: N802 — Qt naming convention
        """Ensure clean shutdown when the window is closed."""
        self._on_stop()
        event.accept()
