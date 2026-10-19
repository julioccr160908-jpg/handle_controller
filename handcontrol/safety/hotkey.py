"""Global emergency hotkey (Section 11.1 / Phase 4).

Registers a system-wide hotkey using the Windows ``RegisterHotKey`` API so the
user can trigger an emergency stop even when the application window is not
focused.

Default hotkey: **Ctrl+Shift+F12**.

The hotkey is managed in a dedicated background thread that pumps a message
loop.  On activation it calls the provided callback (thread-safe).

Windows-only.  On other platforms the class is a no-op stub.
"""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Callable

logger = logging.getLogger(__name__)

# Default hotkey: Ctrl+Shift+F12.
_DEFAULT_MOD = 0x0002 | 0x0004  # MOD_CONTROL | MOD_SHIFT
_DEFAULT_VK = 0x7B  # VK_F12
_HOTKEY_ID = 1


class EmergencyHotkey:
    """Registers and manages a global emergency-stop hotkey.

    Parameters:
        callback: Callable called (with no args) on the background thread when
                  the hotkey is pressed.
        modifiers: Windows virtual key modifiers (MOD_CONTROL, MOD_SHIFT, etc.).
        vk_code: Virtual key code of the trigger key.
    """

    def __init__(
        self,
        callback: Callable[[], None],
        modifiers: int = _DEFAULT_MOD,
        vk_code: int = _DEFAULT_VK,
    ) -> None:
        self._callback = callback
        self._modifiers = modifiers
        self._vk_code = vk_code
        self._thread: threading.Thread | None = None
        self._registered = False
        self._hwnd: int = 0

        if sys.platform != "win32":
            logger.warning("EmergencyHotkey: non-Windows platform — hotkey disabled.")

    @property
    def is_registered(self) -> bool:
        """Whether the hotkey is currently registered."""
        return self._registered

    def start(self) -> bool:
        """Register the hotkey and start the message loop thread.

        Returns:
            True if registered successfully.
        """
        if sys.platform != "win32":
            return False

        self._thread = threading.Thread(
            target=self._message_loop,
            name="HotkeyThread",
            daemon=True,
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        """Unregister the hotkey and stop the message loop."""
        if sys.platform != "win32":
            return
        # Post WM_QUIT to the hotkey thread's message queue.
        if self._thread and self._thread.is_alive():
            try:
                import ctypes

                ctypes.windll.user32.PostThreadMessageW(
                    self._thread.ident,
                    0x0012,
                    0,
                    0,  # WM_QUIT
                )
                self._thread.join(timeout=1.0)
            except Exception:
                logger.exception("EmergencyHotkey: error posting WM_QUIT")

    def _message_loop(self) -> None:
        """Windows message pump — runs on the dedicated hotkey thread."""
        import ctypes
        import ctypes.wintypes

        user32 = ctypes.windll.user32

        ok = user32.RegisterHotKey(None, _HOTKEY_ID, self._modifiers, self._vk_code)
        if not ok:
            logger.error(
                "EmergencyHotkey: RegisterHotKey failed (error %d). "
                "Another application may have claimed the same hotkey.",
                ctypes.windll.kernel32.GetLastError(),
            )
            return

        self._registered = True
        logger.info("EmergencyHotkey: Ctrl+Shift+F12 registered (id=%d)", _HOTKEY_ID)

        try:
            msg = ctypes.wintypes.MSG()
            wm_hotkey = 0x0312
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
                if msg.message == wm_hotkey and msg.wParam == _HOTKEY_ID:
                    logger.warning("EmergencyHotkey: hotkey triggered!")
                    try:
                        self._callback()
                    except Exception:
                        logger.exception("EmergencyHotkey: callback error")
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            if self._registered:
                user32.UnregisterHotKey(None, _HOTKEY_ID)
                self._registered = False
                logger.info("EmergencyHotkey: unregistered")
