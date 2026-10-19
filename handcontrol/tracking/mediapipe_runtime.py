"""Make MediaPipe resources available from a Windows-safe path.

Some MediaPipe Windows builds still pass resource paths to C++ through a
non-Unicode code path.  Consequently, a virtual environment or an installed
application below a folder such as ``mapeamento_mãos`` cannot load the hand
landmark graph.  Windows installations with 8.3 names disabled (a common
default) cannot use the usual short-path workaround either.

This module first tries the cheap 8.3-path solution.  If that is unavailable,
it mirrors MediaPipe's *data-only* ``modules`` folder into an ASCII-only cache
under the Public Documents folder and points SolutionBase at that mirror.  No
model is downloaded and Python code continues to be imported from the normal
installation.
"""

from __future__ import annotations

import ctypes
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

_RESOURCE_MARKER = ".handcontrol-mediapipe-resources"


def prepare_mediapipe_resources() -> None:
    """Patch MediaPipe's resource root when its installed path is non-ASCII.

    The function is safe to call more than once and is intentionally a no-op
    on systems where MediaPipe already lives in an ASCII-only path.
    """
    if sys.platform != "win32":
        return

    try:
        import mediapipe
        import mediapipe.python.solution_base as solution_base

        current_file = Path(os.path.abspath(solution_base.__file__))
        if _is_ascii_path(current_file):
            return

        short_path = _windows_short_path(current_file)
        if short_path is not None and _is_ascii_path(short_path):
            solution_base.__file__ = str(short_path)
            logger.info("Using Windows short path for MediaPipe resources")
            return

        source_package = Path(mediapipe.__file__).resolve().parent
        mirror_package = _prepare_ascii_resource_mirror(source_package, mediapipe.__version__)
        if mirror_package is None:
            logger.warning(
                "Could not create an ASCII-safe MediaPipe resource cache; "
                "hand tracking may not start from this folder."
            )
            return

        # SolutionBase only uses __file__ to calculate its resource root.
        # The placeholder need not contain executable Python code.
        solution_base.__file__ = str(mirror_package / "python" / "solution_base.py")
        logger.info("Using ASCII-safe MediaPipe resource cache: %s", mirror_package)
    except Exception:
        # Keep importing the application possible and preserve the original
        # MediaPipe exception if a platform-specific setup is irrecoverable.
        logger.exception("Failed to prepare MediaPipe resource path")


def _prepare_ascii_resource_mirror(source_package: Path, version: str) -> Path | None:
    """Copy the bundled MediaPipe model resources into an ASCII-only cache."""
    source_modules = source_package / "modules"
    if not source_modules.is_dir():
        logger.warning("MediaPipe modules directory not found: %s", source_modules)
        return None

    cache_root = _find_writable_ascii_cache_dir()
    if cache_root is None:
        return None

    # A versioned cache makes upgrades safe and avoids replacing files used by
    # another running instance of the application.
    cache_name = f"mediapipe-{_safe_version(version)}"
    destination = cache_root / cache_name / "mediapipe"
    marker = destination / _RESOURCE_MARKER
    graph = destination / "modules" / "hand_landmark" / "hand_landmark_tracking_cpu.binarypb"
    if marker.is_file() and graph.is_file():
        return destination

    staging_parent = cache_root / f".{cache_name}-{os.getpid()}"
    staging_package = staging_parent / "mediapipe"
    try:
        if staging_parent.exists():
            shutil.rmtree(staging_parent)
        shutil.copytree(source_modules, staging_package / "modules")
        (staging_package / "python").mkdir(parents=True, exist_ok=True)
        (staging_package / _RESOURCE_MARKER).write_text("HandControl MediaPipe resources\n")

        version_root = destination.parent
        version_root.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            os.replace(staging_package, destination)
        return destination if graph.is_file() else None
    except OSError:
        logger.exception("Unable to mirror MediaPipe resources to %s", destination)
        return None
    finally:
        if staging_parent.exists():
            shutil.rmtree(staging_parent, ignore_errors=True)


def _find_writable_ascii_cache_dir() -> Path | None:
    """Return a writable cache directory whose full path contains ASCII only."""
    public_dir = os.environ.get("PUBLIC", r"C:\Users\Public")
    candidates = (
        Path(public_dir) / "Documents" / "HandControlRuntime",
        Path(tempfile.gettempdir()) / "HandControlRuntime",
    )
    for candidate in candidates:
        if not _is_ascii_path(candidate):
            continue
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except OSError:
            logger.debug("MediaPipe cache directory is not writable: %s", candidate)
    return None


def _windows_short_path(path: Path) -> Path | None:
    """Return an ASCII 8.3 path when NTFS short names are available."""
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        length = ctypes.windll.kernel32.GetShortPathNameW(str(path), buffer, len(buffer))
        return Path(buffer.value) if length else None
    except Exception:
        return None


def _is_ascii_path(path: Path) -> bool:
    try:
        str(path).encode("ascii")
    except UnicodeEncodeError:
        return False
    return True


def _safe_version(version: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ".-_" else "_" for ch in version)
