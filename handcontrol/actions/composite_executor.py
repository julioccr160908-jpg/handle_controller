"""Composite executor routing actions to specialized executors (Phase 4 & 5).

Routes:
- Mouse actions (cursor_move, click_*, drag_*, scroll) → MouseExecutor
- Keyboard & media actions (shortcut, shortcut_*, media_*, volume_*, key_*) → KeyboardExecutor

Guarantees that ``release_all()`` calls ``release_all()`` on all sub-executors,
ensuring that neither mouse buttons nor keyboard keys remain stuck.
"""

from __future__ import annotations

import logging

from handcontrol.actions.base import BaseExecutor
from handcontrol.actions.keyboard_executor import KeyboardExecutor
from handcontrol.actions.mouse_executor import MouseExecutor
from handcontrol.tracking.models import ActionRequest, ActionResult, ActionStatus

logger = logging.getLogger(__name__)

# Action categories for routing
_MOUSE_ACTIONS = frozenset(
    {
        "cursor_move",
        "click_left",
        "click_right",
        "click_middle",
        "click_double",
        "drag_start",
        "drag_end",
        "scroll",
    }
)

_KEYBOARD_ACTIONS = frozenset(
    {
        "shortcut",
        "key_press",
        "shortcut_alt_tab",
        "shortcut_ctrl_c",
        "shortcut_ctrl_v",
        "shortcut_ctrl_z",
        "shortcut_win_d",
        "key_esc",
        "media_play_pause",
        "media_next",
        "media_prev",
        "volume_up",
        "volume_down",
        "volume_mute",
    }
)


class CompositeExecutor(BaseExecutor):
    """Coordinates specialized executors for mouse, keyboard, and media actions."""

    def __init__(
        self,
        mouse_executor: MouseExecutor | None = None,
        keyboard_executor: KeyboardExecutor | None = None,
    ) -> None:
        self.mouse = mouse_executor or MouseExecutor()
        self.keyboard = keyboard_executor or KeyboardExecutor()

    def execute(self, request: ActionRequest) -> ActionResult:
        """Route action request to the appropriate sub-executor."""
        name = request.action_name

        if name in _MOUSE_ACTIONS:
            return self.mouse.execute(request)

        if name in _KEYBOARD_ACTIONS:
            return self.keyboard.execute(request)

        if name == "noop":
            import time

            return ActionResult(
                action_name=name,
                status=ActionStatus.EXECUTED,
                reason="noop",
                timestamp=time.monotonic(),
            )

        import time

        return ActionResult(
            action_name=name,
            status=ActionStatus.REFUSED,
            reason=f"CompositeExecutor: unhandled action '{name}'",
            timestamp=time.monotonic(),
        )

    def release_all(self) -> None:
        """Release all held inputs across both mouse and keyboard executors."""
        try:
            self.mouse.release_all()
        except Exception:
            logger.exception("Error during mouse release_all")

        try:
            self.keyboard.release_all()
        except Exception:
            logger.exception("Error during keyboard release_all")
