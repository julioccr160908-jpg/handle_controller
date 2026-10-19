"""Keyboard and media actions executor using Windows SendInput (Section 7, Phase 5).

Implements ``BaseExecutor`` for:
- Shortcuts: Alt+Tab, Ctrl+C, Ctrl+V, Ctrl+Z, Win+D, Esc, and custom validated combinations.
- Media controls: play/pause, next track, prev track, volume +, volume -, mute.

Enforces:
- Rate limiting / debounce per action (limiting repetition per second).
- Tracking of held keys with full cleanup on ``release_all()`` to ensure no keys
  remain stuck on hand loss, pause, emergency, or window close.
- Support for dependency injection of ``send_input_fn`` for test isolation.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import time
from collections.abc import Callable

from handcontrol.actions.base import BaseExecutor
from handcontrol.actions.keyboard_validator import (
    MODIFIER_KEYS,
    VK_ESCAPE,
    VK_LWIN,
    VK_MEDIA_NEXT_TRACK,
    VK_MEDIA_PLAY_PAUSE,
    VK_MEDIA_PREV_TRACK,
    VK_MENU,
    VK_TAB,
    VK_VOLUME_DOWN,
    VK_VOLUME_MUTE,
    VK_VOLUME_UP,
    validate_shortcut,
)
from handcontrol.tracking.models import ActionRequest, ActionResult, ActionStatus

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Windows SendInput structures for Keyboard
# ---------------------------------------------------------------------------

INPUT_KEYBOARD = 1

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.wintypes.WORD),
        ("wScan", ctypes.wintypes.WORD),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _InputUnion(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.wintypes.DWORD), ("_input", _InputUnion)]


def _send_keyboard(*inputs: INPUT) -> bool:
    """Send keyboard inputs via Windows user32.SendInput."""
    count = len(inputs)
    arr = (INPUT * count)(*inputs)
    sent = ctypes.windll.user32.SendInput(count, arr, ctypes.sizeof(INPUT))
    return sent == count


def _key_event(vk_code: int, is_up: bool = False) -> INPUT:
    """Create a single key down or key up INPUT structure."""
    ki = KEYBDINPUT()
    ki.wVk = vk_code
    ki.wScan = 0
    flags = 0
    if is_up:
        flags |= KEYEVENTF_KEYUP
    if vk_code in (
        VK_LWIN,
        VK_MEDIA_PLAY_PAUSE,
        VK_MEDIA_NEXT_TRACK,
        VK_MEDIA_PREV_TRACK,
        VK_VOLUME_UP,
        VK_VOLUME_DOWN,
        VK_VOLUME_MUTE,
    ):
        flags |= KEYEVENTF_EXTENDEDKEY
    ki.dwFlags = flags
    ki.time = 0
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp._input.ki = ki
    return inp


# ---------------------------------------------------------------------------
# Default rate limits (min seconds between successive activations)
# ---------------------------------------------------------------------------

_DEFAULT_MIN_INTERVAL = 0.12  # ~8 per second max
_VOLUME_MIN_INTERVAL = 0.04  # responsive volume ramping


# ---------------------------------------------------------------------------
# Keyboard and Media Executor
# ---------------------------------------------------------------------------


class KeyboardExecutor(BaseExecutor):
    """Windows keyboard and multimedia executor.

    Parameters:
        send_input_fn: Optional callable to replace user32.SendInput for testing.
    """

    def __init__(self, send_input_fn: Callable[..., bool] | None = None) -> None:
        self._send_input = send_input_fn or _send_keyboard
        self._held_keys: set[int] = set()
        self._last_executed: dict[str, float] = {}

    @property
    def held_keys(self) -> set[int]:
        """Set of virtual key codes currently held down."""
        return set(self._held_keys)

    def execute(self, request: ActionRequest) -> ActionResult:
        """Execute a keyboard or multimedia action request."""
        name = request.action_name
        params = request.parameters
        now = time.monotonic()

        # Enforce rate limit / debounce per action
        min_interval = (
            _VOLUME_MIN_INTERVAL if name in ("volume_up", "volume_down") else _DEFAULT_MIN_INTERVAL
        )
        last_t = self._last_executed.get(name, 0.0)
        if (now - last_t) < min_interval:
            return ActionResult(
                action_name=name,
                status=ActionStatus.REFUSED,
                reason=f"Rate limit exceeded (min interval: {min_interval:.2f}s)",
                timestamp=now,
            )

        try:
            # 1. Multimedia actions
            if name == "media_play_pause":
                res = self._send_single_key(VK_MEDIA_PLAY_PAUSE, name, now)
            elif name == "media_next":
                res = self._send_single_key(VK_MEDIA_NEXT_TRACK, name, now)
            elif name == "media_prev":
                res = self._send_single_key(VK_MEDIA_PREV_TRACK, name, now)
            elif name == "volume_up":
                res = self._send_single_key(VK_VOLUME_UP, name, now)
            elif name == "volume_down":
                res = self._send_single_key(VK_VOLUME_DOWN, name, now)
            elif name == "volume_mute":
                res = self._send_single_key(VK_VOLUME_MUTE, name, now)

            # 2. Discrete standard shortcuts
            elif name == "shortcut_alt_tab":
                res = self._execute_vk_sequence([VK_MENU, VK_TAB], name, now)
            elif name == "shortcut_ctrl_c":
                res = self._execute_vk_sequence([0x11, ord("C")], name, now)
            elif name == "shortcut_ctrl_v":
                res = self._execute_vk_sequence([0x11, ord("V")], name, now)
            elif name == "shortcut_ctrl_z":
                res = self._execute_vk_sequence([0x11, ord("Z")], name, now)
            elif name == "shortcut_win_d":
                res = self._execute_vk_sequence([VK_LWIN, ord("D")], name, now)
            elif name == "key_esc":
                res = self._send_single_key(VK_ESCAPE, name, now)

            # 3. Generic shortcut parameter
            elif name == "shortcut":
                keys_str = params.get("keys", "")
                res = self._execute_shortcut(keys_str, name, now)

            # 4. Single key press
            elif name == "key_press":
                key_name = params.get("key", "")
                res = self._execute_shortcut(key_name, name, now)

            else:
                return ActionResult(
                    action_name=name,
                    status=ActionStatus.REFUSED,
                    reason=f"KeyboardExecutor: unknown action '{name}'",
                    timestamp=now,
                )

            if res.status == ActionStatus.EXECUTED:
                self._last_executed[name] = now
            return res

        except Exception as exc:
            logger.exception("KeyboardExecutor error for '%s'", name)
            return ActionResult(
                action_name=name,
                status=ActionStatus.FAILED,
                reason=str(exc),
                timestamp=now,
            )

    def release_all(self) -> None:
        """Release all currently held keys immediately.

        Guarantees that no keys remain stuck on hand loss, pause, or emergency.
        """
        if not self._held_keys:
            return

        logger.info("KeyboardExecutor.release_all: releasing %d held keys", len(self._held_keys))
        up_events = [_key_event(vk, is_up=True) for vk in self._held_keys]
        try:
            self._send_input(*up_events)
        except Exception:
            logger.exception("KeyboardExecutor: error during release_all")
        finally:
            self._held_keys.clear()

    # ---------------------------------------------------------------- private

    def _send_single_key(self, vk_code: int, action_name: str, now: float) -> ActionResult:
        """Send a single key down + key up stroke."""
        self._held_keys.add(vk_code)
        down = _key_event(vk_code, is_up=False)
        up = _key_event(vk_code, is_up=True)
        ok = self._send_input(down, up)
        self._held_keys.discard(vk_code)

        return ActionResult(
            action_name=action_name,
            status=ActionStatus.EXECUTED if ok else ActionStatus.FAILED,
            reason="" if ok else "SendInput failed",
            timestamp=now,
        )

    def _execute_vk_sequence(
        self,
        vks: list[int],
        action_name: str,
        now: float,
    ) -> ActionResult:
        """Execute a modifier + key sequence cleanly.

        Modifiers are pressed down in order, regular key is pressed down and up,
        then modifiers are released in reverse order.
        """
        if not vks:
            return ActionResult(
                action_name=action_name,
                status=ActionStatus.FAILED,
                reason="Empty key sequence",
                timestamp=now,
            )

        modifiers = [vk for vk in vks if vk in MODIFIER_KEYS]
        regular = [vk for vk in vks if vk not in MODIFIER_KEYS]

        events: list[INPUT] = []

        # 1. Modifiers down
        for mod in modifiers:
            self._held_keys.add(mod)
            events.append(_key_event(mod, is_up=False))

        # 2. Regular keys down + up
        for reg in regular:
            self._held_keys.add(reg)
            events.append(_key_event(reg, is_up=False))
            events.append(_key_event(reg, is_up=True))
            self._held_keys.discard(reg)

        # 3. Modifiers up (reverse order)
        for mod in reversed(modifiers):
            events.append(_key_event(mod, is_up=True))
            self._held_keys.discard(mod)

        ok = self._send_input(*events)
        return ActionResult(
            action_name=action_name,
            status=ActionStatus.EXECUTED if ok else ActionStatus.FAILED,
            reason="" if ok else "SendInput failed",
            timestamp=now,
        )

    def _execute_shortcut(self, keys_str: str, action_name: str, now: float) -> ActionResult:
        """Parse, validate, and execute a shortcut string."""
        is_valid, err, vks = validate_shortcut(keys_str)
        if not is_valid:
            return ActionResult(
                action_name=action_name,
                status=ActionStatus.REFUSED,
                reason=err,
                timestamp=now,
            )

        return self._execute_vk_sequence(vks, action_name, now)
