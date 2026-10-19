"""Unit and integration tests for Phase 6 — Window Management and Multi-monitor (Section 8).

Tests:
1. MonitorInfo & VirtualDesktop:
   - Monitor geometry properties (left, top, right, bottom, width, height, contains_point).
   - Multi-monitor setups: side-by-side, negative coordinates (left, above), varied DPI scales.
   - Point to monitor mapping (including boundary and fallback).
   - Rect to monitor mapping (largest intersection area).
   - Next monitor round-robin traversal.
   - Fallback enumeration on empty/exception.
2. WindowManager:
   - Window eligibility filter (titles, dimensions, shell classes).
   - Minimize, maximize, restore, toggle_maximize.
   - Move window to monitor with centering and work area constraints.
3. WindowDragSession:
   - Relative grab offset calculation (ZERO initial jump).
   - Movement tracking matching hand displacement.
   - 60 Hz rate limiting and frame coalescence.
   - Redundant movement suppression.
   - Elevated process protection (refusal with warning).
   - Auto-restore from maximized state on grab.
   - Release and cancel on hand loss / pause.
4. WindowExecutor:
   - Execution of all window actions (window_grab, window_release, window_minimize,
     window_maximize, window_restore, window_toggle_maximize, window_move_to_monitor).
   - Refusal on elevated windows or missing targets.
   - release_all cancels active drag.
5. CompositeExecutor:
   - Routing between mouse, keyboard, and window executors.
   - Cascading release_all to all sub-executors.
6. Allowlist:
   - Window actions registered with valid parameter schemas.
7. Phase 6 End-to-End:
   - Full pipeline integration: Controller + SafetyManager + CompositeExecutor + WindowExecutor.
"""

from __future__ import annotations

import pytest

from handcontrol.actions.allowlist import Allowlist
from handcontrol.actions.composite_executor import CompositeExecutor
from handcontrol.actions.controller import ActionController
from handcontrol.actions.keyboard_executor import KeyboardExecutor
from handcontrol.actions.mouse_executor import MouseExecutor
from handcontrol.actions.window_executor import WindowExecutor
from handcontrol.mapping.profile import GestureMapper
from handcontrol.safety.manager import SafetyManager
from handcontrol.tracking.models import ActionRequest, ActionResult, ActionStatus
from handcontrol.windows.drag import WindowDragSession
from handcontrol.windows.manager import WindowManager
from handcontrol.windows.models import MonitorInfo, WindowInfo, WindowState
from handcontrol.windows.monitors import VirtualDesktop, enumerate_monitors

# ---------------------------------------------------------------------------
# Test Fixtures and Mocks
# ---------------------------------------------------------------------------


class MockWindowManager(WindowManager):
    """Test double for WindowManager that tracks calls and avoids native OS interactions."""

    def __init__(self, windows: list[WindowInfo] | None = None) -> None:
        super().__init__()
        self.windows_map: dict[int, WindowInfo] = {w.hwnd: w for w in (windows or [])}
        self.foreground_hwnd: int = windows[0].hwnd if windows else 0
        self.moved_calls: list[tuple[int, int, int, int, int]] = []
        self.state_changes: list[tuple[int, str]] = []

    def get_foreground_window(self) -> WindowInfo | None:
        return self.windows_map.get(self.foreground_hwnd)

    def get_window_under_cursor(self, x: float, y: float) -> WindowInfo | None:
        for w in self.windows_map.values():
            if w.x <= x < w.x + w.width and w.y <= y < w.y + w.height:
                return w
        return self.get_foreground_window()

    def _inspect_window(self, hwnd: int) -> WindowInfo | None:
        return self.windows_map.get(hwnd)

    def is_elevated(self, hwnd: int) -> bool:
        w = self.windows_map.get(hwnd)
        return w.is_elevated if w else False

    def minimize_window(self, hwnd: int) -> bool:
        if hwnd in self.windows_map:
            w = self.windows_map[hwnd]
            self.windows_map[hwnd] = WindowInfo(
                hwnd=w.hwnd,
                title=w.title,
                class_name=w.class_name,
                rect=w.rect,
                state=WindowState.MINIMIZED,
                is_elevated=w.is_elevated,
                process_id=w.process_id,
            )
            self.state_changes.append((hwnd, "minimized"))
            return True
        return False

    def maximize_window(self, hwnd: int) -> bool:
        if hwnd in self.windows_map:
            w = self.windows_map[hwnd]
            self.windows_map[hwnd] = WindowInfo(
                hwnd=w.hwnd,
                title=w.title,
                class_name=w.class_name,
                rect=w.rect,
                state=WindowState.MAXIMIZED,
                is_elevated=w.is_elevated,
                process_id=w.process_id,
            )
            self.state_changes.append((hwnd, "maximized"))
            return True
        return False

    def restore_window(self, hwnd: int) -> bool:
        if hwnd in self.windows_map:
            w = self.windows_map[hwnd]
            self.windows_map[hwnd] = WindowInfo(
                hwnd=w.hwnd,
                title=w.title,
                class_name=w.class_name,
                rect=w.rect,
                state=WindowState.NORMAL,
                is_elevated=w.is_elevated,
                process_id=w.process_id,
            )
            self.state_changes.append((hwnd, "normal"))
            return True
        return False

    def toggle_maximize(self, hwnd: int) -> bool:
        w = self.windows_map.get(hwnd)
        if not w:
            return False
        if w.state == WindowState.MAXIMIZED:
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
        if hwnd in self.windows_map:
            w = self.windows_map[hwnd]
            w_w = width if width > 0 else w.width
            w_h = height if height > 0 else w.height
            self.windows_map[hwnd] = WindowInfo(
                hwnd=w.hwnd,
                title=w.title,
                class_name=w.class_name,
                rect=(x, y, w_w, w_h),
                state=w.state,
                is_elevated=w.is_elevated,
                process_id=w.process_id,
            )
            self.moved_calls.append((hwnd, x, y, w_w, w_h))
            return True
        return False


