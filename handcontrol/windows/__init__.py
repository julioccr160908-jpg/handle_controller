"""Window and multi-monitor management subsystem (Section 8, Phase 6)."""

from handcontrol.windows.drag import WindowDragSession
from handcontrol.windows.manager import WindowManager
from handcontrol.windows.models import MonitorInfo, WindowInfo, WindowState
from handcontrol.windows.monitors import VirtualDesktop, enumerate_monitors

__all__ = [
    "MonitorInfo",
    "VirtualDesktop",
    "WindowDragSession",
    "WindowInfo",
    "WindowManager",
    "WindowState",
    "enumerate_monitors",
]
