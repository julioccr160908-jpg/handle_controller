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

import contextlib
import logging
import threading
import time

import cv2
import numpy as np
from PySide6.QtCore import QEvent, Qt, QTimer, Signal, Slot
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
from handcontrol.config.storage import ConfigManager
from handcontrol.features.extractor import FeatureExtractor
from handcontrol.gestures.engine import GestureEngine
from handcontrol.gestures.state_machine import GestureState
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
from handcontrol.ui.profile_editor import ProfileEditorDialog
from handcontrol.ui.tray import SystemTrayController

logger = logging.getLogger(__name__)

# Rendering the preview is intentionally less frequent than hand processing.
# It keeps the interface responsive on integrated GPUs without adding gesture
# latency; the processing pipeline continues at up to 30 FPS.
_MAX_DISPLAY_FPS = 20
_MAX_PROCESSING_FPS = 30
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

        # --- configuration & profiles (must be loaded before the camera) ---
        self.config_manager = ConfigManager()
        cfg = self.config_manager.config

        # --- pipeline components ---
        self._capture = CaptureThread(
            camera_index=cfg.settings.camera_id,
            width=cfg.settings.camera_width,
            height=cfg.settings.camera_height,
            fps=cfg.settings.camera_fps,
        )
        self._tracker = HandTracker(flip_handedness=cfg.settings.flip_handedness)
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
        self._drag_hand: str | None = None
        self._drag_last_seen_time: float = 0.0
        self._is_quitting = False
        self._processing_interval_s = 1.0 / min(cfg.settings.camera_fps, _MAX_PROCESSING_FPS)

        self._gesture_engine.preferred_hand = cfg.settings.preferred_hand
        self._cursor_controller.config.dead_zone_px = cfg.settings.dead_zone_px
        self._cursor_controller.smoother.filter_x.min_cutoff = cfg.settings.smoothing_min_cutoff
        self._cursor_controller.smoother.filter_x.beta = cfg.settings.smoothing_beta
        self._cursor_controller.smoother.filter_y.min_cutoff = cfg.settings.smoothing_min_cutoff
        self._cursor_controller.smoother.filter_y.beta = cfg.settings.smoothing_beta
        self._region.set_margins(*cfg.settings.active_region_margins)

        active_p = next(
            (p for p in cfg.profiles if p.profile_id == cfg.active_profile_id),
            cfg.profiles[0] if cfg.profiles else None,
        )
        if active_p:
            self._mapper.load_from_profile_model(active_p)

        # --- system tray (Phase 7) ---
        self._tray = SystemTrayController(
            parent_widget=self,
            on_restore=self._restore_from_tray,
            on_quit=self._quit_application,
        )
        self._tray.pause_toggled.connect(self._on_tray_pause_toggled)
        self._tray.real_mode_toggled.connect(self._on_tray_real_mode_toggled)
        self._tray.show()

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

        self._btn_invert_hands = QPushButton()
        self._update_invert_hands_button_text()
        self._btn_invert_hands.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_invert_hands.setToolTip("Alternar detecção de mão Direita / Esquerda se sua câmera espelhar de forma diferente")
        self._btn_invert_hands.clicked.connect(self._on_toggle_invert_hands)
        top_bar.addWidget(self._btn_invert_hands)

        # Profile switcher & editor (Phase 7)
        self._profile_combo = QComboBox()
        self._profile_combo.setStyleSheet("""
            QComboBox {
                background-color: #1a1a2e;
                color: #e0e0e0;
                border: 1px solid #233554;
                border-radius: 6px;
                padding: 4px 8px;
                font-size: 11px;
                min-width: 130px;
            }
            QComboBox QAbstractItemView {
                background-color: #1a1a2e;
                color: #e0e0e0;
                selection-background-color: #0f3460;
            }
        """)
        self._refresh_profile_combo()
        self._profile_combo.currentIndexChanged.connect(self._on_profile_combo_changed)
        top_bar.addWidget(self._profile_combo)

        self._btn_editor = QPushButton("⚙️ Perfis")
        self._btn_editor.setToolTip("Abrir Editor Visual de Perfis e Gestos")
        self._btn_editor.setStyleSheet("""
            QPushButton {
                background-color: #2c3e50;
                color: white;
                font-weight: bold;
                padding: 6px 12px;
                border-radius: 6px;
                border: 1px solid #34495e;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #34495e;
            }
        """)
        self._btn_editor.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_editor.clicked.connect(self._open_profile_editor)
        top_bar.addWidget(self._btn_editor)

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
        self._refresh_window_list()

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
        self._mem_label = QLabel("RAM: — MB")
        self._hands_label = QLabel("Mãos: —")
        self._gesture_label = QLabel("Gesto: —")
        self._action_label = QLabel("Ação: —")
        self._tracking_label = QLabel("⏹  Parado")

        for lbl in (
            self._fps_label,
            self._latency_label,
            self._mem_label,
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

    def _update_invert_hands_button_text(self) -> None:
        is_inverted = self.config_manager.config.settings.flip_handedness
        if is_inverted:
            self._btn_invert_hands.setText("🔄 Mãos: Invertidas")
            self._btn_invert_hands.setStyleSheet("""
                QPushButton {
                    background-color: #5c3c10;
                    color: #ffd166;
                    font-weight: bold;
                    padding: 6px 12px;
                    border-radius: 6px;
                    border: 1px solid #f39c12;
                    font-size: 11px;
                }
                QPushButton:hover { background-color: #7d5218; }
            """)
        else:
            self._btn_invert_hands.setText("🖐️ Mãos: Normal")
            self._btn_invert_hands.setStyleSheet("""
                QPushButton {
                    background-color: #2b3a4a;
                    color: white;
                    font-weight: bold;
                    padding: 6px 12px;
                    border-radius: 6px;
                    border: 1px solid #4a627a;
                    font-size: 11px;
                }
                QPushButton:hover { background-color: #3b5066; }
            """)

    @Slot()
    def _on_toggle_invert_hands(self) -> None:
        new_val = not self.config_manager.config.settings.flip_handedness
        self.config_manager.config.settings.flip_handedness = new_val
        self._tracker.flip_handedness = new_val
        self._update_invert_hands_button_text()
        with contextlib.suppress(Exception):
            self.config_manager.save()
        logger.info("flip_handedness toggled to %s", new_val)

        self._update_video_border()
        self._update_tray()

    @Slot()
    def _on_start(self) -> None:
        """Start or resume capture and tracking."""
        if self._paused:
            self._paused = False
            self._tracking_label.setText("🟢  Rastreando")
            self._btn_pause.setText("⏸  Pausar")
            self._update_video_border()
            self._update_tray()
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
        self._update_tray()
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
        self._update_tray()

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
        self._tracker = HandTracker(
            flip_handedness=self.config_manager.config.settings.flip_handedness
        )
        self._extractor = FeatureExtractor()
        self._gesture_engine = GestureEngine(
            preferred_hand=self.config_manager.config.settings.preferred_hand
        )
        self._mapper = GestureMapper()
        active_profile = next(
            (
                profile
                for profile in self.config_manager.config.profiles
                if profile.profile_id == self.config_manager.config.active_profile_id
            ),
            None,
        )
        if active_profile is not None:
            self._mapper.load_from_profile_model(active_profile)
        self._controller = ActionController()
        if not self._safety.simulation_mode:
            self._controller.swap_executor(self._real_executor)
        self._safety = SafetyManager(self._controller, self._mapper)
        self._metrics.reset()
        self._drag_hand = None
        self._drag_last_seen_time = 0.0

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
        self._mem_label.setText("RAM: — MB")
        self._hands_label.setText("Mãos: —")
        self._gesture_label.setText("Gesto: —")
        self._action_label.setText("Ação: —")
        self._tracking_label.setText("⏹  Parado")
        self._update_video_border()
        self._update_tray()
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
        last_processed_at = 0.0

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

            # Some cameras ignore CAP_PROP_FPS and deliver 60+ frames per
            # second.  Process only the newest frame at the configured rate;
            # this preserves low latency while keeping one CPU core from
            # being saturated by redundant MediaPipe calls.
            now = time.monotonic()
            remaining = self._processing_interval_s - (now - last_processed_at)
            if remaining > 0:
                time.sleep(min(remaining, 0.005))
                continue

            last_frame_id = capture_frame.frame_id
            last_processed_at = now

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

                lms_by_hand = {
                    obs.handedness: [(p.x, p.y) for p in obs.landmarks_normalized]
                    for obs in hand_frame.hands
                }
                gesture_events = self._gesture_engine.update(
                    features_by_hand,
                    hand_frame.timestamp,
                    landmarks_by_hand=lms_by_hand,
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

                if pref_obs and len(pref_obs.landmarks_normalized) > 8:
                    idx_tip = pref_obs.landmarks_normalized[8]
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
                    if not self._safety.simulation_mode and hasattr(self._real_executor, "mouse"):
                        self._real_executor.mouse.release_all()

                # Window drag control (fist gesture on either hand)
                drag_mgr = getattr(self._real_executor.window, "drag", None)
                is_drag_active = drag_mgr is not None and drag_mgr.is_active

                # Check if window_grab or window_release was requested in this frame
                grab_req = next((r for r in requests if r.action_name == "window_grab"), None)
                release_req = next((r for r in requests if r.action_name == "window_release"), None)

                # Determine which hand is grabbing or dragging
                target_obs = None
                if is_drag_active and self._drag_hand:
                    # Maintain tracking on the same hand that started the drag
                    target_obs = next(
                        (obs for obs in hand_frame.hands if obs.handedness == self._drag_hand),
                        None,
                    )

                if target_obs is None and (grab_req is not None or not is_drag_active):
                    # Check if a gesture event indicated which hand started the fist
                    fist_ev = next(
                        (
                            ev
                            for ev in gesture_events
                            if ev.gesture_id == "fist"
                            and ev.phase in (GesturePhase.STARTED, GesturePhase.HELD)
                        ),
                        None,
                    )
                    if fist_ev:
                        target_obs = next(
                            (obs for obs in hand_frame.hands if obs.handedness == fist_ev.hand),
                            None,
                        )
                    if target_obs is None:
                        # Find hand with fist gesture in ACTIVE, CANDIDATE or RELEASING state
                        fist_hands = [
                            obs
                            for obs in hand_frame.hands
                            if any(
                                sm.state
                                in (
                                    GestureState.ACTIVE,
                                    GestureState.CANDIDATE,
                                    GestureState.RELEASING,
                                )
                                for (gid, h), sm in self._gesture_engine._machines.items()
                                if gid == "fist" and h == obs.handedness
                            )
                        ]
                        if fist_hands:
                            # Prefer non-preferred hand if both made fist, else first
                            target_obs = next(
                                (obs for obs in fist_hands if obs.handedness != pref_hand),
                                fist_hands[0],
                            )
                        elif hand_frame.hands:
                            target_obs = next(
                                (obs for obs in hand_frame.hands if obs.handedness != pref_hand),
                                hand_frame.hands[0],
                            )

                if target_obs and len(target_obs.landmarks_normalized) > 0:
                    self._drag_hand = target_obs.handedness
                    self._drag_last_seen_time = hand_frame.timestamp

                    lm = (
                        target_obs.landmarks_normalized[9]
                        if len(target_obs.landmarks_normalized) > 9
                        else target_obs.landmarks_normalized[0]
                    )
                    lx, ly = self._region.map_normalized(lm.x, lm.y)

                    # Inject hand screen coordinates into window_grab request
                    if grab_req is not None:
                        grab_req.parameters["x"] = lx
                        grab_req.parameters["y"] = ly
                        if self._selected_hwnd:
                            grab_req.parameters["hwnd"] = self._selected_hwnd
                        grab_req.parameters.pop("hand", None)

                    # If drag session is active, update window position smoothly
                    if (
                        is_drag_active
                        and not self._safety.commands_paused
                        and not self._safety.is_emergency
                    ):
                        drag_mgr.update_hand_position(lx, ly, timestamp=hand_frame.timestamp)
                else:
                    # Hand was temporarily lost
                    if is_drag_active:
                        # Grace period: 0.40 seconds before cancelling the drag session
                        elapsed_lost = hand_frame.timestamp - self._drag_last_seen_time
                        if elapsed_lost > 0.40:
                            drag_mgr.cancel_grab()
                            self._drag_hand = None

                # Check if state machine for the dragging hand has returned to IDLE or COOLDOWN
                if is_drag_active and self._drag_hand:
                    hand_sm = self._gesture_engine._machines.get(("fist", self._drag_hand))
                    if hand_sm and hand_sm.state in (GestureState.IDLE, GestureState.COOLDOWN):
                        drag_mgr.end_grab()
                        self._drag_hand = None

                # If an explicit window_release request was fired, reset dragging hand
                if release_req is not None:
                    release_req.parameters.pop("hand", None)
                    self._drag_hand = None

                # Also if drag_mgr ended or cancelled, reset drag hand
                if drag_mgr is not None and not drag_mgr.is_active:
                    self._drag_hand = None

                t_action_start = time.monotonic()
                action_results = self._controller.submit_many(requests)
                action_latency_ms = (time.monotonic() - t_action_start) * 1000
                self._metrics.record_action_latency(action_latency_ms)

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
            Qt.TransformationMode.FastTransformation,
        )
        self._video_label.setPixmap(scaled)

        # Update metrics.
        fps = self._metrics.fps
        latency = self._metrics.avg_latency_ms
        mem = self._metrics.memory_rss_mb
        self._fps_label.setText(f"FPS: {fps:.1f}")
        self._latency_label.setText(f"Latência: {latency:.0f} ms")
        self._mem_label.setText(f"RAM: {mem:.0f} MB")

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

    # ------------------------------------------------------------ Phase 7 ---

    def _refresh_profile_combo(self) -> None:
        """Repopulate the profile selection combobox."""
        if not hasattr(self, "_profile_combo"):
            return
        self._profile_combo.blockSignals(True)
        self._profile_combo.clear()
        cfg = self.config_manager.config
        for p in cfg.profiles:
            self._profile_combo.addItem(f"📋 {p.name}", userData=p.profile_id)
        # Select active
        for i in range(self._profile_combo.count()):
            if self._profile_combo.itemData(i) == cfg.active_profile_id:
                self._profile_combo.setCurrentIndex(i)
                break
        self._profile_combo.blockSignals(False)

    def _on_profile_combo_changed(self, index: int) -> None:
        """Handle user changing active profile from top bar combobox."""
        if index < 0 or not hasattr(self, "_profile_combo"):
            return
        profile_id = self._profile_combo.itemData(index)
        if not profile_id:
            return
        cfg = self.config_manager.config
        cfg.active_profile_id = str(profile_id)
        self.config_manager.save(cfg)
        active_p = next((p for p in cfg.profiles if p.profile_id == profile_id), None)
        if active_p:
            self._mapper.load_from_profile_model(active_p)
            logger.info("Active profile changed to '%s'", active_p.name)

    def _open_profile_editor(self) -> None:
        """Open the visual profile, gesture and settings editor."""
        dialog = ProfileEditorDialog(self.config_manager, self, on_apply=self._on_config_applied)
        dialog.exec()

    def _on_config_applied(self) -> None:
        """Callback when settings or profiles are saved in editor."""
        cfg = self.config_manager.config
        self._cursor_controller.config.dead_zone_px = cfg.settings.dead_zone_px
        self._cursor_controller.smoother.filter_x.min_cutoff = cfg.settings.smoothing_min_cutoff
        self._cursor_controller.smoother.filter_x.beta = cfg.settings.smoothing_beta
        self._cursor_controller.smoother.filter_y.min_cutoff = cfg.settings.smoothing_min_cutoff
        self._cursor_controller.smoother.filter_y.beta = cfg.settings.smoothing_beta
        self._gesture_engine.preferred_hand = cfg.settings.preferred_hand
        self._region.set_margins(*cfg.settings.active_region_margins)

        self._refresh_profile_combo()
        active_p = next((p for p in cfg.profiles if p.profile_id == cfg.active_profile_id), None)
        if active_p:
            self._mapper.load_from_profile_model(active_p)

    def _update_tray(self) -> None:
        """Update system tray privacy indicator and state."""
        if hasattr(self, "_tray"):
            is_capturing = self._processing
            is_paused = self._paused
            is_real = not self._safety.simulation_mode
            self._tray.update_state(
                is_capturing=is_capturing,
                is_paused=is_paused,
                is_real_mode=is_real,
            )

    def _restore_from_tray(self) -> None:
        """Restore and raise the main application window."""
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def _quit_application(self) -> None:
        """Cleanly terminate the application from system tray menu."""
        self._is_quitting = True
        self.close()

    def _on_tray_pause_toggled(self, paused: bool) -> None:
        if paused != self._paused:
            self._on_pause()

    def _on_tray_real_mode_toggled(self, real_mode: bool) -> None:
        if real_mode != (not self._safety.simulation_mode):
            self._on_toggle_real_mode()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        if (
            event.type() == QEvent.Type.WindowStateChange
            and self.isMinimized()
            and hasattr(self, "config_manager")
            and self.config_manager.config.settings.minimize_to_tray
        ):
            self.hide()
        super().changeEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        """Ensure clean shutdown or minimize to tray when closed."""
        if hasattr(self, "_is_quitting") and self._is_quitting:
            self._hotkey.stop()
            self._on_stop()
            if hasattr(self, "_tray"):
                self._tray.hide()
            event.accept()
            return

        if hasattr(self, "config_manager") and self.config_manager.config.settings.minimize_to_tray:
            event.ignore()
            self.hide()
            if hasattr(self, "_tray"):
                self._tray.show_message(
                    "HandControl",
                    "O aplicativo continua ativo na bandeja do sistema.",
                )
            return

        self._hotkey.stop()
        self._on_stop()
        if hasattr(self, "_tray"):
            self._tray.hide()
        event.accept()
