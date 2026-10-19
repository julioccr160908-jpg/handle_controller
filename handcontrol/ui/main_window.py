"""Main application window — Phase 3 (Section 9 subset).

Displays live webcam video with MediaPipe hand landmarks, handedness labels,
FPS, latency, recognized gesture, last action result, and event history.
Provides Start/Pause/Stop controls, an Emergency Stop button, and a
Simulation mode indicator.

Threading model:
    - CaptureThread (Python thread): reads webcam → size-1 buffer.
    - Processing thread (Python thread): MediaPipe → features → gestures
      → mapping → controller → stores result.
    - GUI thread (Qt main thread): QTimer at ≤ 30 Hz polls for latest result
      and updates widgets.  No widget is ever touched outside the GUI thread
      (Rule 6).
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
    QListWidget,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from handcontrol.actions.controller import ActionController
from handcontrol.capture.camera import CaptureThread
from handcontrol.features.extractor import FeatureExtractor
from handcontrol.gestures.engine import GestureEngine
from handcontrol.mapping.profile import GestureMapper
from handcontrol.metrics.performance import PerformanceMetrics
from handcontrol.safety.manager import SafetyManager
from handcontrol.tracking.models import ActionResult, ActionStatus, GestureEvent, HandFrame
from handcontrol.tracking.tracker import HandTracker

logger = logging.getLogger(__name__)

# Maximum display refresh rate (Hz) — spec: ≤ 30 updates/s.
_MAX_DISPLAY_FPS = 30
# Max rows shown in the event history list.
_HISTORY_MAX_ROWS = 60


class MainWindow(QMainWindow):
    """HandControl main window with video, landmarks, controls and metrics."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("HandControl — Simulação")
        self.setMinimumSize(960, 700)

        # --- pipeline components ---
        self._capture = CaptureThread()
        self._tracker = HandTracker()
        self._extractor = FeatureExtractor()
        self._gesture_engine = GestureEngine()
        self._mapper = GestureMapper()
        self._controller = ActionController()  # SimulatedExecutor by default
        self._safety = SafetyManager(self._controller, self._mapper)
        self._metrics = PerformanceMetrics()

        # --- processing thread state ---
        self._processing = False
        self._processing_thread: threading.Thread | None = None
        self._latest_result: (
            tuple[HandFrame, np.ndarray, list[GestureEvent], list[ActionResult]] | None
        ) = None
        self._result_lock = threading.Lock()
        self._paused = False

        # --- UI display state (GUI thread only) ---
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
        root.setSpacing(8)
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
        color_emergency = "#c0392b"
        color_emergency_hover = "#e74c3c"
        color_sim = "#1a6b3a"

        self.setStyleSheet(f"""
            QMainWindow {{
                background-color: {bg_dark};
            }}
            QWidget {{
                background-color: {bg_dark};
                color: {fg_text};
            }}
            QSplitter::handle {{
                background-color: {border_color};
                width: 4px;
            }}
        """)

        # ── Status / simulation badge ─────────────────────────────────────
        sim_badge = QLabel("🔒  MODO SIMULAÇÃO — Nenhuma ação real será executada")
        sim_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sim_badge.setStyleSheet(f"""
            QLabel {{
                background-color: {color_sim};
                color: white;
                font-size: 12px;
                font-weight: bold;
                padding: 6px 12px;
                border-radius: 6px;
                border: 1px solid #2ecc71;
            }}
        """)
        root.addWidget(sim_badge)

        # ── Main content: video + history side by side ────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(4)

        # Video panel.
        video_widget = QWidget()
        video_layout = QVBoxLayout(video_widget)
        video_layout.setContentsMargins(0, 0, 0, 0)
        video_layout.setSpacing(0)

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
        self._video_label.setMinimumSize(600, 440)
        self._video_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        video_layout.addWidget(self._video_label, stretch=1)

        splitter.addWidget(video_widget)

        # History panel.
        history_widget = QWidget()
        history_layout = QVBoxLayout(history_widget)
        history_layout.setContentsMargins(8, 0, 0, 0)
        history_layout.setSpacing(4)

        history_title = QLabel("📋  Histórico de Ações")
        history_title.setStyleSheet(f"""
            QLabel {{
                color: {fg_text};
                font-size: 13px;
                font-weight: bold;
                background: transparent;
                padding: 4px 0;
            }}
        """)
        history_layout.addWidget(history_title)

        self._history_list = QListWidget()
        self._history_list.setStyleSheet(f"""
            QListWidget {{
                background-color: {bg_panel};
                color: {fg_text};
                border: 1px solid {border_color};
                border-radius: 6px;
                font-size: 11px;
                font-family: 'Consolas', 'Courier New', monospace;
            }}
            QListWidget::item {{
                padding: 3px 6px;
                border-bottom: 1px solid {bg_accent};
            }}
            QListWidget::item:selected {{
                background-color: {accent_blue};
            }}
        """)
        self._history_list.setMinimumWidth(240)
        history_layout.addWidget(self._history_list, stretch=1)

        splitter.addWidget(history_widget)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, stretch=1)

        # ── Metrics bar ───────────────────────────────────────────────────
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
                font-size: 12px;
                font-weight: bold;
                background: transparent;
            }}
        """

        self._fps_label = QLabel("FPS: —")
        self._latency_label = QLabel("Latência: — ms")
        self._hands_label = QLabel("Mãos: —")
        self._gesture_label = QLabel("Gesto: —")
        self._action_label = QLabel("Ação: —")
        self._tracking_label = QLabel("⏹  Parado")

        for lbl in (
            self._fps_label,
            self._latency_label,
            self._hands_label,
            self._gesture_label,
            self._action_label,
            self._tracking_label,
        ):
            lbl.setStyleSheet(label_style)
            metrics_layout.addWidget(lbl)

        metrics_layout.addStretch()
        root.addWidget(metrics_frame)

        # ── Control buttons ───────────────────────────────────────────────
        btn_frame = QFrame()
        btn_frame.setStyleSheet("QFrame { background: transparent; }")
        btn_layout = QHBoxLayout(btn_frame)
        btn_layout.setContentsMargins(0, 4, 0, 4)
        btn_layout.setSpacing(10)

        btn_style = f"""
            QPushButton {{
                background-color: {accent_blue};
                color: white;
                border: none;
                border-radius: 8px;
                padding: 10px 24px;
                font-size: 13px;
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

        emerg_style = f"""
            QPushButton {{
                background-color: {color_emergency};
                color: white;
                border: none;
                border-radius: 8px;
                padding: 10px 24px;
                font-size: 13px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {color_emergency_hover};
            }}
            QPushButton:pressed {{
                background-color: #922b21;
            }}
            QPushButton:disabled {{
                background-color: #2a2a3a;
                color: #555;
            }}
        """

        self._btn_start = QPushButton("▶  Iniciar")
        self._btn_pause = QPushButton("⏸  Pausar")
        self._btn_stop = QPushButton("⏹  Parar")
        self._btn_emergency = QPushButton("🛑  Parada de Emergência")

        self._btn_pause.setEnabled(False)
        self._btn_stop.setEnabled(False)
        self._btn_emergency.setEnabled(False)

        for btn in (self._btn_start, self._btn_pause, self._btn_stop):
            btn.setStyleSheet(btn_style)
            btn.setMinimumHeight(44)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_layout.addWidget(btn)

        self._btn_emergency.setStyleSheet(emerg_style)
        self._btn_emergency.setMinimumHeight(44)
        self._btn_emergency.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_layout.addStretch()
        btn_layout.addWidget(self._btn_emergency)

        self._btn_start.clicked.connect(self._on_start)
        self._btn_pause.clicked.connect(self._on_pause)
        self._btn_stop.clicked.connect(self._on_stop)
        self._btn_emergency.clicked.connect(self._on_emergency)

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
        self._btn_emergency.setEnabled(True)
        self._tracking_label.setText("🟢  Rastreando")
        logger.info("Tracking started")

    @Slot()
    def _on_pause(self) -> None:
        """Toggle pause state."""
        self._paused = not self._paused
        if self._paused:
            self._tracking_label.setText("⏸  Pausado")
            self._btn_pause.setText("▶  Retomar")
            # Cancel active gestures and release inputs.
            self._gesture_engine.cancel_all(time.monotonic())
            self._controller._executor.release_all()
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
        self._safety.full_reset()

        # Re-create pipeline for next start.
        self._tracker = HandTracker()
        self._extractor = FeatureExtractor()
        self._gesture_engine = GestureEngine()
        self._mapper = GestureMapper()
        self._controller = ActionController()
        self._safety = SafetyManager(self._controller, self._mapper)
        self._metrics.reset()

        # Reset UI.
        self._btn_start.setEnabled(True)
        self._btn_pause.setEnabled(False)
        self._btn_stop.setEnabled(False)
        self._btn_emergency.setEnabled(False)
        self._btn_pause.setText("⏸  Pausar")
        self._paused = False
        self._last_gesture = "—"

        self._video_label.setPixmap(QPixmap())
        self._video_label.setText("📷  Câmera parada")
        self._fps_label.setText("FPS: —")
        self._latency_label.setText("Latência: — ms")
        self._hands_label.setText("Mãos: —")
        self._gesture_label.setText("Gesto: —")
        self._action_label.setText("Ação: —")
        self._tracking_label.setText("⏹  Parado")
        logger.info("Tracking stopped")

    @Slot()
    def _on_emergency(self) -> None:
        """Activate emergency stop: block all actions and release inputs."""
        if self._safety.is_emergency:
            # Second press clears the emergency.
            self._safety.reset_emergency()
            self._btn_emergency.setText("🛑  Parada de Emergência")
            self._btn_emergency.setStyleSheet(self._btn_emergency.styleSheet())
            self._tracking_label.setText("🟢  Rastreando" if not self._paused else "⏸  Pausado")
            logger.info("Emergency cleared by user")
        else:
            self._safety.emergency_stop()
            self._btn_emergency.setText("✅  Retomar (limpar emergência)")
            self._tracking_label.setText("🔴  EMERGÊNCIA")
            logger.warning("Emergency stop activated by user")

    # --------------------------------------------------- processing thread --

    def _processing_loop(self) -> None:
        """Full pipeline loop — runs in a dedicated thread.

        Capture → MediaPipe → features → gestures → mapping → controller.
        """
        logger.info("Processing loop started")
        last_frame_id = -1

        while self._processing:
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

            if capture_frame.frame_id == last_frame_id:
                time.sleep(0.001)
                continue

            last_frame_id = capture_frame.frame_id

            try:
                # Phase 1: tracking.
                hand_frame, annotated = self._tracker.process(
                    capture_frame.image,
                    capture_frame.frame_id,
                    capture_frame.timestamp,
                )

                # Phase 2: features + gestures.
                features_by_hand = self._extractor.extract_all(
                    hand_frame.hands,
                    hand_frame.timestamp,
                )
                present = {obs.handedness for obs in hand_frame.hands}
                for side in ("Left", "Right"):
                    if side not in present:
                        self._extractor.reset(side)

                gesture_events = self._gesture_engine.update(
                    features_by_hand,
                    hand_frame.timestamp,
                )

                # Phase 3: mapping + execution.
                requests = self._mapper.process_events(gesture_events)
                action_results = self._controller.submit_many(requests)

                # Reset repeat counters for ended/cancelled gestures.
                from handcontrol.tracking.models import GesturePhase

                for ev in gesture_events:
                    if ev.phase in (GesturePhase.ENDED, GesturePhase.CANCELLED):
                        self._mapper.reset_gesture(ev.gesture_id, ev.hand)

                latency_ms = (time.monotonic() - capture_frame.timestamp) * 1000
                self._metrics.record_frame()
                self._metrics.record_latency(latency_ms)

                with self._result_lock:
                    self._latest_result = (
                        hand_frame,
                        annotated,
                        gesture_events,
                        action_results,
                    )

            except Exception:
                logger.exception("Pipeline error")
                time.sleep(0.01)

        logger.info("Processing loop ended")

    # ---------------------------------------------------- display refresh ---

    @Slot()
    def _update_display(self) -> None:
        """Refresh the GUI with the latest pipeline result (≤ 30 Hz)."""
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

        hand_frame, annotated, gesture_events, action_results = result

        # Update gesture label.
        from handcontrol.tracking.models import GesturePhase

        for ev in gesture_events:
            if ev.phase == GesturePhase.STARTED:
                self._last_gesture = ev.gesture_id
            elif ev.phase in (GesturePhase.ENDED, GesturePhase.CANCELLED):
                self._last_gesture = "—"
        self._gesture_label.setText(f"Gesto: {self._last_gesture}")

        # Update action label + history.
        if action_results:
            last = action_results[-1]
            icon = {"SIMULATED": "🟡", "EXECUTED": "🟢", "REFUSED": "🔴", "FAILED": "❌"}.get(
                last.status.value, "•"
            )
            self._action_label.setText(f"Ação: {icon} {last.action_name}")
            for ar in action_results:
                self._add_history_entry(ar)

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

        if not hand_frame.hands:
            self._hands_label.setText("Mãos: nenhuma")
        else:
            parts = [
                f"{obs.handedness} ({obs.handedness_confidence:.0%})" for obs in hand_frame.hands
            ]
            self._hands_label.setText(f"Mãos: {', '.join(parts)}")

    def _add_history_entry(self, result: ActionResult) -> None:
        """Add one line to the history list widget (GUI thread only)."""
        icon = {
            ActionStatus.SIMULATED: "🟡",
            ActionStatus.EXECUTED: "🟢",
            ActionStatus.REFUSED: "🔴",
            ActionStatus.FAILED: "❌",
        }.get(result.status, "•")
        label = f"{icon} {result.action_name}"
        if result.reason:
            label += f"  — {result.reason[:40]}"
        self._history_list.insertItem(0, label)
        # Keep list bounded.
        while self._history_list.count() > _HISTORY_MAX_ROWS:
            self._history_list.takeItem(self._history_list.count() - 1)

    # ------------------------------------------------------------ cleanup ---

    def closeEvent(self, event) -> None:  # noqa: N802
        """Ensure clean shutdown when the window is closed."""
        self._on_stop()
        event.accept()
