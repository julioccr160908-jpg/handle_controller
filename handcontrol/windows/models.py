"""Data models for windows and multi-monitor management (Section 8).

Defines data structures for monitors, virtual screen geometry, window descriptors,
and window states.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class WindowState(StrEnum):
    """Window display state."""

    NORMAL = "normal"
    MINIMIZED = "minimized"
    MAXIMIZED = "maximized"


@dataclass(frozen=True)
class MonitorInfo:
    """Information about a physical display monitor in virtual desktop space.

    Attributes:
        index: Zero-based index of the monitor.
        hmonitor: Native Windows HMONITOR handle (or mock id).
        rect: (left, top, right, bottom) in virtual screen pixels.
        work_area: (left, top, right, bottom) excluding taskbars/docked bars.
        is_primary: True if this is the primary monitor (origin at (0, 0)).
        scale_factor: DPI scaling factor (1.0 = 100%, 1.25 = 125%, 1.5 = 150%).
        device_name: OS device identifier name (e.g. \\\\.\\DISPLAY1).
    """

    index: int
    hmonitor: int
    rect: tuple[int, int, int, int]
    work_area: tuple[int, int, int, int]
    is_primary: bool = False
    scale_factor: float = 1.0
    device_name: str = ""

    @property
    def left(self) -> int:
        return self.rect[0]

    @property
    def top(self) -> int:
        return self.rect[1]

    @property
    def right(self) -> int:
        return self.rect[2]

    @property
    def bottom(self) -> int:
        return self.rect[3]

    @property
    def width(self) -> int:
        return self.rect[2] - self.rect[0]

    @property
    def height(self) -> int:
        return self.rect[3] - self.rect[1]

    def contains_point(self, x: float, y: float) -> bool:
        """Return True if the coordinates fall within this monitor's bounds."""
        return self.left <= x < self.right and self.top <= y < self.bottom


@dataclass(frozen=True)
class WindowInfo:
    """Information about an active or eligible application window.

    Attributes:
        hwnd: Native Windows HWND window handle.
        title: Window caption / title text.
        class_name: Window class name.
        rect: (x, y, width, height) in virtual screen pixels.
        state: WindowState (NORMAL, MINIMIZED, MAXIMIZED).
        is_elevated: True if window belongs to an elevated (Administrator) process.
        process_id: Native process identifier PID.
    """

    hwnd: int
    title: str
    class_name: str = ""
    rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    state: WindowState = WindowState.NORMAL
    is_elevated: bool = False
    process_id: int = 0

    @property
    def x(self) -> int:
        return self.rect[0]

    @property
    def y(self) -> int:
        return self.rect[1]

    @property
    def width(self) -> int:
        return self.rect[2]

    @property
    def height(self) -> int:
        return self.rect[3]

    @property
    def is_minimized(self) -> bool:
        return self.state == WindowState.MINIMIZED

    @property
    def is_maximized(self) -> bool:
        return self.state == WindowState.MAXIMIZED