# ---------------------------------------------------------------------------
# 1. MonitorInfo & VirtualDesktop Tests
# ---------------------------------------------------------------------------


class TestMonitorGeometry:
    def test_single_monitor_properties(self):
        m = MonitorInfo(
            index=0,
            hmonitor=1,
            rect=(0, 0, 1920, 1080),
            work_area=(0, 0, 1920, 1040),
            is_primary=True,
            scale_factor=1.25,
            device_name="\\\\.\\DISPLAY1",
        )
        assert m.left == 0
        assert m.top == 0
        assert m.right == 1920
        assert m.bottom == 1080
        assert m.width == 1920
        assert m.height == 1080
        assert m.is_primary
        assert m.scale_factor == 1.25
        assert m.contains_point(500, 500)
        assert not m.contains_point(1920, 500)
        assert not m.contains_point(-10, 500)

    def test_multi_monitor_side_by_side(self):
        m1 = MonitorInfo(
            index=0,
            hmonitor=1,
            rect=(0, 0, 1920, 1080),
            work_area=(0, 0, 1920, 1040),
            is_primary=True,
        )
        m2 = MonitorInfo(
            index=1,
            hmonitor=2,
            rect=(1920, 0, 3840, 1080),
            work_area=(1920, 0, 3840, 1080),
            is_primary=False,
        )
        vd = VirtualDesktop([m1, m2])
        assert vd.monitor_count == 2
        assert vd.virtual_rect == (0, 0, 3840, 1080)
        assert vd.primary_monitor == m1

        # Point lookups
        assert vd.get_monitor_for_point(500, 500) == m1
        assert vd.get_monitor_for_point(2500, 500) == m2
        # Outside points fallback to primary
        assert vd.get_monitor_for_point(5000, 500) == m1

    def test_multi_monitor_negative_coordinates(self):
        # Secondary monitor to the left: x in [-1920, 0]
        m_left = MonitorInfo(
            index=1,
            hmonitor=2,
            rect=(-1920, 0, 0, 1080),
            work_area=(-1920, 0, 0, 1080),
            is_primary=False,
        )
        # Primary monitor: x in [0, 1920]
        m_pri = MonitorInfo(
            index=0,
            hmonitor=1,
            rect=(0, 0, 1920, 1080),
            work_area=(0, 0, 1920, 1040),
            is_primary=True,
        )
        vd = VirtualDesktop([m_pri, m_left])
        assert vd.virtual_rect == (-1920, 0, 3840, 1080)
        assert vd.get_monitor_for_point(-500, 500) == m_left
        assert vd.get_monitor_for_point(500, 500) == m_pri

    def test_multi_monitor_monitor_above(self):
        # Secondary monitor above: y in [-1080, 0]
        m_above = MonitorInfo(
            index=1,
            hmonitor=2,
            rect=(0, -1080, 1920, 0),
            work_area=(0, -1080, 1920, 0),
            is_primary=False,
        )
        m_pri = MonitorInfo(
            index=0,
            hmonitor=1,
            rect=(0, 0, 1920, 1080),
            work_area=(0, 0, 1920, 1040),
            is_primary=True,
        )
        vd = VirtualDesktop([m_pri, m_above])
        assert vd.virtual_rect == (0, -1080, 1920, 2160)
        assert vd.get_monitor_for_point(500, -500) == m_above
        assert vd.get_monitor_for_point(500, 500) == m_pri

    def test_get_monitor_for_rect_largest_intersection(self):
        m1 = MonitorInfo(
            index=0,
            hmonitor=1,
            rect=(0, 0, 1920, 1080),
            work_area=(0, 0, 1920, 1040),
            is_primary=True,
        )
        m2 = MonitorInfo(
            index=1,
            hmonitor=2,
            rect=(1920, 0, 3840, 1080),
            work_area=(1920, 0, 3840, 1080),
            is_primary=False,
        )
        vd = VirtualDesktop([m1, m2])

        # Window mostly on monitor 2 (1800 to 2200, width 400: 120px on m1, 280px on m2)
        win_rect = (1800, 100, 400, 300)
        assert vd.get_monitor_for_rect(win_rect) == m2

        # Rect completely on m1
        assert vd.get_monitor_for_rect((100, 100, 500, 400)) == m1

    def test_get_next_monitor_round_robin(self):
        m1 = MonitorInfo(index=0, hmonitor=1, rect=(0, 0, 1920, 1080), work_area=(0, 0, 1920, 1040))
        m2 = MonitorInfo(
            index=1, hmonitor=2, rect=(1920, 0, 3840, 1080), work_area=(1920, 0, 3840, 1080)
        )
        vd = VirtualDesktop([m1, m2])
        assert vd.get_next_monitor(0) == m2
        assert vd.get_next_monitor(1) == m1

    def test_enumerate_monitors_fallback(self):
        # Fallback when enumeration returns empty
        vd_empty = enumerate_monitors(enum_fn=lambda: [])
        assert vd_empty.monitor_count == 1
        assert vd_empty.primary_monitor.is_primary

        # Fallback when enumeration raises exception
        def _failing_enum():
            raise RuntimeError("OS monitor query failed")

        vd_err = enumerate_monitors(enum_fn=_failing_enum)
        assert vd_err.monitor_count == 1
        assert vd_err.primary_monitor.is_primary


