"""System tray icon and background notification area controller (Section 10).

Differentiates 4 states:
1. Minimizar: window hidden, tracking continues if active.
2. Pausar: tracking continues, gesture translation is paused.
3. Desligar a câmera: capture device released.
4. Encerrar: process cleanly terminates, all inputs released.

Provides dynamic privacy camera indicators, context menu, and window restore.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

logger = logging.getLogger(__name__)


def create_tray_pixmap(color: QColor, text: str = "H") -> QPixmap:
    """Generate a clean colored badge pixmap for the system tray icon."""
    pix = QPixmap(32, 32)
    pix.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Base circle
    painter.setBrush(color)
    painter.setPen(QColor(30, 30, 30))
    painter.drawEllipse(2, 2, 28, 28)

    # Text indicator
    painter.setPen(QColor(255, 255, 255))
    font = painter.font()
    font.setPixelSize(16)
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(pix.rect(), 0x0084, text)  # AlignCenter
    painter.end()
    return pix


class SystemTrayController(QObject):
    """Manages the system tray icon, privacy indicator, and background actions."""

    restore_requested = Signal()
    pause_toggled = Signal(bool)
    real_mode_toggled = Signal(bool)
    quit_requested = Signal()

    def __init__(
        self,
        parent_widget: QWidget | None = None,
        on_restore: Callable[[], None] | None = None,
        on_quit: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent_widget)
        self._parent = parent_widget

        # Pre-render state icons
        self._icon_active_real = QIcon(
            create_tray_pixmap(QColor(46, 204, 113), "R")
        )  # Green (Real)
        self._icon_active_sim = QIcon(create_tray_pixmap(QColor(52, 152, 219), "S"))  # Blue (Sim)
        self._icon_paused = QIcon(create_tray_pixmap(QColor(241, 196, 15), "P"))  # Yellow (Paused)
        self._icon_stopped = QIcon(create_tray_pixmap(QColor(149, 165, 166), "X"))  # Gray (Off)

        self._tray = QSystemTrayIcon(self._icon_active_sim, parent_widget)
        self._menu = QMenu(parent_widget)
        self._build_menu()

        self._tray.setContextMenu(self._menu)
        self._tray.activated.connect(self._on_tray_activated)

        if on_restore:
            self.restore_requested.connect(on_restore)
        if on_quit:
            self.quit_requested.connect(on_quit)

        self.update_state(is_capturing=False, is_paused=False, is_real_mode=False)

    def show(self) -> None:
        """Display the tray icon in the system notification area."""
        self._tray.show()

    def hide(self) -> None:
        """Hide the tray icon."""
        self._tray.hide()

    def show_message(self, title: str, message: str) -> None:
        """Display a system tray balloon notification."""
        self._tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 3000)

    def update_state(
        self,
        is_capturing: bool,
        is_paused: bool,
        is_real_mode: bool,
    ) -> None:
        """Update icon appearance and tooltip to reflect system state (Section 10)."""
        self._action_pause.setChecked(is_paused)
        self._action_real_mode.setChecked(is_real_mode)

        if not is_capturing:
            self._tray.setIcon(self._icon_stopped)
            self._tray.setToolTip("HandControl — Câmera Desligada")
        elif is_paused:
            self._tray.setIcon(self._icon_paused)
            self._tray.setToolTip("HandControl — Comandos Pausados (Câmera Ativa)")
        elif is_real_mode:
            self._tray.setIcon(self._icon_active_real)
            self._tray.setToolTip("HandControl — Rastreando (Comandos Reais Ativos)")
        else:
            self._tray.setIcon(self._icon_active_sim)
            self._tray.setToolTip("HandControl — Rastreando (Modo Simulação)")

    # ---------------------------------------------------------------- private

    def _build_menu(self) -> None:
        self._action_restore = QAction("Abrir HandControl", self)
        self._action_restore.triggered.connect(self.restore_requested.emit)
        self._menu.addAction(self._action_restore)

        self._menu.addSeparator()

        self._action_real_mode = QAction("Modo Comandos Reais", self)
        self._action_real_mode.setCheckable(True)
        self._action_real_mode.toggled.connect(self.real_mode_toggled.emit)
        self._menu.addAction(self._action_real_mode)

        self._action_pause = QAction("Pausar Comandos", self)
        self._action_pause.setCheckable(True)
        self._action_pause.toggled.connect(self.pause_toggled.emit)
        self._menu.addAction(self._action_pause)

        self._menu.addSeparator()

        self._action_quit = QAction("Encerrar", self)
        self._action_quit.triggered.connect(self.quit_requested.emit)
        self._menu.addAction(self._action_quit)

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.restore_requested.emit()
