"""Monitor enumeration and virtual desktop geometry (Section 8.4, Phase 6).

Enumerates all connected monitors, their resolutions, work areas, DPI scales,
and relative placement in Windows virtual desktop space (including negative coordinates).

Supports arbitrary monitor layouts (monitors to the left, above, below, or different scales).
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import sys
from collections.abc import Callable

from handcontrol.windows.models import MonitorInfo

logger = logging.getLogger(__name__)

# Windows API constants
MONITORINFOF_PRIMARY = 0x0001
CCHDEVICENAME = 32


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.wintypes.LONG),
        ("top", ctypes.wintypes.LONG),
        ("right", ctypes.wintypes.LONG),
        ("bottom", ctypes.wintypes.LONG),
    ]


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.wintypes.DWORD),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("szDevice", ctypes.c_wchar * CCHDEVICENAME),
    ]


class VirtualDesktop:
    """Represents the multi-monitor virtual desktop environment."""

    def __init__(self, monitors: list[MonitorInfo]) -> None:
        self._monitors = sorted(monitors, key=lambda m: m.index)
        self._calculate_bounds()

    def _calculate_bounds(self) -> None:
        if not self._monitors:
            self._virtual_rect = (0, 0, 1920, 1080)
            return

        min_left = min(m.left for m in self._monitors)
        min_top = min(m.top for m in self._monitors)
        max_right = max(m.right for m in self._monitors)
        max_bottom = max(m.bottom for m in self._monitors)
        self._virtual_rect = (min_left, min_top, max_right - min_left, max_bottom - min_top)

    @property
    def monitors(self) -> list[MonitorInfo]:
        """List of all detected monitors."""
        return list(self._monitors)

    @property
    def monitor_count(self) -> int:
        return len(self._monitors)

    @property
    def virtual_rect(self) -> tuple[int, int, int, int]:
        """Bounding rectangle (left, top, width, height) of the total virtual desktop."""
        return self._virtual_rect

    @property
    def primary_monitor(self) -> MonitorInfo | None:
        """The primary monitor (origin at (0, 0)), or None."""
        for m in self._monitors:
            if m.is_primary:
                return m
        return self._monitors[0] if self._monitors else None

    def get_monitor_for_point(self, x: float, y: float) -> MonitorInfo | None:
        """Find which monitor contains the given virtual desktop point (x, y)."""
        for m in self._monitors:
            if m.contains_point(x, y):
                return m
        return self.primary_monitor

    def get_monitor_for_rect(
        self,
        rect: tuple[int, int, int, int],
    ) -> MonitorInfo | None:
        """Find the monitor with the largest intersection area for a window rect."""
        rx, ry, rw, rh = rect
        r_left, r_top, r_right, r_bottom = rx, ry, rx + rw, ry + rh

        best_m: MonitorInfo | None = None
        best_area = -1

        for m in self._monitors:
            # Calculate intersection
            ix1 = max(r_left, m.left)
            iy1 = max(r_top, m.top)
            ix2 = min(r_right, m.right)
            iy2 = min(r_bottom, m.bottom)
            if ix2 > ix1 and iy2 > iy1:
                area = (ix2 - ix1) * (iy2 - iy1)
                if area > best_area:
                    best_area = area
                    best_m = m

        return best_m or self.primary_monitor

    def get_next_monitor(self, current_index: int) -> MonitorInfo:
        """Return the next monitor in round-robin order."""
        if not self._monitors:
            raise ValueError("No monitors available")
        next_idx = (current_index + 1) % len(self._monitors)
        return self._monitors[next_idx]


def _default_windows_enum() -> list[MonitorInfo]:
    """Native Windows monitor enumeration using user32.EnumDisplayMonitors."""
    monitors: list[MonitorInfo] = []

    if sys.platform != "win32":
        return [
            MonitorInfo(
                index=0,
                hmonitor=1,
                rect=(0, 0, 1920, 1080),
                work_area=(0, 0, 1920, 1040),
                is_primary=True,
                scale_factor=1.0,
                device_name="GenericDisplay",
            )
        ]

    user32 = ctypes.windll.user32

    monitor_enum_proc_type = ctypes.WINFUNCTYPE(
        ctypes.wintypes.BOOL,
        ctypes.wintypes.HMONITOR,
        ctypes.wintypes.HDC,
        ctypes.POINTER(RECT),
        ctypes.wintypes.LPARAM,
    )

    def _callback(hmonitor, hdc, lprect, lparam):  # noqa: ANN001, ARG001
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            rc = info.rcMonitor
            rc_work = info.rcWork
            is_pri = bool(info.dwFlags & MONITORINFOF_PRIMARY)
            idx = len(monitors)

            # Detect scale factor if available via shcore
            scale = 1.0
            try:
                shcore = ctypes.windll.shcore
                dpi_x = ctypes.wintypes.UINT()
                dpi_y = ctypes.wintypes.UINT()
                # MDT_EFFECTIVE_DPI = 0
                if (
                    shcore.GetDpiForMonitor(hmonitor, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y))
                    == 0
                ):
                    scale = dpi_x.value / 96.0
            except Exception:
                pass

            monitors.append(
                MonitorInfo(
                    index=idx,
                    hmonitor=int(hmonitor),
                    rect=(rc.left, rc.top, rc.right, rc.bottom),
                    work_area=(rc_work.left, rc_work.top, rc_work.right, rc_work.bottom),
                    is_primary=is_pri,
                    scale_factor=scale,
                    device_name=str(info.szDevice),
                )
            )
        return True

    cb = monitor_enum_proc_type(_callback)
    user32.EnumDisplayMonitors(None, None, cb, 0)
    return monitors


def enumerate_monitors(
    enum_fn: Callable[[], list[MonitorInfo]] | None = None,
) -> VirtualDesktop:
    """Enumerate all monitors and return a configured VirtualDesktop instance.

    Args:
        enum_fn: Optional callable to replace native Windows enumeration for tests.
    """
    fn = enum_fn or _default_windows_enum
    try:
        monitors = fn()
        if not monitors:
            monitors = [
                MonitorInfo(
                    index=0,
                    hmonitor=1,
                    rect=(0, 0, 1920, 1080),
                    work_area=(0, 0, 1920, 1040),
                    is_primary=True,
                    scale_factor=1.0,
                    device_name="FallbackDisplay",
                )
            ]
    except Exception:
        logger.exception("Error enumerating monitors, using fallback single monitor")
        monitors = [
            MonitorInfo(
                index=0,
                hmonitor=1,
                rect=(0, 0, 1920, 1080),
                work_area=(0, 0, 1920, 1040),
                is_primary=True,
                scale_factor=1.0,
                device_name="FallbackDisplay",
            )
        ]

    return VirtualDesktop(monitors)