# ---------------------------------------------------------------------------
# 2. WindowManager Tests
# ---------------------------------------------------------------------------


class TestWindowManager:
    def test_is_eligible_filter(self):
        wm = WindowManager()

        # Valid normal window
        valid = WindowInfo(
            hwnd=1,
            title="Google Chrome",
            class_name="Chrome_WidgetWin_1",
            rect=(100, 100, 800, 600),
        )
        assert wm._is_eligible(valid)

        # Empty or whitespace title
        no_title = WindowInfo(hwnd=2, title="", class_name="App", rect=(100, 100, 800, 600))
        assert not wm._is_eligible(no_title)
        ws_title = WindowInfo(hwnd=3, title="   ", class_name="App", rect=(100, 100, 800, 600))
        assert not wm._is_eligible(ws_title)

        # Too small dimensions (e.g. hidden helper window)
        tiny = WindowInfo(hwnd=4, title="Helper", class_name="App", rect=(0, 0, 5, 5))
        assert not wm._is_eligible(tiny)

        # Shell internals
        progman = WindowInfo(
            hwnd=5, title="Program Manager", class_name="Progman", rect=(0, 0, 1920, 1080)
        )
        assert not wm._is_eligible(progman)
        tray = WindowInfo(
            hwnd=6, title="Taskbar", class_name="Shell_TrayWnd", rect=(0, 1040, 1920, 40)
        )
        assert not wm._is_eligible(tray)

    def test_move_window_to_monitor_centering_and_fitting(self):
        w = WindowInfo(hwnd=10, title="Doc", rect=(100, 100, 800, 600), state=WindowState.NORMAL)
        wm = MockWindowManager([w])

        target_mon = MonitorInfo(
            index=1,
            hmonitor=2,
            rect=(1920, 0, 3840, 1080),
            work_area=(1920, 0, 3840, 1040),  # width: 1920, height: 1040
        )
        ok = wm.move_window_to_monitor(10, target_mon)
        assert ok
        updated = wm._inspect_window(10)
        assert updated is not None
        # Center: x = 1920 + (1920 - 800)//2 = 1920 + 560 = 2480
        # Center: y = 0 + (1040 - 600)//2 = 220
        assert updated.x == 2480
        assert updated.y == 220
        assert updated.width == 800
        assert updated.height == 600

    def test_move_window_restores_maximized_first(self):
        w = WindowInfo(hwnd=10, title="Doc", rect=(0, 0, 1920, 1080), state=WindowState.MAXIMIZED)
        wm = MockWindowManager([w])

        target_mon = MonitorInfo(
            index=1,
            hmonitor=2,
            rect=(1920, 0, 3840, 1080),
            work_area=(1920, 0, 3840, 1040),
        )
        ok = wm.move_window_to_monitor(10, target_mon)
        assert ok
        # Must have restored state to normal
        assert (10, "normal") in wm.state_changes

    def test_toggle_maximize(self):
        w_normal = WindowInfo(hwnd=1, title="Normal Window", state=WindowState.NORMAL)
        wm = MockWindowManager([w_normal])

        assert wm.toggle_maximize(1)
        assert wm._inspect_window(1).state == WindowState.MAXIMIZED

        assert wm.toggle_maximize(1)
        assert wm._inspect_window(1).state == WindowState.NORMAL


