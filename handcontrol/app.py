"""HandControl application entry point.

Sets up logging, DPI awareness, and launches the PySide6 main window.
"""

from __future__ import annotations

import contextlib
import ctypes
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def _set_dpi_awareness() -> None:
    """Declare Per-Monitor DPI Awareness v2 (Section 3).

    Must be called before any window is created so coordinates for monitors
    and windows are consistent across different DPI scales.
    """
    try:
        # PROCESS_PER_MONITOR_DPI_AWARE_V2 = 2
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        with contextlib.suppress(Exception):
            ctypes.windll.user32.SetProcessDPIAware()


def _fix_windows_mediapipe_path() -> None:
    """Ensure MediaPipe package path uses 8.3 short format on Windows.

    Solves FileNotFoundError in C++ calculator graph loader when folder names
    contain non-ASCII characters (e.g. 'mapeamento_mãos' with 'ã'), both in
    normal Python development and inside PyInstaller frozen binaries.
    """
    if sys.platform != "win32":
        return
    try:
        import os

        import mediapipe.python.solution_base as sb

        target = os.path.abspath(sb.__file__)
        parts_to_append: list[str] = []
        curr = target
        while curr and not os.path.exists(curr):
            curr, tail = os.path.split(curr)
            if tail:
                parts_to_append.append(tail)
            else:
                break

        if curr and os.path.exists(curr):
            buf = ctypes.create_unicode_buffer(1000)
            if ctypes.windll.kernel32.GetShortPathNameW(curr, buf, 1000):
                short_base = buf.value
                if parts_to_append:
                    sb.__file__ = os.path.join(short_base, *reversed(parts_to_append))
                else:
                    sb.__file__ = short_base
    except Exception:
        pass


def _setup_logging() -> None:
    """Configure logging with console + rotating file handler.

    Log files are stored in ``%APPDATA%\\HandControl\\logs\\``.
    No sensitive data is logged (Rule 8 / 9).
    """
    log_dir = Path.home() / "AppData" / "Roaming" / "HandControl" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    # Console.
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    root_logger.addHandler(console_handler)

    # Rotating file (5 MB × 3 backups).
    file_handler = RotatingFileHandler(
        log_dir / "handcontrol.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s (%(filename)s:%(lineno)d): %(message)s"
        )
    )
    root_logger.addHandler(file_handler)


def main() -> None:
    """Application entry point."""
    # DPI awareness must be set before QApplication.
    _set_dpi_awareness()
    _fix_windows_mediapipe_path()

    _setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("HandControl starting")

    # PySide6 import after DPI awareness is set.
    from PySide6.QtWidgets import QApplication

    from handcontrol.ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("HandControl")
    app.setApplicationVersion("0.1.0")

    window = MainWindow()
    window.show()

    exit_code = app.exec()
    logger.info("HandControl exiting with code %d", exit_code)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
