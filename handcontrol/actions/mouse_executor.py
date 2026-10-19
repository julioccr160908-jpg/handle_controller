"""Real mouse executor using Windows SendInput (Section 4, Phase 4).

Implements ``BaseExecutor`` using ``ctypes`` + ``SendInput`` for:
- cursor_move    — absolute move in virtual screen space
- click_left     — left button down + up
- click_right    — right button down + up
- click_middle   — middle button down + up
- click_double   — double left click
- drag_start     — left button down (hold)
- drag_end       — left button up
- scroll         — vertical wheel scroll

Uses ``MOUSEEVENTF_VIRTUALDESK | MOUSEEVENTF_ABSOLUTE`` so coordinates work
correctly in multi-monitor virtual desktop space (Section 6).

All coordinates can be provided in virtual screen pixels (x, y) or normalized (norm_x, norm_y).
The ``_to_absolute`` helper converts virtual screen pixels to the [0, 65535] range that
SendInput requires.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import time
from collections.abc import Callable

from handcontrol.actions.base import BaseExecutor
from handcontrol.tracking.models import ActionRequest, ActionResult, ActionStatus

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Windows SendInput structures
# ---------------------------------------------------------------------------

INPUT_MOUSE = 0

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000  # Use the entire virtual desktop

WHEEL_DELTA = 120


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.wintypes.LONG),
        ("dy", ctypes.wintypes.LONG),
        ("mouseData", ctypes.wintypes.DWORD),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _InputUnion(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.wintypes.DWORD), ("_input", _InputUnion)]


# ---------------------------------------------------------------------------
# Helpers: virtual screen metrics and mouse input builders
# ---------------------------------------------------------------------------

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79


def _virtual_screen() -> tuple[int, int, int, int]:
    """Return (left, top, width, height) of the virtual screen."""
    gss = ctypes.windll.user32.GetSystemMetrics
    return (
        gss(SM_XVIRTUALSCREEN),
        gss(SM_YVIRTUALSCREEN),
        gss(SM_CXVIRTUALSCREEN),
        gss(SM_CYVIRTUALSCREEN),
    )


def _send_mouse(*inputs: INPUT) -> bool:
    count = len(inputs)
    arr = (INPUT * count)(*inputs)
    sent = ctypes.windll.user32.SendInput(count, arr, ctypes.sizeof(INPUT))
    return sent == count


def _mouse_move(ax: int, ay: int) -> INPUT:
    mi = MOUSEINPUT()
    mi.dx = ax
    mi.dy = ay
    mi.dwFlags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
    inp = INPUT()
    inp.type = INPUT_MOUSE
    inp._input.mi = mi
    return inp


def _mouse_button(flags: int) -> INPUT:
    mi = MOUSEINPUT()
    mi.dwFlags = flags
    inp = INPUT()
    inp.type = INPUT_MOUSE
    inp._input.mi = mi
    return inp


def _mouse_wheel(delta: int) -> INPUT:
    mi = MOUSEINPUT()
    mi.dwFlags = MOUSEEVENTF_WHEEL
    mi.mouseData = ctypes.wintypes.DWORD(delta)
    inp = INPUT()
    inp.type = INPUT_MOUSE
    inp._input.mi = mi
    return inp


# ---------------------------------------------------------------------------
# Real mouse executor
# ---------------------------------------------------------------------------


class MouseExecutor(BaseExecutor):
    """Windows mouse executor using SendInput.

    Tracks drag state so ``release_all`` can release the left button.
    Supports dependency injection of ``send_mouse_fn`` and ``virtual_screen_fn``
    for safe unit testing without hardware side-effects.
    """

    def __init__(
        self,
        send_mouse_fn: Callable[..., bool] | None = None,
        virtual_screen_fn: Callable[[], tuple[int, int, int, int]] | None = None,
    ) -> None:
        self._drag_active = False
        self._send_mouse_fn = send_mouse_fn or _send_mouse
        self._virtual_screen_fn = virtual_screen_fn or _virtual_screen

    @property
    def is_dragging(self) -> bool:
        """Whether a mouse drag is currently held."""
        return self._drag_active

    def to_absolute(self, x: float, y: float) -> tuple[int, int]:
        """Convert virtual screen pixel coords to SendInput absolute [0, 65535]."""
        vl, vt, vw, vh = self._virtual_screen_fn()
        ax = int((x - vl) * 65535 / max(vw, 1))
        ay = int((y - vt) * 65535 / max(vh, 1))
        return (max(0, min(65535, ax)), max(0, min(65535, ay)))

    def execute(self, request: ActionRequest) -> ActionResult:
        """Execute a mouse action request."""
        name = request.action_name
        params = request.parameters
        now = time.monotonic()

        try:
            if name == "cursor_move":
                return self._cursor_move(request, params, now)
            if name == "click_left":
                return self._click(request, MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP, now)
            if name == "click_right":
                return self._click(request, MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP, now)
            if name == "click_middle":
                return self._click(request, MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP, now)
            if name == "click_double":
                return self._click_double(request, now)
            if name == "drag_start":
                return self._drag_start(request, now)
            if name == "drag_end":
                return self._drag_end(request, now)
            if name == "scroll":
                return self._scroll(request, params, now)
            if name == "noop":
                return ActionResult(
                    action_name=name,
                    status=ActionStatus.EXECUTED,
                    reason="noop",
                    timestamp=now,
                )
            return ActionResult(
                action_name=name,
                status=ActionStatus.REFUSED,
                reason=f"MouseExecutor: unknown action '{name}'",
                timestamp=now,
            )
        except Exception as exc:
            logger.exception("MouseExecutor error for '%s'", name)
            return ActionResult(
                action_name=name,
                status=ActionStatus.FAILED,
                reason=str(exc),
                timestamp=now,
            )

    def release_all(self) -> None:
        """Release left button if a drag is active."""
        if self._drag_active:
            try:
                self._send_mouse_fn(_mouse_button(MOUSEEVENTF_LEFTUP))
                logger.info("MouseExecutor: drag released via release_all()")
            except Exception:
                logger.exception("MouseExecutor: release_all() failed")
            finally:
                self._drag_active = False

    # ---------------------------------------------------------------- private

    def _cursor_move(self, req: ActionRequest, params: dict, now: float) -> ActionResult:
        if "x" in params and "y" in params:
            x = float(params["x"])
            y = float(params["y"])
            ax, ay = self.to_absolute(x, y)
        elif "norm_x" in params and "norm_y" in params:
            nx = float(params["norm_x"])
            ny = float(params["norm_y"])
            if nx > 1.0 or ny > 1.0 or nx < 0.0 or ny < 0.0:
                ax, ay = self.to_absolute(nx, ny)
            else:
                vl, vt, vw, vh = self._virtual_screen_fn()
                sx = vl + nx * vw
                sy = vt + ny * vh
                ax, ay = self.to_absolute(sx, sy)
        else:
            return ActionResult(
                action_name=req.action_name,
                status=ActionStatus.FAILED,
                reason="cursor_move requires (x, y) or (norm_x, norm_y)",
                timestamp=now,
            )

        ok = self._send_mouse_fn(_mouse_move(ax, ay))
        return ActionResult(
            action_name=req.action_name,
            status=ActionStatus.EXECUTED if ok else ActionStatus.FAILED,
            reason="" if ok else "SendInput failed",
            timestamp=now,
        )

    def _click(self, req: ActionRequest, down_flag: int, up_flag: int, now: float) -> ActionResult:
        ok = self._send_mouse_fn(_mouse_button(down_flag), _mouse_button(up_flag))
        return ActionResult(
            action_name=req.action_name,
            status=ActionStatus.EXECUTED if ok else ActionStatus.FAILED,
            reason="" if ok else "SendInput failed",
            timestamp=now,
        )

    def _click_double(self, req: ActionRequest, now: float) -> ActionResult:
        ok1 = self._send_mouse_fn(
            _mouse_button(MOUSEEVENTF_LEFTDOWN), _mouse_button(MOUSEEVENTF_LEFTUP)
        )
        time.sleep(0.04)
        ok2 = self._send_mouse_fn(
            _mouse_button(MOUSEEVENTF_LEFTDOWN), _mouse_button(MOUSEEVENTF_LEFTUP)
        )
        ok = ok1 and ok2
        return ActionResult(
            action_name=req.action_name,
            status=ActionStatus.EXECUTED if ok else ActionStatus.FAILED,
            reason="" if ok else "SendInput failed",
            timestamp=now,
        )

    def _drag_start(self, req: ActionRequest, now: float) -> ActionResult:
        if not self._drag_active:
            ok = self._send_mouse_fn(_mouse_button(MOUSEEVENTF_LEFTDOWN))
            if ok:
                self._drag_active = True
            return ActionResult(
                action_name=req.action_name,
                status=ActionStatus.EXECUTED if ok else ActionStatus.FAILED,
                reason="" if ok else "SendInput failed",
                timestamp=now,
            )
        return ActionResult(
            action_name=req.action_name,
            status=ActionStatus.EXECUTED,
            reason="drag already active",
            timestamp=now,
        )

    def _drag_end(self, req: ActionRequest, now: float) -> ActionResult:
        if self._drag_active:
            ok = self._send_mouse_fn(_mouse_button(MOUSEEVENTF_LEFTUP))
            self._drag_active = False
            return ActionResult(
                action_name=req.action_name,
                status=ActionStatus.EXECUTED if ok else ActionStatus.FAILED,
                reason="" if ok else "SendInput failed",
                timestamp=now,
            )
        return ActionResult(
            action_name=req.action_name,
            status=ActionStatus.EXECUTED,
            reason="no active drag",
            timestamp=now,
        )

    def _scroll(self, req: ActionRequest, params: dict, now: float) -> ActionResult:
        delta_y = int(params.get("delta_y", 0)) * WHEEL_DELTA
        if delta_y == 0:
            return ActionResult(
                action_name=req.action_name,
                status=ActionStatus.EXECUTED,
                reason="delta_y=0",
                timestamp=now,
            )
        ok = self._send_mouse_fn(_mouse_wheel(delta_y))
        return ActionResult(
            action_name=req.action_name,
            status=ActionStatus.EXECUTED if ok else ActionStatus.FAILED,
            reason="" if ok else "SendInput failed",
            timestamp=now,
        )