# ---------------------------------------------------------------------------
# 3. WindowDragSession Tests
# ---------------------------------------------------------------------------


class TestWindowDragSession:
    def test_zero_initial_jump_calculation(self):
        w = WindowInfo(hwnd=10, title="Editor", rect=(200, 150, 800, 600))
        wm = MockWindowManager([w])
        session = WindowDragSession(window_manager=wm)

        # Hand grabs at (350, 400) at t=1.0
        ok, msg, info = session.start_grab(
            hand_x=350.0, hand_y=400.0, target_hwnd=10, timestamp=1.0
        )
        assert ok
        assert info.hwnd == 10
        assert session.is_active

        # Initial relative offset is (200 - 350 = -150, 150 - 400 = -250)
        assert session._offset_x == -150.0
        assert session._offset_y == -250.0

        # Hand moves by +10px in X and +15px in Y -> (360, 415) at t=1.033 (30fps)
        # New window position must be (360 - 150 = 210, 415 - 250 = 165)
        # That is exactly +10px X and +15px Y displacement with NO TELEPORT JUMP!
        updated = session.update_hand_position(hand_x=360.0, hand_y=415.0, timestamp=1.033)
        assert updated
        assert wm._inspect_window(10).x == 210
        assert wm._inspect_window(10).y == 165

    def test_drag_rate_limiting_60hz_coalescence(self):
        w = WindowInfo(hwnd=10, title="Editor", rect=(100, 100, 800, 600))
        wm = MockWindowManager([w])
        session = WindowDragSession(window_manager=wm)

        session.start_grab(hand_x=100.0, hand_y=100.0, target_hwnd=10, timestamp=1.000)

        # First update at t = 1.020 (20ms later -> 50 Hz, valid)
        upd1 = session.update_hand_position(110.0, 110.0, timestamp=1.020)
        assert upd1

        # Second update at t = 1.028 (8ms after upd1 -> ~125 Hz, exceeds 60 Hz limit)
        upd2 = session.update_hand_position(115.0, 115.0, timestamp=1.028)
        assert not upd2  # Dropped / coalesced!

        # Third update at t = 1.040 (20ms after upd1 -> within 60 Hz limit)
        upd3 = session.update_hand_position(125.0, 125.0, timestamp=1.040)
        assert upd3
        assert wm._inspect_window(10).x == 125
        assert wm._inspect_window(10).y == 125

    def test_redundant_movement_suppression(self):
        w = WindowInfo(hwnd=10, title="Editor", rect=(100, 100, 800, 600))
        wm = MockWindowManager([w])
        session = WindowDragSession(window_manager=wm)

        session.start_grab(100.0, 100.0, target_hwnd=10, timestamp=1.0)
        session.update_hand_position(110.0, 110.0, timestamp=1.025)
        calls_before = len(wm.moved_calls)

        # Update at later timestamp but SAME hand position
        upd = session.update_hand_position(110.0, 110.0, timestamp=1.050)
        assert not upd
        assert len(wm.moved_calls) == calls_before

    def test_elevated_window_protection(self):
        w_elevated = WindowInfo(
            hwnd=99,
            title="Administrator: PowerShell",
            rect=(100, 100, 800, 600),
            is_elevated=True,
        )
        wm = MockWindowManager([w_elevated])
        session = WindowDragSession(window_manager=wm)

        ok, msg, info = session.start_grab(100.0, 100.0, target_hwnd=99)
        assert not ok
        assert "elevado" in msg or "Administrador" in msg
        assert not session.is_active

    def test_auto_restore_maximized_window_on_grab(self):
        w_max = WindowInfo(
            hwnd=10, title="Editor", rect=(0, 0, 1920, 1080), state=WindowState.MAXIMIZED
        )
        wm = MockWindowManager([w_max])
        session = WindowDragSession(window_manager=wm)

        ok, _, info = session.start_grab(500.0, 500.0, target_hwnd=10)
        assert ok
        assert session.is_active
        # Window was restored before dragging
        assert (10, "normal") in wm.state_changes

    def test_end_and_cancel_grab(self):
        w = WindowInfo(hwnd=10, title="Editor", rect=(100, 100, 800, 600))
        wm = MockWindowManager([w])
        session = WindowDragSession(window_manager=wm)

        # Normal end_grab
        session.start_grab(100.0, 100.0, target_hwnd=10)
        assert session.is_active
        session.end_grab()
        assert not session.is_active
        assert session.grabbed_window is None

        # cancel_grab (hand lost / pause)
        session.start_grab(100.0, 100.0, target_hwnd=10)
        assert session.is_active
        session.cancel_grab()
        assert not session.is_active
        assert session.grabbed_window is None

    def test_edge_snap_left_half(self):
        w = WindowInfo(hwnd=10, title="Editor", rect=(200, 200, 800, 600))
        wm = MockWindowManager([w])
        m = MonitorInfo(
            index=0,
            hmonitor=1,
            rect=(0, 0, 1920, 1080),
            work_area=(0, 0, 1920, 1040),
            is_primary=True,
        )
        desktop = VirtualDesktop([m])
        session = WindowDragSession(window_manager=wm, virtual_desktop=desktop)

        session.start_grab(hand_x=500.0, hand_y=500.0, target_hwnd=10, timestamp=1.0)
        session.update_hand_position(hand_x=50.0, hand_y=500.0, timestamp=1.03)

        action = session.end_grab()
        assert action == "snap_left"
        assert not session.is_active
        # Window rect is left half of work area (0, 0, 960, 1040)
        snapped = wm._inspect_window(10)
        assert snapped.rect == (0, 0, 960, 1040)

    def test_edge_snap_right_half(self):
        w = WindowInfo(hwnd=10, title="Editor", rect=(200, 200, 800, 600))
        wm = MockWindowManager([w])
        m = MonitorInfo(
            index=0,
            hmonitor=1,
            rect=(0, 0, 1920, 1080),
            work_area=(0, 0, 1920, 1040),
            is_primary=True,
        )
        desktop = VirtualDesktop([m])
        session = WindowDragSession(window_manager=wm, virtual_desktop=desktop)

        session.start_grab(hand_x=500.0, hand_y=500.0, target_hwnd=10, timestamp=1.0)
        session.update_hand_position(hand_x=1880.0, hand_y=500.0, timestamp=1.03)

        action = session.end_grab()
        assert action == "snap_right"
        assert not session.is_active
        # Window rect is right half of work area (960, 0, 960, 1040)
        snapped = wm._inspect_window(10)
        assert snapped.rect == (960, 0, 960, 1040)

    def test_edge_snap_top_maximize(self):
        w = WindowInfo(hwnd=10, title="Editor", rect=(200, 200, 800, 600))
        wm = MockWindowManager([w])
        m = MonitorInfo(
            index=0,
            hmonitor=1,
            rect=(0, 0, 1920, 1080),
            work_area=(0, 0, 1920, 1040),
            is_primary=True,
        )
        desktop = VirtualDesktop([m])
        session = WindowDragSession(window_manager=wm, virtual_desktop=desktop)

        session.start_grab(hand_x=500.0, hand_y=500.0, target_hwnd=10, timestamp=1.0)
        session.update_hand_position(hand_x=960.0, hand_y=30.0, timestamp=1.03)

        action = session.end_grab()
        assert action == "maximized"
        assert not session.is_active
        assert (10, "maximized") in wm.state_changes
        assert wm._inspect_window(10).state == WindowState.MAXIMIZED

    def test_edge_snap_coordinates_passed_to_end_grab(self):
        w = WindowInfo(hwnd=10, title="Editor", rect=(200, 200, 800, 600))
        wm = MockWindowManager([w])
        m = MonitorInfo(
            index=0,
            hmonitor=1,
            rect=(0, 0, 1920, 1080),
            work_area=(0, 0, 1920, 1040),
            is_primary=True,
        )
        desktop = VirtualDesktop([m])
        session = WindowDragSession(window_manager=wm, virtual_desktop=desktop)

        session.start_grab(hand_x=500.0, hand_y=500.0, target_hwnd=10, timestamp=1.0)
        session.update_hand_position(hand_x=520.0, hand_y=520.0, timestamp=1.03)

        # Release with hand at left edge coordinates
        action = session.end_grab(hand_x=40.0, hand_y=500.0)
        assert action == "snap_left"
        snapped = wm._inspect_window(10)
        assert snapped.rect == (0, 0, 960, 1040)

    def test_no_snap_when_released_in_center(self):
        w = WindowInfo(hwnd=10, title="Editor", rect=(200, 200, 800, 600))
        wm = MockWindowManager([w])
        m = MonitorInfo(
            index=0,
            hmonitor=1,
            rect=(0, 0, 1920, 1080),
            work_area=(0, 0, 1920, 1040),
            is_primary=True,
        )
        desktop = VirtualDesktop([m])
        session = WindowDragSession(window_manager=wm, virtual_desktop=desktop)

        session.start_grab(hand_x=500.0, hand_y=500.0, target_hwnd=10, timestamp=1.0)
        session.update_hand_position(hand_x=600.0, hand_y=600.0, timestamp=1.03)

        action = session.end_grab()
        assert action is None
        assert not session.is_active
        # Window moved to (300, 300) without resizing
        snapped = wm._inspect_window(10)
        assert snapped.rect == (300, 300, 800, 600)

    def test_multi_monitor_edge_snap(self):
        w = WindowInfo(hwnd=10, title="Editor", rect=(200, 200, 800, 600))
        wm = MockWindowManager([w])
        m1 = MonitorInfo(
            index=0,
            hmonitor=1,
            rect=(0, 0, 1920, 1080),
            work_area=(0, 0, 1920, 1040),
            is_primary=True,
        )
        m2 = MonitorInfo(
            index=1,
            hmonitor=2,
            rect=(1920, 0, 3840, 1080),
            work_area=(1920, 0, 3840, 1080),
            is_primary=False,
        )
        desktop = VirtualDesktop([m1, m2])
        session = WindowDragSession(window_manager=wm, virtual_desktop=desktop)

        # Drag hand into left edge of Monitor 2
        session.start_grab(hand_x=2200.0, hand_y=500.0, target_hwnd=10, timestamp=1.0)
        session.update_hand_position(hand_x=1950.0, hand_y=500.0, timestamp=1.03)

        action = session.end_grab()
        assert action == "snap_left"
        # Left half of monitor 2: left=1920, top=0, width=960, height=1080
        snapped = wm._inspect_window(10)
        assert snapped.rect == (1920, 0, 960, 1080)

    def test_window_drag_sensitivity_amplification(self):
        w = WindowInfo(hwnd=10, title="Editor", rect=(200, 200, 800, 600))
        wm = MockWindowManager([w])
        session = WindowDragSession(window_manager=wm, drag_sensitivity=1.6)

        session.start_grab(hand_x=500.0, hand_y=500.0, target_hwnd=10, timestamp=1.0)
        # Move hand by +100 in X and +50 in Y
        session.update_hand_position(hand_x=600.0, hand_y=550.0, timestamp=1.03)

        # Window displacement must be amplified by 1.6x:
        # dx = 100 * 1.6 = 160 -> x = 200 + 160 = 360
        # dy = 50 * 1.6 = 80 -> y = 200 + 80 = 280
        moved = wm._inspect_window(10)
        assert moved.x == 360
        assert moved.y == 280


