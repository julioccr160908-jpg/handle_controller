"""Main application window — Phase 4 (Section 9 subset).

Displays live webcam video with MediaPipe hand landmarks, active region overlay,
FPS, latency, recognized gesture, last action result, and event history.
Provides Start/Pause/Stop controls, an Emergency Stop button, Simulation vs Real
commands toggle, active region calibration, and global hotkey integration.

Threading model:
    - CaptureThread (Python thread): reads webcam → size-1 buffer.
    - Processing thread (Python thread): MediaPipe → features → gestures
      → mapping → cursor controller → actions → stores result.
    - GUI thread (Qt main thread): QTimer at ≤ 30 Hz polls for latest result
      and updates widgets. No widget is ever touched outside the GUI thread
      (Rule 6).
"""

from __future__ import annotations

import logging
import threading
import time

import cv2
import numpy as np
from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from handcontrol.actions.composite_executor import CompositeExecutor
from handcontrol.actions.controller import ActionController
from handcontrol.actions.keyboard_executor import KeyboardExecutor
from handcontrol.actions.mouse_executor import MouseExecutor, _virtual_screen
from handcontrol.capture.camera import CaptureThread
from handcontrol.features.extractor import FeatureExtractor
from handcontrol.gestures.engine import GestureEngine
from handcontrol.mapping.profile import GestureMapper
from handcontrol.metrics.performance import PerformanceMetrics
from handcontrol.mouse.cursor import CursorController
from handcontrol.mouse.region import ActiveRegion
from handcontrol.safety.hotkey import EmergencyHotkey
from handcontrol.safety.manager import SafetyManager
from handcontrol.tracking.models import (
    ActionRequest,
    ActionResult,
    ActionStatus,
    GestureEvent,
    GesturePhase,
    HandFrame,
)
from handcontrol.tracking.tracker import HandTracker

logger = logging.getLogger(__name__)

_MAX_DISPLAY_FPS = 30
_HISTORY_MAX_ROWS = 60


