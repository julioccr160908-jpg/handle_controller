"""Window dragging session with relative offset, 60 Hz coalescence, and edge snapping (Section 8.2).

Flow:
1. Select window (foreground or under cursor).
2. Grab gesture: record initial window rect and initial hand position.
3. Compute relative offset (win_pos - hand_pos) so there is ZERO initial jump.
4. Move window along with hand displacement, coalesced at ≤ 60 Hz.
5. Release gesture (hand open / fist release):
   - Left edge: snap window to left half of screen.
   - Right edge: snap window to right half of screen.
   - Top center: maximize window to full screen.
   - Other areas: leave window at current drop position.
6. Hand loss / cancellation: leaves window at current position without edge snap.
7. Guard: detects elevated processes and refuses grab with a descriptive warning.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import time

from handcontrol.windows.manager import WindowManager
from handcontrol.windows.models import WindowInfo
from handcontrol.windows.monitors import VirtualDesktop, enumerate_monitors

logger = logging.getLogger(__name__)

# Max update frequency: 60 Hz (Section 8.2)
_MAX_DRAG_HZ = 60.0
_MIN_DRAG_INTERVAL = 1.0 / _MAX_DRAG_HZ


class WindowDragSession:
    """Manages an active relative window dragging interaction with edge snapping."""

    def __init__(
        self,
        window_manager: WindowManager | None = None,
        virtual_desktop: VirtualDesktop | None = None,
    ) -> None:
        self._wm = window_manager or WindowManager()
        self._desktop = virtual_desktop or enumerate_monitors()
        self._active = False
        self._hwnd: int = 0
        self._offset_x: float = 0.0
        self._offset_y: float = 0.0
        self._initial_hand_x: float = 0.0
        self._initial_hand_y: float = 0.0
        self._last_hand_x: float = 0.0
        self._last_hand_y: float = 0.0
        self._last_win_x: int = 0
        self._last_win_y: int = 0
        self._win_width: int = 800
        self._win_height: int = 600
        self._has_moved: bool = False
        self._last_update_time: float = 0.0
        self._target_info: WindowInfo | None = None

    @property
    def is_active(self) -> bool:
        """Whether a window is currently grabbed and being moved."""
        return self._active

    @property
    def grabbed_window(self) -> WindowInfo | None:
        """Information about the currently grabbed window."""
        return self._target_info

    def start_grab(
        self,
        hand_x: float,
        hand_y: float,
        target_hwnd: int | None = None,
        timestamp: float | None = None,
    ) -> tuple[bool, str, WindowInfo | None]:
        """Begin grabbing a window for relative dragging.

        Args:
            hand_x: Current screen X coordinate of the grabbing hand.
            hand_y: Current screen Y coordinate of the grabbing hand.
            target_hwnd: Optional explicit window handle to grab.
            timestamp: Monotonic timestamp of grab start.

        Returns:
            (success, message, WindowInfo | None).
        """
        # Resolve target window
        info: WindowInfo | None = None
        if target_hwnd:
            info = self._wm._inspect_window(target_hwnd)
        if not info:
            info = self._wm.get_window_under_cursor(hand_x, hand_y)
        if not info:
            info = self._wm.get_foreground_window()
        if not info:
            eligible = self._wm.list_eligible_windows()
            if eligible:
                info = eligible[0]

        if not info:
            return False, "Nenhuma janela elegível encontrada para mover.", None

        # Elevation check (Section 8.5)
        if info.is_elevated:
            msg = (
                f"Janela '{info.title[:30]}' pertence a um processo elevado "
                "(Administrador) e não pode ser manipulada sem privilégios."
            )
            logger.warning("Window grab refused: %s", msg)
            return False, msg, info

        # If maximized, restore before dragging so it can move freely
        if info.is_maximized:
            self._wm.restore_window(info.hwnd)
            if sys.platform == "win32":
                time.sleep(0.04)
            info = self._wm._inspect_window(info.hwnd) or info

        # Calculate relative offset so there is ZERO initial jump
        self._hwnd = info.hwnd
        self._target_info = info
        self._offset_x = float(info.x) - hand_x
        self._offset_y = float(info.y) - hand_y
        self._initial_hand_x = hand_x
        self._initial_hand_y = hand_y
        self._last_hand_x = hand_x
        self._last_hand_y = hand_y
        self._last_win_x = info.x
        self._last_win_y = info.y
        self._win_width = info.rect[2] if len(info.rect) > 2 and info.rect[2] > 0 else 800
        self._win_height = info.rect[3] if len(info.rect) > 3 and info.rect[3] > 0 else 600
        self._has_moved = False
        self._last_update_time = timestamp if timestamp is not None else time.monotonic()
        self._active = True

        logger.info(
            "Window grabbed: '%s' (hwnd=%d) at (%d, %d), offset=(%.1f, %.1f)",
            info.title[:25],
            info.hwnd,
            info.x,
            info.y,
            self._offset_x,
            self._offset_y,
        )
        return True, "", info

    def record_hand_position(self, hand_x: float, hand_y: float) -> None:
        """Record current hand position without triggering a window move."""
        self._last_hand_x = hand_x
        self._last_hand_y = hand_y

    def update_hand_position(
        self,
        hand_x: float,
        hand_y: float,
        timestamp: float | None = None,
    ) -> bool:
        """Update window position based on hand displacement (coalesced at ≤ 60 Hz).

        Args:
            hand_x: New screen X coordinate of the grabbing hand.
            hand_y: New screen Y coordinate of the grabbing hand.
            timestamp: Monotonic frame timestamp.

        Returns:
            True if position was updated, False if throttled by 60 Hz rate limit.
        """
        if not self._active:
            return False

        now = timestamp if timestamp is not None else time.monotonic()
        if (now - self._last_update_time) < _MIN_DRAG_INTERVAL:
            # Coalesce: drop intermediate frame if updates arrive faster than 60 Hz
            return False

        # Calculate target window position preserving initial grab offset
        target_x = int(hand_x + self._offset_x)
        target_y = int(hand_y + self._offset_y)

        # Skip redundant OS calls if position has not changed
        if target_x == self._last_win_x and target_y == self._last_win_y:
            self._last_update_time = now
            self._last_hand_x = hand_x
            self._last_hand_y = hand_y
            return False

        # Ensure window is restored if still zoomed on Windows
        if (
            sys.platform == "win32"
            and ctypes.windll.user32.IsWindow(self._hwnd)
            and ctypes.windll.user32.IsZoomed(self._hwnd)
        ):
            self._wm.restore_window(self._hwnd)

        ok = self._wm.set_window_position(self._hwnd, target_x, target_y)
        if ok:
            self._last_win_x = target_x
            self._last_win_y = target_y
            self._last_hand_x = hand_x
            self._last_hand_y = hand_y
            self._has_moved = True
            self._last_update_time = now
            logger.debug("Window %d moved to (%d, %d)", self._hwnd, target_x, target_y)

        return ok

    def end_grab(
        self,
        hand_x: float | None = None,
        hand_y: float | None = None,
    ) -> str | None:
        """Release the grabbed window, performing edge snap if near display edges.

        Snapping rules:
        - Left edge: snap window to left half of monitor work area.
        - Right edge: snap window to right half of monitor work area.
        - Top center: maximize window to full screen.
        - Other areas: retain current position without resizing.

        Returns:
            Snap action name ('snap_left', 'snap_right', 'maximized', or None).
        """
        if not self._active:
            return None

        if hand_x is not None:
            self._last_hand_x = hand_x
        if hand_y is not None:
            self._last_hand_y = hand_y

        action: str | None = None
        hwnd = self._hwnd

        try:
            has_dragged = (
                self._has_moved
                or abs(self._last_hand_x - self._initial_hand_x) > 20
                or abs(self._last_hand_y - self._initial_hand_y) > 20
            )

            if has_dragged:
                # Identify monitor where hand/window is released
                monitor = self._desktop.get_monitor_for_point(self._last_hand_x, self._last_hand_y)
                if not monitor:
                    monitor = self._desktop.get_monitor_for_rect(
                        (self._last_win_x, self._last_win_y, self._win_width, self._win_height)
                    )
                if not monitor:
                    monitor = self._desktop.primary_monitor

                if monitor:
                    mw_left, mw_top, mw_right, mw_bottom = monitor.work_area
                    mw_width = mw_right - mw_left
                    mw_height = mw_bottom - mw_top

                    snap_margin_x = max(120, int(mw_width * 0.10))
                    snap_margin_y = max(80, int(mw_height * 0.08))

                    is_left_snap = (
                        self._last_hand_x <= mw_left + snap_margin_x
                        or (
                            self._last_win_x <= mw_left + 30
                            and self._last_hand_x <= mw_left + int(mw_width * 0.35)
                        )
                    )

                    is_right_snap = (
                        self._last_hand_x >= mw_right - snap_margin_x
                        or (
                            (self._last_win_x + self._win_width) >= mw_right - 30
                            and self._last_hand_x >= mw_right - int(mw_width * 0.35)
                        )
                    )

                    is_top_hand = self._last_hand_y <= mw_top + snap_margin_y
                    is_top_win = (
                        self._last_win_y <= mw_top + 30
                        and self._last_hand_y <= mw_top + int(mw_height * 0.35)
                    )
                    is_in_top = is_top_hand or is_top_win
                    is_center_x = (
                        (mw_left + snap_margin_x) < self._last_hand_x < (mw_right - snap_margin_x)
                    )
                    is_top_maximize = is_in_top and is_center_x

                    if is_left_snap:
                        snap_w = mw_width // 2
                        if sys.platform == "win32" and ctypes.windll.user32.IsZoomed(hwnd):
                            self._wm.restore_window(hwnd)
                        self._wm.set_window_position(hwnd, mw_left, mw_top, snap_w, mw_height)
                        self._last_win_x = mw_left
                        self._last_win_y = mw_top
                        action = "snap_left"
                        logger.info(
                            "Window %d snapped to LEFT half: (%d, %d, %d, %d)",
                            hwnd,
                            mw_left,
                            mw_top,
                            snap_w,
                            mw_height,
                        )
                    elif is_right_snap:
                        snap_w = mw_width // 2
                        snap_x = mw_left + (mw_width - snap_w)
                        if sys.platform == "win32" and ctypes.windll.user32.IsZoomed(hwnd):
                            self._wm.restore_window(hwnd)
                        self._wm.set_window_position(hwnd, snap_x, mw_top, snap_w, mw_height)
                        self._last_win_x = snap_x
                        self._last_win_y = mw_top
                        action = "snap_right"
                        logger.info(
                            "Window %d snapped to RIGHT half: (%d, %d, %d, %d)",
                            hwnd,
                            snap_x,
                            mw_top,
                            snap_w,
                            mw_height,
                        )
                    elif is_top_maximize:
                        self._wm.maximize_window(hwnd)
                        action = "maximized"
                        logger.info("Window %d snapped to MAXIMIZED (full screen)", hwnd)
                    else:
                        logger.info(
                            "Window %d released at (%d, %d)",
                            hwnd,
                            self._last_win_x,
                            self._last_win_y,
                        )
            else:
                logger.info(
                    "Window %d released without movement at (%d, %d)",
                    hwnd,
                    self._last_win_x,
                    self._last_win_y,
                )
        except Exception:
            logger.exception("Error during window release snapping")
        finally:
            self._active = False
            self._target_info = None

        return action

    def cancel_grab(self) -> None:
        """Cancel drag (e.g. on hand loss or pause), leaving window at its current position."""
        if self._active:
            logger.info("Window grab cancelled — position retained")
        self._active = False
        self._target_info = None