# ---------------------------------------------------------------------------
# 4. WindowExecutor Tests
# ---------------------------------------------------------------------------


class TestWindowExecutor:
    @pytest.fixture
    def test_windows(self) -> list[WindowInfo]:
        return [
            WindowInfo(hwnd=10, title="Editor Code", rect=(100, 100, 800, 600)),
            WindowInfo(hwnd=20, title="Admin Cmd", rect=(200, 200, 800, 600), is_elevated=True),
        ]

    @pytest.fixture
    def mock_wm(self, test_windows: list[WindowInfo]) -> MockWindowManager:
        return MockWindowManager(test_windows)

    @pytest.fixture
    def desktop(self) -> VirtualDesktop:
        m1 = MonitorInfo(
            index=0,
            hmonitor=1,
            rect=(0, 0, 1920, 1080),
            work_area=(0, 0, 1920, 1040),
            is_primary=True,
        )
        m2 = MonitorInfo(
            index=1, hmonitor=2, rect=(1920, 0, 3840, 1080), work_area=(1920, 0, 3840, 1080)
        )
        return VirtualDesktop([m1, m2])

    @pytest.fixture
    def executor(self, mock_wm: MockWindowManager, desktop: VirtualDesktop) -> WindowExecutor:
        return WindowExecutor(window_manager=mock_wm, virtual_desktop=desktop)

    def test_window_grab_and_release(self, executor: WindowExecutor):
        req_grab = ActionRequest(
            action_name="window_grab",
            parameters={"x": 150.0, "y": 150.0, "hwnd": 10},
        )
        res = executor.execute(req_grab)
        assert res.status == ActionStatus.EXECUTED
        assert executor.drag.is_active

        req_rel = ActionRequest(action_name="window_release")
        res_rel = executor.execute(req_rel)
        assert res_rel.status == ActionStatus.EXECUTED
        assert not executor.drag.is_active

    def test_window_grab_elevated_refused(self, executor: WindowExecutor):
        req = ActionRequest(
            action_name="window_grab",
            parameters={"x": 250.0, "y": 250.0, "hwnd": 20},
        )
        res = executor.execute(req)
        assert res.status == ActionStatus.REFUSED
        assert "elevado" in res.reason

    def test_window_minimize_maximize_restore(
        self, executor: WindowExecutor, mock_wm: MockWindowManager
    ):
        # Minimize
        res_min = executor.execute(
            ActionRequest(action_name="window_minimize", parameters={"hwnd": 10})
        )
        assert res_min.status == ActionStatus.EXECUTED
        assert mock_wm._inspect_window(10).state == WindowState.MINIMIZED

        # Maximize
        res_max = executor.execute(
            ActionRequest(action_name="window_maximize", parameters={"hwnd": 10})
        )
        assert res_max.status == ActionStatus.EXECUTED
        assert mock_wm._inspect_window(10).state == WindowState.MAXIMIZED

        # Restore
        res_rest = executor.execute(
            ActionRequest(action_name="window_restore", parameters={"hwnd": 10})
        )
        assert res_rest.status == ActionStatus.EXECUTED
        assert mock_wm._inspect_window(10).state == WindowState.NORMAL

    def test_window_toggle_maximize(self, executor: WindowExecutor, mock_wm: MockWindowManager):
        res1 = executor.execute(
            ActionRequest(action_name="window_toggle_maximize", parameters={"hwnd": 10})
        )
        assert res1.status == ActionStatus.EXECUTED
        assert mock_wm._inspect_window(10).state == WindowState.MAXIMIZED

        res2 = executor.execute(
            ActionRequest(action_name="window_toggle_maximize", parameters={"hwnd": 10})
        )
        assert res2.status == ActionStatus.EXECUTED
        assert mock_wm._inspect_window(10).state == WindowState.NORMAL

    def test_window_move_to_monitor(self, executor: WindowExecutor, mock_wm: MockWindowManager):
        res = executor.execute(
            ActionRequest(action_name="window_move_to_monitor", parameters={"hwnd": 10})
        )
        assert res.status == ActionStatus.EXECUTED
        assert "Moved to monitor 1" in res.reason
        # Window moved to monitor 1 area (x >= 1920)
        assert mock_wm._inspect_window(10).x >= 1920

    def test_window_unknown_action(self, executor: WindowExecutor):
        res = executor.execute(ActionRequest(action_name="window_explode"))
        assert res.status == ActionStatus.REFUSED
        assert "unknown action" in res.reason

    def test_release_all_cancels_drag(self, executor: WindowExecutor):
        executor.execute(
            ActionRequest(action_name="window_grab", parameters={"x": 100, "y": 100, "hwnd": 10})
        )
        assert executor.drag.is_active

        executor.release_all()
        assert not executor.drag.is_active


