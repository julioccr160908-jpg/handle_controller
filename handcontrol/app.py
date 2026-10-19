"""HandControl application entry point.

Sets up logging, DPI awareness, and launches the PySide6 main window.
"""

from __future__ import annotations

import contextlib
import ctypes
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from handcontrol.tracking.mediapipe_runtime import prepare_mediapipe_resources

_qt_dll_directory_handles: list[object] = []


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
    """Ensure MediaPipe package resources use a Windows-safe path.

    Solves FileNotFoundError in the C++ calculator graph loader when folders
    contain non-ASCII characters (e.g. 'mapeamento_mãos' with 'ã'), including
    machines where NTFS 8.3 aliases are disabled.
    """
    prepare_mediapipe_resources()


def _prepare_qt_dll_search_path() -> None:
    """Prioritize the Qt DLLs bundled beside a frozen executable.

    Qt extension modules link against generic names such as ``Qt6Core.dll``.
    If another program has added a different Qt version to ``PATH``, Windows
    may resolve that DLL first and report "procedure not found" when
    ``QtWidgets`` imports.  ``add_dll_directory`` supplies a deterministic,
    process-local search location before PySide6 is imported.
    """
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return

    bundle_dir = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent / "_internal"))
    native_dirs = (bundle_dir / "PySide6", bundle_dir / "shiboken6", bundle_dir)
    valid_dirs = [directory for directory in native_dirs if directory.is_dir()]

    for directory in valid_dirs:
        try:
            # Keep the returned handles alive for the whole process.
            _qt_dll_directory_handles.append(os.add_dll_directory(str(directory)))
        except OSError:
            logging.getLogger(__name__).warning("Unable to add Qt DLL directory: %s", directory)

    # Some Qt plugins use PATH rather than the modern DLL-directory API.
    if valid_dirs:
        os.environ["PATH"] = os.pathsep.join([*(str(path) for path in valid_dirs), os.environ["PATH"]])


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
    _prepare_qt_dll_search_path()

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
