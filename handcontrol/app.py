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