# ---------------------------------------------------------------------------
# 5. CompositeExecutor Tests
# ---------------------------------------------------------------------------


class TestCompositeExecutorRouting:
    def test_routes_to_proper_subexecutor(self):
        mock_wm = MockWindowManager([WindowInfo(hwnd=10, title="App", rect=(0, 0, 800, 600))])
        win_exec = WindowExecutor(window_manager=mock_wm)

        mouse_calls = []
        kbd_calls = []

        class MockMouse(MouseExecutor):
            def execute(self, req: ActionRequest) -> ActionResult:
                mouse_calls.append(req.action_name)
                return ActionResult(action_name=req.action_name, status=ActionStatus.EXECUTED)

        class MockKbd(KeyboardExecutor):
            def execute(self, req: ActionRequest) -> ActionResult:
                kbd_calls.append(req.action_name)
                return ActionResult(action_name=req.action_name, status=ActionStatus.EXECUTED)

        comp = CompositeExecutor(
            mouse_executor=MockMouse(),
            keyboard_executor=MockKbd(),
            window_executor=win_exec,
        )

        # Mouse action
        r_mouse = comp.execute(ActionRequest(action_name="click_left"))
        assert r_mouse.status == ActionStatus.EXECUTED
        assert "click_left" in mouse_calls

        # Keyboard action
        r_kbd = comp.execute(ActionRequest(action_name="shortcut", parameters={"keys": "ctrl+c"}))
        assert r_kbd.status == ActionStatus.EXECUTED
        assert "shortcut" in kbd_calls

        # Window action
        r_win = comp.execute(ActionRequest(action_name="window_minimize", parameters={"hwnd": 10}))
        assert r_win.status == ActionStatus.EXECUTED
        assert mock_wm._inspect_window(10).state == WindowState.MINIMIZED

    def test_release_all_cascades_to_all(self):
        released = {"mouse": False, "kbd": False, "win": False}

        class MockM(MouseExecutor):
            def release_all(self):
                released["mouse"] = True

        class MockK(KeyboardExecutor):
            def release_all(self):
                released["kbd"] = True

        class MockW(WindowExecutor):
            def release_all(self):
                released["win"] = True

        comp = CompositeExecutor(
            mouse_executor=MockM(),
            keyboard_executor=MockK(),
            window_executor=MockW(),
        )
        comp.release_all()
        assert released["mouse"]
        assert released["kbd"]
        assert released["win"]