class ActiveRegionDialog(QDialog):
    """Dialog to calibrate the camera active region margins (Section 6)."""

    def __init__(self, region: ActiveRegion, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Calibração da Região Ativa")
        self.setMinimumWidth(380)
        self._region = region

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        info_lbl = QLabel(
            "A Região Ativa define a área da câmera mapeada para a tela inteira.\n"
            "Margens maiores permitem alcançar as bordas da tela com menos movimento."
        )
        info_lbl.setWordWrap(True)
        info_lbl.setStyleSheet("color: #ccc; font-size: 12px;")
        layout.addWidget(info_lbl)

        # Screen metrics
        screen_lbl = QLabel(
            f"🖥  Tela Virtual: {region.screen_width}×{region.screen_height} px "
            f"(offset: x={region.screen_left}, y={region.screen_top})"
        )
        screen_lbl.setStyleSheet("color: #88c0d0; font-weight: bold; font-size: 11px;")
        layout.addWidget(screen_lbl)

        # Margin slider
        self._slider_lbl = QLabel(f"Margem atual: {int(region.left_frac * 100)} %")
        self._slider_lbl.setStyleSheet("font-weight: bold; font-size: 12px;")
        layout.addWidget(self._slider_lbl)

        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(5, 30)
        self._slider.setValue(int(region.left_frac * 100))
        self._slider.valueChanged.connect(self._on_slider_changed)
        layout.addWidget(self._slider)

        # Presets
        preset_layout = QHBoxLayout()
        for name, pct in [("Ampla (5%)", 5), ("Padrão (10%)", 10), ("Compacta (20%)", 20)]:
            btn = QPushButton(name)
            btn.clicked.connect(lambda checked=False, p=pct: self._slider.setValue(p))
            preset_layout.addWidget(btn)
        layout.addLayout(preset_layout)

        # Close button
        btn_close = QPushButton("OK — Fechar")
        btn_close.clicked.connect(self.accept)
        layout.addWidget(btn_close)

    def _on_slider_changed(self, value: int) -> None:
        self._slider_lbl.setText(f"Margem atual: {value} %")
        self._region.set_margins(value / 100.0)


class MainWindow(QMainWindow):
    """HandControl main window with video, landmarks, controls and metrics."""

    emergency_triggered = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("HandControl — Controle por Gestos")
        self.setMinimumSize(980, 720)

        # --- virtual screen & active region ---
        try:
            vl, vt, vw, vh = _virtual_screen()
        except Exception:
            vl, vt, vw, vh = 0, 0, 1920, 1080

        self._region = ActiveRegion(
            frame_width=640,
            frame_height=480,
            screen_left=vl,
            screen_top=vt,
            screen_width=vw,
            screen_height=vh,
            left_frac=0.10,
            top_frac=0.10,
            right_frac=0.90,
            bottom_frac=0.90,
        )
        self._cursor_controller = CursorController(region=self._region)

        # --- pipeline components ---
        self._capture = CaptureThread()
        self._tracker = HandTracker()
        self._extractor = FeatureExtractor()
        self._gesture_engine = GestureEngine()
        self._mapper = GestureMapper()
        self._controller = ActionController()  # SimulatedExecutor by default
        self._mouse_executor = MouseExecutor()
        self._keyboard_executor = KeyboardExecutor()
        self._real_executor = CompositeExecutor(
            mouse_executor=self._mouse_executor,
            keyboard_executor=self._keyboard_executor,
        )
        self._safety = SafetyManager(self._controller, self._mapper)
        self._metrics = PerformanceMetrics()
        self._confirmed_real_mode_in_session = False
        self._selected_hwnd: int = 0

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

        # --- global emergency hotkey (Ctrl+Shift+F12) ---
        self.emergency_triggered.connect(self._on_emergency)
        self._hotkey = EmergencyHotkey(callback=self.emergency_triggered.emit)
        self._hotkey.start()

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

        # Style constants
        bg_dark = "#0f0f1a"
        bg_panel = "#1a1a2e"
        bg_accent = "#16213e"
        fg_text = "#e0e0e0"
        border_color = "#233554"

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

        # ── Status bar / simulation vs real badge + mode controls ─────────
        top_bar = QHBoxLayout()
        top_bar.setSpacing(10)

        self._sim_badge = QLabel("🔒  MODO SIMULAÇÃO — Nenhuma ação real será executada")
        self._sim_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sim_badge.setStyleSheet("""
            QLabel {
                background-color: #1a6b3a;
                color: white;
                font-size: 12px;
                font-weight: bold;
                padding: 6px 14px;
                border-radius: 6px;
                border: 1px solid #2ecc71;
            }
        """)
        top_bar.addWidget(self._sim_badge, stretch=1)

        self._btn_mode = QPushButton("⚡  Ativar Comandos Reais")
        self._btn_mode.setStyleSheet("""
            QPushButton {
                background-color: #d35400;
                color: white;
                font-weight: bold;
                padding: 6px 14px;
                border-radius: 6px;
                border: 1px solid #e67e22;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #e67e22;
            }
        """)
        self._btn_mode.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_mode.clicked.connect(self._on_toggle_real_mode)
        top_bar.addWidget(self._btn_mode)

        self._btn_calibrate = QPushButton("📐  Região Ativa")
        self._btn_calibrate.setStyleSheet("""
            QPushButton {
                background-color: #2b3a4a;
                color: white;
                font-weight: bold;
                padding: 6px 14px;
                border-radius: 6px;
                border: 1px solid #4a627a;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #3b5066;
            }
        """)
        self._btn_calibrate.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_calibrate.clicked.connect(self._on_calibrate_region)
        top_bar.addWidget(self._btn_calibrate)

        root.addLayout(top_bar)

        # ── Window selection & manipulation bar (Section 8.1, 8.5) ────────
        win_bar = QHBoxLayout()
        win_bar.setSpacing(8)

        win_lbl = QLabel("🪟 Alvo:")
        win_lbl.setStyleSheet("font-weight: bold; font-size: 11px; color: #a0aec0;")
        win_bar.addWidget(win_lbl)

        self._window_combo = QComboBox()
        self._window_combo.setStyleSheet("""
            QComboBox {
                background-color: #1a1a2e;
                color: #e0e0e0;
                border: 1px solid #233554;
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 11px;
                min-width: 260px;
            }
            QComboBox QAbstractItemView {
                background-color: #1a1a2e;
                color: #e0e0e0;
                selection-background-color: #0f3460;
            }
        """)
        self._window_combo.addItem("🪟 Janela Ativa (Foco Automático)", userData=0)
        self._window_combo.currentIndexChanged.connect(self._on_window_selected)
        win_bar.addWidget(self._window_combo, stretch=1)

        self._btn_refresh_windows = QPushButton("🔄")
        self._btn_refresh_windows.setToolTip("Atualizar lista de janelas elegíveis")
        self._btn_refresh_windows.setStyleSheet("""
            QPushButton {
                background-color: #233554;
                color: white;
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 11px;
            }
            QPushButton:hover { background-color: #3b5066; }
        """)
        self._btn_refresh_windows.clicked.connect(self._refresh_window_list)
        win_bar.addWidget(self._btn_refresh_windows)

        # Quick window actions
        for icon_text, action_id, tip in [
            ("🗕", "window_minimize", "Minimizar janela"),
            ("🗖", "window_toggle_maximize", "Maximizar/Restaurar janela"),
            ("🖥", "window_move_to_monitor", "Transferir para próximo monitor"),
        ]:
            btn = QPushButton(icon_text)
            btn.setToolTip(tip)
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #16213e;
                    color: white;
                    border: 1px solid #233554;
                    border-radius: 4px;
                    padding: 4px 10px;
                    font-size: 11px;
                }
                QPushButton:hover { background-color: #0f3460; }
            """)
            btn.clicked.connect(lambda checked=False, a=action_id: self._trigger_window_action(a))
            win_bar.addWidget(btn)

        root.addLayout(win_bar)

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
        self._update_video_border()
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
                background-color: #0f3460;
            }}
        """)
        self._history_list.setMinimumWidth(250)
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

        btn_style = """
            QPushButton {
                background-color: #0f3460;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 10px 24px;
                font-size: 13px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #1a508b;
            }
            QPushButton:pressed {
                background-color: #0a2647;
            }
            QPushButton:disabled {
                background-color: #2a2a3a;
                color: #555;
            }
        """

        emerg_style = """
            QPushButton {
                background-color: #c0392b;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 10px 24px;
                font-size: 13px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #e74c3c;
            }
            QPushButton:pressed {
                background-color: #922b21;
            }
            QPushButton:disabled {
                background-color: #2a2a3a;
                color: #555;
            }
        """

        self._btn_start = QPushButton("▶  Iniciar")
        self._btn_pause = QPushButton("⏸  Pausar")
        self._btn_stop = QPushButton("⏹  Parar")
        self._btn_emergency = QPushButton("🛑  Parada de Emergência (Ctrl+Shift+F12)")

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

    def _update_video_border(self) -> None:
        """Update video label border color based on current application state."""
        if self._safety.is_emergency:
            border_css = "3px solid #e74c3c"  # Red
        elif not self._safety.simulation_mode:
            border_css = "3px solid #f39c12"  # Amber
        elif self._processing and not self._paused:
            border_css = "2px solid #2ecc71"  # Green
        else:
            border_css = "2px solid #233554"  # Subtle blue-gray

        self._video_label.setStyleSheet(f"""
            QLabel {{
                background-color: #1a1a2e;
                color: #e0e0e0;
                border: {border_css};
                border-radius: 10px;
                font-size: 18px;
                padding: 4px;
            }}
        """)

    # --------------------------------------------------------------- slots --

    @Slot()
    def _on_calibrate_region(self) -> None:
        """Open the active region calibration dialog."""
        dlg = ActiveRegionDialog(self._region, self)
        dlg.exec()

    @Slot(int)
    def _on_window_selected(self, index: int) -> None:
        """Handle window selection change from combobox."""
        self._selected_hwnd = self._window_combo.currentData() or 0
        if self._selected_hwnd and hasattr(self._real_executor, "window"):
            info = self._real_executor.window.wm._inspect_window(self._selected_hwnd)
            if info and info.is_elevated:
                self._add_history_entry(
                    ActionResult(
                        action_name="window_select",
                        status=ActionStatus.REFUSED,
                        reason="⚠ Janela elevada (Admin) — requer privilégios",
                        timestamp=time.monotonic(),
                    )
                )

    @Slot()
    def _refresh_window_list(self) -> None:
        """Refresh the list of eligible application windows in the combobox."""
        current_hwnd = self._selected_hwnd
        self._window_combo.blockSignals(True)
        self._window_combo.clear()
        self._window_combo.addItem("🪟 Janela Ativa (Foco Automático)", userData=0)

        wm = getattr(self._real_executor, "window", None)
        if wm:
            windows = wm.wm.list_eligible_windows()
            selected_idx = 0
            for i, w in enumerate(windows, start=1):
                prefix = "🛡 [Admin] " if w.is_elevated else ""
                title_short = (w.title[:35] + "…") if len(w.title) > 35 else w.title
                self._window_combo.addItem(f"{prefix}{title_short}", userData=w.hwnd)
                if w.hwnd == current_hwnd:
                    selected_idx = i
            self._window_combo.setCurrentIndex(selected_idx)

        self._window_combo.blockSignals(False)

    def _trigger_window_action(self, action_name: str) -> None:
        """Trigger an explicit window management action from UI."""
        params: dict[str, int] = {}
        if self._selected_hwnd:
            params["hwnd"] = self._selected_hwnd
        req = ActionRequest(action_name=action_name, parameters=params, source_gesture="ui_button")
        res = self._controller.submit(req)
        self._add_history_entry(res)

    @Slot()
    def _on_toggle_real_mode(self) -> None:
        """Toggle between simulation mode and real Windows mouse/action execution."""
        if self._safety.simulation_mode:
            # First time in session: explicit confirmation dialog (Section 11.1).
            if not self._confirmed_real_mode_in_session:
                reply = QMessageBox.warning(
                    self,
                    "Confirmar Ativação de Comandos Reais",
                    "Atenção: O HandControl passará a mover o cursor do mouse e "
                    "executar cliques reais no seu computador Windows.\n\n"
                    "Recursos de segurança ativos:\n"
                    "• Pressione Ctrl+Shift+F12 a qualquer momento para PARADA DE EMERGÊNCIA "
                    "(mesmo fora da janela do app).\n"
                    "• Soltar a mão ou cobrir a câmera congela o cursor e libera os botões.\n"
                    "• O botão vermelho de Emergência na tela também interrompe imediatamente.\n\n"
                    "Deseja ativar os comandos reais agora?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
                self._confirmed_real_mode_in_session = True

            self._safety.enable_real_mode(self._real_executor)
            self._sim_badge.setText("⚡  MODO REAL ATIVO — Comandos controlam o mouse do sistema")
            self._sim_badge.setStyleSheet("""
                QLabel {
                    background-color: #8c4200;
                    color: white;
                    font-size: 12px;
                    font-weight: bold;
                    padding: 6px 14px;
                    border-radius: 6px;
                    border: 1px solid #f39c12;
                }
            """)
            self._btn_mode.setText("🔒  Voltar para Simulação")
            self._btn_mode.setStyleSheet("""
                QPushButton {
                    background-color: #27ae60;
                    color: white;
                    font-weight: bold;
                    padding: 6px 14px;
                    border-radius: 6px;
                    border: 1px solid #2ecc71;
                    font-size: 11px;
                }
                QPushButton:hover {
                    background-color: #2ecc71;
                }
            """)
        else:
            self._safety.enable_simulation_mode()
            self._sim_badge.setText("🔒  MODO SIMULAÇÃO — Nenhuma ação real será executada")
            self._sim_badge.setStyleSheet("""
                QLabel {
                    background-color: #1a6b3a;
                    color: white;
                    font-size: 12px;
                    font-weight: bold;
                    padding: 6px 14px;
                    border-radius: 6px;
                    border: 1px solid #2ecc71;
                }
            """)
            self._btn_mode.setText("⚡  Ativar Comandos Reais")
            self._btn_mode.setStyleSheet("""
                QPushButton {
                    background-color: #d35400;
                    color: white;
                    font-weight: bold;
                    padding: 6px 14px;
                    border-radius: 6px;
                    border: 1px solid #e67e22;
                    font-size: 11px;
                }
                QPushButton:hover {
                    background-color: #e67e22;
                }
            """)

        self._update_video_border()

    @Slot()
    def _on_start(self) -> None:
        """Start or resume capture and tracking."""
        if self._paused:
            self._paused = False
            self._tracking_label.setText("🟢  Rastreando")
            self._btn_pause.setText("⏸  Pausar")
            self._update_video_border()
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
        self._update_video_border()
        logger.info("Tracking started")

    @Slot()
    def _on_pause(self) -> None:
        """Toggle pause state."""
        self._paused = not self._paused
        if self._paused:
            self._tracking_label.setText("⏸  Pausado")
            self._btn_pause.setText("▶  Retomar")
            self._gesture_engine.cancel_all(time.monotonic())
            self._cursor_controller.on_hand_lost()
            self._controller._executor.release_all()
            logger.info("Tracking paused")
        else:
            self._tracking_label.setText("🟢  Rastreando")
            self._btn_pause.setText("⏸  Pausar")
            logger.info("Tracking resumed")
        self._update_video_border()

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
        self._cursor_controller.reset()
        self._safety.full_reset()

        # Re-create pipeline for next start.
        self._tracker = HandTracker()
        self._extractor = FeatureExtractor()
        self._gesture_engine = GestureEngine()
        self._mapper = GestureMapper()
        self._controller = ActionController()
        if not self._safety.simulation_mode:
            self._controller.swap_executor(self._real_executor)
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
        self._update_video_border()
        logger.info("Tracking stopped")

    @Slot()
    def _on_emergency(self) -> None:
        """Activate emergency stop: block all actions and release inputs."""
        if self._safety.is_emergency:
            # Second press clears the emergency.
            self._safety.reset_emergency()
            self._btn_emergency.setText("🛑  Parada de Emergência (Ctrl+Shift+F12)")
            self._tracking_label.setText("🟢  Rastreando" if not self._paused else "⏸  Pausado")
            logger.info("Emergency cleared by user")
        else:
            self._safety.emergency_stop()
            self._cursor_controller.reset()
            self._btn_emergency.setText("✅  Retomar (limpar emergência)")
            self._tracking_label.setText("🔴  EMERGÊNCIA")
            logger.warning("Emergency stop activated!")

        self._update_video_border()

    # --------------------------------------------------- processing thread --

    def _processing_loop(self) -> None:
        """Full pipeline loop — runs in a dedicated thread.

        Capture → MediaPipe → features → gestures → mapping + cursor → controller.
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

                # Draw active region overlay on preview
                rx1, ry1, rx2, ry2 = self._region.pixel_rect
                cv2.rectangle(annotated, (rx1, ry1), (rx2, ry2), (70, 160, 70), 1, cv2.LINE_AA)
                c_len = 12
                # Corners
                cv2.line(annotated, (rx1, ry1), (rx1 + c_len, ry1), (100, 255, 100), 2)
                cv2.line(annotated, (rx1, ry1), (rx1, ry1 + c_len), (100, 255, 100), 2)
                cv2.line(annotated, (rx2, ry1), (rx2 - c_len, ry1), (100, 255, 100), 2)
                cv2.line(annotated, (rx2, ry1), (rx2, ry1 + c_len), (100, 255, 100), 2)
                cv2.line(annotated, (rx1, ry2), (rx1 + c_len, ry2), (100, 255, 100), 2)
                cv2.line(annotated, (rx1, ry2), (rx1, ry2 - c_len), (100, 255, 100), 2)
                cv2.line(annotated, (rx2, ry2), (rx2 - c_len, ry2), (100, 255, 100), 2)
                cv2.line(annotated, (rx2, ry2), (rx2, ry2 - c_len), (100, 255, 100), 2)

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

                # Phase 3 & 4: mapping + cursor control.
                requests = self._mapper.process_events(gesture_events)

                # Reset repeat counters for ended/cancelled gestures.
                for ev in gesture_events:
                    if ev.phase in (GesturePhase.ENDED, GesturePhase.CANCELLED):
                        self._mapper.reset_gesture(ev.gesture_id, ev.hand)

                # Cursor control (clutch: pointing pose or sustained drag)
                pref_hand = self._gesture_engine.preferred_hand
                pref_obs = next(
                    (obs for obs in hand_frame.hands if obs.handedness == pref_hand), None
                )

                if pref_obs and len(pref_obs.landmarks) > 8:
                    idx_tip = pref_obs.landmarks[8]
                    active_g = self._gesture_engine.get_active_gestures()
                    is_pointing = any("point" in g for g in active_g)
                    is_dragging = any("pinch_sustained" in g for g in active_g)
                    is_pinching = any("pinch_index" in g for g in active_g)

                    # Freeze cursor during pinch click to avoid displacing click
                    clutch_on = (is_pointing or is_dragging) and not is_pinching

                    cursor_upd = self._cursor_controller.update(
                        norm_x=idx_tip.x,
                        norm_y=idx_tip.y,
                        clutch_on=clutch_on,
                        timestamp=hand_frame.timestamp,
                    )

                    if (
                        cursor_upd.moved
                        and not self._safety.commands_paused
                        and not self._safety.is_emergency
                    ):
                        requests.append(
                            ActionRequest(
                                action_name="cursor_move",
                                parameters={
                                    "x": cursor_upd.x,
                                    "y": cursor_upd.y,
                                    "norm_x": cursor_upd.x,
                                    "norm_y": cursor_upd.y,
                                },
                                source_gesture="point",
                            )
                        )
                else:
                    self._cursor_controller.on_hand_lost()
                    if not self._safety.simulation_mode:
                        self._controller._executor.release_all()

                # Window drag control (left hand fist gesture, Section 8.2)
                left_obs = next((obs for obs in hand_frame.hands if obs.handedness == "Left"), None)
                if left_obs and len(left_obs.landmarks) > 0:
                    lm = (
                        left_obs.landmarks[9]
                        if len(left_obs.landmarks) > 9
                        else left_obs.landmarks[0]
                    )
                    lx, ly = self._region.map_normalized(lm.x, lm.y)

                    # If window_grab was requested, inject hand coordinates
                    for req in requests:
                        if req.action_name == "window_grab" and "x" not in req.parameters:
                            req.parameters["x"] = lx
                            req.parameters["y"] = ly
                            if self._selected_hwnd:
                                req.parameters["hwnd"] = self._selected_hwnd

                    # If drag session is active, update window position
                    if (
                        hasattr(self._real_executor, "window")
                        and self._real_executor.window.drag.is_active
                        and not self._safety.commands_paused
                        and not self._safety.is_emergency
                    ):
                        self._real_executor.window.drag.update_hand_position(
                            lx, ly, timestamp=hand_frame.timestamp
                        )
                else:
                    if (
                        hasattr(self._real_executor, "window")
                        and self._real_executor.window.drag.is_active
                    ):
                        self._real_executor.window.drag.cancel_grab()

                action_results = self._controller.submit_many(requests)

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
        for ev in gesture_events:
            if ev.phase == GesturePhase.STARTED:
                self._last_gesture = ev.gesture_id
            elif ev.phase in (GesturePhase.ENDED, GesturePhase.CANCELLED):
                self._last_gesture = "—"
        self._gesture_label.setText(f"Gesto: {self._last_gesture}")

        # Update action label + history (omit high-frequency cursor_move from history list).
        discrete_actions = [ar for ar in action_results if ar.action_name != "cursor_move"]
        if discrete_actions:
            last = discrete_actions[-1]
            icon = {"SIMULATED": "🟡", "EXECUTED": "🟢", "REFUSED": "🔴", "FAILED": "❌"}.get(
                last.status.value, "•"
            )
            self._action_label.setText(f"Ação: {icon} {last.action_name}")
            for ar in discrete_actions:
                self._add_history_entry(ar)
        elif action_results:
            # Only cursor move occurred
            last = action_results[-1]
            icon = "🟢" if last.status == ActionStatus.EXECUTED else "🟡"
            self._action_label.setText(f"Ação: {icon} {last.action_name}")

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
        elif result.action_name.startswith(("media_", "volume_")):
            label += "  (requer suporte do app em foco)"
        self._history_list.insertItem(0, label)
        while self._history_list.count() > _HISTORY_MAX_ROWS:
            self._history_list.takeItem(self._history_list.count() - 1)

    # ------------------------------------------------------------ cleanup ---

    def closeEvent(self, event) -> None:  # noqa: N802
        """Ensure clean shutdown when the window is closed."""
        self._hotkey.stop()
        self._on_stop()
        event.accept()
