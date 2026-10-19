"""Window management action executor (Section 8, Phase 6).

Implements ``BaseExecutor`` for:
- window_grab            — begin relative dragging session
- window_release         — end relative dragging session
- window_minimize        — minimize target/active window
- window_maximize        — maximize target/active window
- window_restore         — restore target/active window
- window_toggle_maximize — toggle maximize/restore
- window_move_to_monitor — transfer window to specified/next monitor
"""

from __future__ import annotations

import logging
import time

from handcontrol.actions.base import BaseExecutor
from handcontrol.tracking.models import ActionRequest, ActionResult, ActionStatus
from handcontrol.windows.drag import WindowDragSession
from handcontrol.windows.manager import WindowManager
from handcontrol.windows.monitors import VirtualDesktop, enumerate_monitors

logger = logging.getLogger(__name__)


class WindowExecutor(BaseExecutor):
    """Executes window management actions."""

    def __init__(
        self,
        window_manager: WindowManager | None = None,
        virtual_desktop: VirtualDesktop | None = None,
    ) -> None:
        self.wm = window_manager or WindowManager()
        self.desktop = virtual_desktop or enumerate_monitors()
        self.drag = WindowDragSession(window_manager=self.wm)

    def execute(self, request: ActionRequest) -> ActionResult:
        """Execute a window management action."""
        name = request.action_name
        params = request.parameters
        now = time.monotonic()

        try:
            if name == "window_grab":
                hand_x = float(params.get("x", params.get("norm_x", 0.0)))
                hand_y = float(params.get("y", params.get("norm_y", 0.0)))
                target_hwnd = params.get("hwnd")
                ts = params.get("timestamp")
                ok, err, info = self.drag.start_grab(hand_x, hand_y, target_hwnd, timestamp=ts)
                if not ok:
                    return ActionResult(
                        action_name=name,
                        status=ActionStatus.REFUSED,
                        reason=err,
                        timestamp=now,
                    )
                return ActionResult(
                    action_name=name,
                    status=ActionStatus.EXECUTED,
                    reason=f"Grabbed: {info.title[:25]}" if info else "",
                    timestamp=now,
                )

            if name == "window_release":
                self.drag.end_grab()
                return ActionResult(
                    action_name=name,
                    status=ActionStatus.EXECUTED,
                    reason="Window released",
                    timestamp=now,
                )

            if name == "window_minimize":
                hwnd = self._resolve_hwnd(params)
                if not hwnd:
                    return ActionResult(
                        action_name=name,
                        status=ActionStatus.REFUSED,
                        reason="No target window found to minimize",
                        timestamp=now,
                    )
                ok = self.wm.minimize_window(hwnd)
                return ActionResult(
                    action_name=name,
                    status=ActionStatus.EXECUTED if ok else ActionStatus.FAILED,
                    reason="" if ok else "ShowWindow(SW_MINIMIZE) failed",
                    timestamp=now,
                )

            if name == "window_maximize":
                hwnd = self._resolve_hwnd(params)
                if not hwnd:
                    return ActionResult(
                        action_name=name,
                        status=ActionStatus.REFUSED,
                        reason="No target window found to maximize",
                        timestamp=now,
                    )
                ok = self.wm.maximize_window(hwnd)
                return ActionResult(
                    action_name=name,
                    status=ActionStatus.EXECUTED if ok else ActionStatus.FAILED,
                    reason="" if ok else "ShowWindow(SW_MAXIMIZE) failed",
                    timestamp=now,
                )

            if name == "window_restore":
                hwnd = self._resolve_hwnd(params)
                if not hwnd:
                    return ActionResult(
                        action_name=name,
                        status=ActionStatus.REFUSED,
                        reason="No target window found to restore",
                        timestamp=now,
                    )
                ok = self.wm.restore_window(hwnd)
                return ActionResult(
                    action_name=name,
                    status=ActionStatus.EXECUTED if ok else ActionStatus.FAILED,
                    reason="" if ok else "ShowWindow(SW_RESTORE) failed",
                    timestamp=now,
                )

            if name == "window_toggle_maximize":
                hwnd = self._resolve_hwnd(params)
                if not hwnd:
                    return ActionResult(
                        action_name=name,
                        status=ActionStatus.REFUSED,
                        reason="No target window found",
                        timestamp=now,
                    )
                ok = self.wm.toggle_maximize(hwnd)
                return ActionResult(
                    action_name=name,
                    status=ActionStatus.EXECUTED if ok else ActionStatus.FAILED,
                    reason="",
                    timestamp=now,
                )

            if name == "window_move_to_monitor":
                hwnd = self._resolve_hwnd(params)
                if not hwnd:
                    return ActionResult(
                        action_name=name,
                        status=ActionStatus.REFUSED,
                        reason="No target window found",
                        timestamp=now,
                    )
                info = self.wm._inspect_window(hwnd)
                if not info:
                    return ActionResult(
                        action_name=name,
                        status=ActionStatus.REFUSED,
                        reason="Window not available",
                        timestamp=now,
                    )
                current_mon = self.desktop.get_monitor_for_rect(info.rect)
                current_idx = current_mon.index if current_mon else 0
                target_mon = self.desktop.get_next_monitor(current_idx)
                ok = self.wm.move_window_to_monitor(hwnd, target_mon)
                return ActionResult(
                    action_name=name,
                    status=ActionStatus.EXECUTED if ok else ActionStatus.FAILED,
                    reason=f"Moved to monitor {target_mon.index}",
                    timestamp=now,
                )

            return ActionResult(
                action_name=name,
                status=ActionStatus.REFUSED,
                reason=f"WindowExecutor: unknown action '{name}'",
                timestamp=now,
            )

        except Exception as exc:
            logger.exception("WindowExecutor error for '%s'", name)
            return ActionResult(
                action_name=name,
                status=ActionStatus.FAILED,
                reason=str(exc),
                timestamp=now,
            )

    def release_all(self) -> None:
        """Cancel any active dragging session on hand loss, pause, or emergency."""
        if self.drag.is_active:
            self.drag.cancel_grab()

    # ---------------------------------------------------------------- private

    def _resolve_hwnd(self, params: dict) -> int | None:
        if "hwnd" in params:
            return int(params["hwnd"])
        active = self.wm.get_foreground_window()
        return active.hwnd if active else None
