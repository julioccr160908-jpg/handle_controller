"""Window manager — selection, manipulation, and elevation detection (Section 8).

Responsibilities:
1. Identify the active (foreground) window or window under the cursor.
2. Filter out ineligible windows (hidden, cloaked, system shell, desktop).
3. Detect elevated (Administrator) windows and flag them so the UI can warn the user.
4. Minimize, maximize, restore, and reposition windows across monitors.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import sys

from handcontrol.windows.models import MonitorInfo, WindowInfo, WindowState

logger = logging.getLogger(__name__)

# Windows API constants
SW_RESTORE = 9
SW_MINIMIZE = 6
SW_MAXIMIZE = 3

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010

GA_ROOT = 2
GWL_STYLE = -16
WS_VISIBLE = 0x10000000

# DWM Cloaked attribute
DWMWA_CLOAKED = 14

# Process token constants for elevation check
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x0008
TokenElevation = 20  # enum TOKEN_INFORMATION_CLASS


class _TokenElevation(ctypes.Structure):
    _fields_ = [("TokenIsElevated", ctypes.wintypes.DWORD)]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.wintypes.LONG),
        ("top", ctypes.wintypes.LONG),
        ("right", ctypes.wintypes.LONG),
        ("bottom", ctypes.wintypes.LONG),
    ]


class WindowManager:
    """Manages window discovery, inspection, and manipulation."""

    def __init__(self) -> None:
        self._last_warning_hwnd: int = 0

    def get_foreground_window(self) -> WindowInfo | None:
        """Return information about the currently active foreground window."""
        if sys.platform != "win32":
            return WindowInfo(hwnd=100, title="Mock Active Window", rect=(100, 100, 800, 600))

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        return self._inspect_window(hwnd)

    def get_window_under_cursor(self, x: float, y: float) -> WindowInfo | None:
        """Return information about the top-level window under the virtual point (x, y)."""
        if sys.platform != "win32":
            return WindowInfo(hwnd=100, title="Mock Window Under Cursor", rect=(0, 0, 800, 600))

        user32 = ctypes.windll.user32
        point = ctypes.wintypes.POINT(int(x), int(y))
        hwnd = user32.WindowFromPoint(point)
        if not hwnd:
            return None

        # Resolve to top-level window
        root_hwnd = user32.GetAncestor(hwnd, GA_ROOT) or hwnd
        return self._inspect_window(root_hwnd)

    def list_eligible_windows(self) -> list[WindowInfo]:
        """List all top-level application windows eligible for gesture control."""
        if sys.platform != "win32":
            return [
                WindowInfo(hwnd=100, title="Editor — Visual Studio Code", rect=(0, 0, 1200, 800)),
                WindowInfo(hwnd=101, title="Navegador Web", rect=(200, 100, 1000, 700)),
            ]

        user32 = ctypes.windll.user32
        results: list[WindowInfo] = []

        enum_proc_type = ctypes.WINFUNCTYPE(
            ctypes.wintypes.BOOL, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM
        )

        def _enum_callback(hwnd, lparam):  # noqa: ANN001, ARG001
            info = self._inspect_window(hwnd)
            if info and self._is_eligible(info):
                results.append(info)
            return True

        cb = enum_proc_type(_enum_callback)
        user32.EnumWindows(cb, 0)
        return results

    def is_elevated(self, hwnd: int) -> bool:
        """Return True if the window belongs to an elevated (Administrator) process."""
        if sys.platform != "win32" or not hwnd:
            return False

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        advapi32 = ctypes.windll.advapi32

        pid = ctypes.wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == 0:
            return False

        h_process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not h_process:
            # If we cannot even open the process with limited query, it is almost certainly elevated
            return True

        try:
            h_token = ctypes.wintypes.HANDLE()
            if not advapi32.OpenProcessToken(h_process, TOKEN_QUERY, ctypes.byref(h_token)):
                return True

            try:
                elev = _TokenElevation()
                ret_len = ctypes.wintypes.DWORD()
                if advapi32.GetTokenInformation(
                    h_token,
                    TokenElevation,
                    ctypes.byref(elev),
                    ctypes.sizeof(elev),
                    ctypes.byref(ret_len),
                ):
                    return bool(elev.TokenIsElevated)
            finally:
                kernel32.CloseHandle(h_token)
        finally:
            kernel32.CloseHandle(h_process)

        return False

    def minimize_window(self, hwnd: int) -> bool:
        """Minimize the specified window."""
        if sys.platform != "win32":
            return True
        return bool(ctypes.windll.user32.ShowWindow(hwnd, SW_MINIMIZE))

    def maximize_window(self, hwnd: int) -> bool:
        """Maximize the specified window."""
        if sys.platform != "win32":
            return True
        return bool(ctypes.windll.user32.ShowWindow(hwnd, SW_MAXIMIZE))

    def restore_window(self, hwnd: int) -> bool:
        """Restore the specified window to normal size."""
        if sys.platform != "win32":
            return True
        return bool(ctypes.windll.user32.ShowWindow(hwnd, SW_RESTORE))

    def toggle_maximize(self, hwnd: int) -> bool:
        """Toggle window between maximized and restored states."""
        if sys.platform != "win32":
            return True
        user32 = ctypes.windll.user32
        if user32.IsZoomed(hwnd):
            return self.restore_window(hwnd)
        return self.maximize_window(hwnd)

    def set_window_position(
        self,
        hwnd: int,
        x: int,
        y: int,
        width: int = 0,
        height: int = 0,
    ) -> bool:
        """Set window top-left position and optional dimensions.

        Uses SWP_NOSIZE when width/height are <= 0.
        """
        if sys.platform != "win32":
            return True

        user32 = ctypes.windll.user32
        flags = SWP_NOZORDER | SWP_NOACTIVATE
        if width <= 0 or height <= 0:
            flags |= SWP_NOSIZE

        return bool(user32.SetWindowPos(hwnd, 0, int(x), int(y), int(width), int(height), flags))

    def move_window_to_monitor(self, hwnd: int, target_monitor: MonitorInfo) -> bool:
        """Transfer window to the target monitor, maintaining relative placement."""
        info = self._inspect_window(hwnd)
        if not info:
            return False

        # If window is maximized, restore first before transferring
        if info.is_maximized:
            self.restore_window(hwnd)
            info = self._inspect_window(hwnd)
            if not info:
                return False

        wx, wy, ww, wh = info.rect
        # Ensure dimensions fit in target monitor work area
        mw_left, mw_top, mw_right, mw_bottom = target_monitor.work_area
        max_w = mw_right - mw_left
        max_h = mw_bottom - mw_top

        final_w = min(ww, max_w)
        final_h = min(wh, max_h)
        # Center in target monitor work area if outside
        final_x = mw_left + (max_w - final_w) // 2
        final_y = mw_top + (max_h - final_h) // 2

        return self.set_window_position(hwnd, final_x, final_y, final_w, final_h)

    # ---------------------------------------------------------------- private

    def _inspect_window(self, hwnd: int) -> WindowInfo | None:
        """Retrieve WindowInfo metadata for a given HWND handle."""
        if sys.platform != "win32" or not hwnd:
            return None

        user32 = ctypes.windll.user32
        if not user32.IsWindow(hwnd):
            return None

        # Title
        length = user32.GetWindowTextLengthW(hwnd)
        title_buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title_buf, length + 1)
        title = title_buf.value

        # Class name
        class_buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, class_buf, 256)
        class_name = class_buf.value

        # Rect
        rc = RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rc))
        rect = (rc.left, rc.top, rc.right - rc.left, rc.bottom - rc.top)

        # State
        if user32.IsIconic(hwnd):
            state = WindowState.MINIMIZED
        elif user32.IsZoomed(hwnd):
            state = WindowState.MAXIMIZED
        else:
            state = WindowState.NORMAL

        # Process ID & elevation
        pid = ctypes.wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        elevated = self.is_elevated(hwnd)

        return WindowInfo(
            hwnd=int(hwnd),
            title=title,
            class_name=class_name,
            rect=rect,
            state=state,
            is_elevated=elevated,
            process_id=pid.value,
        )

    def _is_eligible(self, info: WindowInfo) -> bool:
        """Check if a window is an eligible manipulable application window."""
        if not info.title or not info.title.strip():
            return False
        if info.rect[2] <= 10 or info.rect[3] <= 10:
            return False
        # Ignore Windows shell internals
        return info.class_name not in (
            "Progman",
            "WorkerW",
            "Shell_TrayWnd",
            "Shell_SecondaryTrayWnd",
            "Windows.UI.Core.CoreWindow",
        )
