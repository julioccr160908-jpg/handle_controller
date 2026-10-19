"""Window dragging session with relative offset and 60 Hz coalescence (Section 8.2).

Flow:
1. Select window (foreground or under cursor).
2. Grab gesture: record initial window rect and initial hand position.
3. Compute relative offset (win_pos - hand_pos) so there is ZERO initial jump.
4. Move window along with hand displacement, coalesced at ≤ 60 Hz.
5. Release / cancel on hand loss or pause: leaves window at current position.
6. Guard: detects elevated processes and refuses grab with a descriptive warning.
"""

from __future__ import annotations

import logging
import time

from handcontrol.windows.manager import WindowManager
from handcontrol.windows.models import WindowInfo

logger = logging.getLogger(__name__)

# Max update frequency: 60 Hz (Section 8.2)
_MAX_DRAG_HZ = 60.0
_MIN_DRAG_INTERVAL = 1.0 / _MAX_DRAG_HZ


class WindowDragSession:
    """Manages an active relative window dragging interaction."""

    def __init__(self, window_manager: WindowManager | None = None) -> None:
        self._wm = window_manager or WindowManager()
        self._active = False
        self._hwnd: int = 0
        self._offset_x: float = 0.0
        self._offset_y: float = 0.0
        self._last_win_x: int = 0
        self._last_win_y: int = 0
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
            info = self._wm._inspect_window(info.hwnd) or info

        # Calculate relative offset so there is ZERO initial jump
        self._hwnd = info.hwnd
        self._target_info = info
        self._offset_x = float(info.x) - hand_x
        self._offset_y = float(info.y) - hand_y
        self._last_win_x = info.x
        self._last_win_y = info.y
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
            return False

        ok = self._wm.set_window_position(self._hwnd, target_x, target_y)
        if ok:
            self._last_win_x = target_x
            self._last_win_y = target_y
            self._last_update_time = now

        return ok

    def end_grab(self) -> None:
        """Release the grabbed window, leaving it at its current position."""
        if self._active:
            logger.info("Window released at (%d, %d)", self._last_win_x, self._last_win_y)
        self._active = False
        self._target_info = None

    def cancel_grab(self) -> None:
        """Cancel drag (e.g. on hand loss or pause), leaving window at its current position."""
        if self._active:
            logger.info("Window grab cancelled — position retained")
        self._active = False
        self._target_info = None