# ---------------------------------------------------------------------------
# 6. Allowlist Tests
# ---------------------------------------------------------------------------


class TestAllowlistWindowActions:
    def test_window_actions_registered_and_validated(self):
        allowlist = Allowlist()
        for act in (
            "window_grab",
            "window_release",
            "window_minimize",
            "window_maximize",
            "window_restore",
            "window_toggle_maximize",
            "window_move_to_monitor",
        ):
            assert allowlist.is_allowed(act), f"Action '{act}' should be registered in Allowlist"

    def test_validate_window_parameters(self):
        allowlist = Allowlist()

        # Valid grab with optional coordinates and hwnd
        errs = allowlist.validate("window_grab", {"x": 500.0, "y": 300.0, "hwnd": 1024})
        assert not errs

        # Valid grab without parameters (resolves active window)
        errs = allowlist.validate("window_grab", {})
        assert not errs

        # Unknown parameter
        errs = allowlist.validate("window_grab", {"unknown_param": 123})
        assert len(errs) > 0
        assert "Unknown parameter" in errs[0]


# ---------------------------------------------------------------------------
# 7. Phase 6 End-to-End Tests
# ---------------------------------------------------------------------------


class TestPhase6Integration:
    def test_full_pipeline_simulation_mode(self):
        mock_wm = MockWindowManager(
            [WindowInfo(hwnd=10, title="Editor", rect=(100, 100, 800, 600))]
        )
        win_exec = WindowExecutor(window_manager=mock_wm)
        comp = CompositeExecutor(window_executor=win_exec)

        controller = ActionController(executor=comp)
        mapper = GestureMapper()
        safety = SafetyManager(controller=controller, mapper=mapper)
        safety.enable_simulation_mode()

        req = ActionRequest(action_name="window_minimize", parameters={"hwnd": 10})
        res = controller.submit(req)

        assert res.status == ActionStatus.SIMULATED
        # Window must not have actually changed in simulation mode
        assert mock_wm._inspect_window(10).state == WindowState.NORMAL

    def test_full_pipeline_live_execution_and_emergency_stop(self):
        mock_wm = MockWindowManager(
            [WindowInfo(hwnd=10, title="Editor", rect=(100, 100, 800, 600))]
        )
        win_exec = WindowExecutor(window_manager=mock_wm)
        comp = CompositeExecutor(window_executor=win_exec)

        controller = ActionController(executor=comp)
        mapper = GestureMapper()
        safety = SafetyManager(controller=controller, mapper=mapper)
        safety.enable_real_mode(comp)

        # 1. Grab window in live mode
        res_grab = controller.submit(
            ActionRequest(action_name="window_grab", parameters={"x": 100, "y": 100, "hwnd": 10})
        )
        assert res_grab.status == ActionStatus.EXECUTED
        assert win_exec.drag.is_active

        # 2. Trigger emergency stop
        safety.emergency_stop()
        assert safety.is_emergency
        # Emergency stop must call release_all on controller, which cascades to win_exec.drag
        assert not win_exec.drag.is_active

        # 3. Subsequent actions blocked during emergency stop
        res_blocked = controller.submit(
            ActionRequest(action_name="window_maximize", parameters={"hwnd": 10})
        )
        assert res_blocked.status == ActionStatus.REFUSED
        assert "Emergency stop" in res_blocked.reason
