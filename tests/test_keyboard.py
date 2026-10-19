"""Unit and integration tests for Phase 5 — Keyboard and Media actions (Section 7).

Tests:
1. KeyboardValidator: normalization, valid shortcuts, dangerous shortcut blocking,
   unknown key rejection.
2. KeyboardExecutor: mock SendInput, shortcuts (Alt+Tab, Ctrl+C, Ctrl+V, Ctrl+Z, Win+D, Esc),
   media controls (play/pause, next, prev, volume up/down, mute), rate limiting,
   held keys tracking and release_all.
3. CompositeExecutor: routing between mouse and keyboard, cascaded release_all.
4. Phase 5 Integration: end-to-end execution through Allowlist, Controller, and SafetyManager.
"""

from __future__ import annotations

import pytest

from handcontrol.actions.allowlist import Allowlist
from handcontrol.actions.composite_executor import CompositeExecutor
from handcontrol.actions.controller import ActionController
from handcontrol.actions.keyboard_executor import (
    INPUT,
    KEYEVENTF_EXTENDEDKEY,
    KEYEVENTF_KEYUP,
    KeyboardExecutor,
)
from handcontrol.actions.keyboard_validator import (
    VK_CONTROL,
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
    normalize_shortcut_string,
    validate_shortcut,
)
from handcontrol.actions.mouse_executor import MouseExecutor
from handcontrol.safety.manager import SafetyManager
from handcontrol.tracking.models import ActionRequest, ActionStatus

# ---------------------------------------------------------------------------
# 1. KeyboardValidator tests
# ---------------------------------------------------------------------------


class TestKeyboardValidator:
    def test_normalize_shortcut_string(self):
        assert normalize_shortcut_string("Ctrl + C") == "ctrl+c"
        assert normalize_shortcut_string("  alt +  Tab ") == "alt+tab"
        assert normalize_shortcut_string("Ctrl+Shift+F12") == "ctrl+shift+f12"

    def test_valid_standard_shortcuts(self):
        for s in ("Ctrl+C", "Ctrl+V", "Ctrl+Z", "Alt+Tab", "Win+D", "Esc"):
            ok, err, vks = validate_shortcut(s)
            assert ok, f"Expected '{s}' to be valid, got error: {err}"
            assert len(vks) >= 1
            assert err == ""

    def test_dangerous_shortcuts_blocked(self):
        for s in ("Ctrl+Alt+Del", "ctrl+alt+delete", "Win+L", "Ctrl+Shift+Esc", "Alt+F4"):
            ok, err, vks = validate_shortcut(s)
            assert not ok, f"Expected '{s}' to be blocked as dangerous"
            assert "Dangerous shortcut" in err
            assert vks == []

    def test_unknown_key_rejected(self):
        ok, err, vks = validate_shortcut("Ctrl+InvalidKeyName")
        assert not ok
        assert "Unknown or disallowed key" in err
        assert vks == []

    def test_empty_shortcut_rejected(self):
        ok, err, vks = validate_shortcut("")
        assert not ok
        assert "cannot be empty" in err

    def test_function_keys_valid(self):
        ok, _, vks = validate_shortcut("F1")
        assert ok
        assert vks == [0x70]


# ---------------------------------------------------------------------------
# 2. KeyboardExecutor tests (mock SendInput)
# ---------------------------------------------------------------------------


class MockKeyboardInputRecorder:
    """Records keyboard inputs sent to user32.SendInput."""

    def __init__(self) -> None:
        self.calls: list[list[INPUT]] = []
        self.should_succeed = True

    def send_keyboard(self, *inputs: INPUT) -> bool:
        if not self.should_succeed:
            return False
        self.calls.append(list(inputs))
        return True


class TestKeyboardExecutor:
    @pytest.fixture
    def mock_recorder(self) -> MockKeyboardInputRecorder:
        return MockKeyboardInputRecorder()

    @pytest.fixture
    def executor(self, mock_recorder: MockKeyboardInputRecorder) -> KeyboardExecutor:
        return KeyboardExecutor(send_input_fn=mock_recorder.send_keyboard)

    def test_shortcut_ctrl_c(
        self, executor: KeyboardExecutor, mock_recorder: MockKeyboardInputRecorder
    ):
        req = ActionRequest(action_name="shortcut_ctrl_c")
        res = executor.execute(req)
        assert res.status == ActionStatus.EXECUTED
        assert len(mock_recorder.calls) == 1
        inputs = mock_recorder.calls[0]
        # Modifier down (Ctrl), Key down (C), Key up (C), Modifier up (Ctrl)
        assert len(inputs) == 4
        assert inputs[0]._input.ki.wVk == VK_CONTROL
        assert inputs[0]._input.ki.dwFlags == 0  # down
        assert inputs[1]._input.ki.wVk == ord("C")
        assert inputs[1]._input.ki.dwFlags == 0  # down
        assert inputs[2]._input.ki.wVk == ord("C")
        assert inputs[2]._input.ki.dwFlags == KEYEVENTF_KEYUP
        assert inputs[3]._input.ki.wVk == VK_CONTROL
        assert inputs[3]._input.ki.dwFlags == KEYEVENTF_KEYUP
        # No keys left held
        assert len(executor.held_keys) == 0

    def test_shortcut_alt_tab(
        self, executor: KeyboardExecutor, mock_recorder: MockKeyboardInputRecorder
    ):
        req = ActionRequest(action_name="shortcut_alt_tab")
        res = executor.execute(req)
        assert res.status == ActionStatus.EXECUTED
        inputs = mock_recorder.calls[0]
        assert inputs[0]._input.ki.wVk == VK_MENU
        assert inputs[1]._input.ki.wVk == VK_TAB

    def test_key_esc(self, executor: KeyboardExecutor, mock_recorder: MockKeyboardInputRecorder):
        req = ActionRequest(action_name="key_esc")
        res = executor.execute(req)
        assert res.status == ActionStatus.EXECUTED
        inputs = mock_recorder.calls[0]
        assert len(inputs) == 2
        assert inputs[0]._input.ki.wVk == VK_ESCAPE
        assert inputs[0]._input.ki.dwFlags == 0
        assert inputs[1]._input.ki.wVk == VK_ESCAPE
        assert inputs[1]._input.ki.dwFlags == KEYEVENTF_KEYUP

    def test_generic_shortcut_param(
        self, executor: KeyboardExecutor, mock_recorder: MockKeyboardInputRecorder
    ):
        req = ActionRequest(action_name="shortcut", parameters={"keys": "Win+D"})
        res = executor.execute(req)
        assert res.status == ActionStatus.EXECUTED
        inputs = mock_recorder.calls[0]
        assert inputs[0]._input.ki.wVk == VK_LWIN
        assert inputs[1]._input.ki.wVk == ord("D")

    def test_dangerous_shortcut_refused(self, executor: KeyboardExecutor):
        req = ActionRequest(action_name="shortcut", parameters={"keys": "Ctrl+Alt+Del"})
        res = executor.execute(req)
        assert res.status == ActionStatus.REFUSED
        assert "Dangerous shortcut" in res.reason

    def test_multimedia_keys(
        self, executor: KeyboardExecutor, mock_recorder: MockKeyboardInputRecorder
    ):
        media_map = {
            "media_play_pause": VK_MEDIA_PLAY_PAUSE,
            "media_next": VK_MEDIA_NEXT_TRACK,
            "media_prev": VK_MEDIA_PREV_TRACK,
            "volume_up": VK_VOLUME_UP,
            "volume_down": VK_VOLUME_DOWN,
            "volume_mute": VK_VOLUME_MUTE,
        }
        for action_name, expected_vk in media_map.items():
            mock_recorder.calls.clear()
            executor._last_executed.clear()  # reset rate limit
            res = executor.execute(ActionRequest(action_name=action_name))
            assert res.status == ActionStatus.EXECUTED
            inputs = mock_recorder.calls[0]
            assert inputs[0]._input.ki.wVk == expected_vk
            assert inputs[0]._input.ki.dwFlags & KEYEVENTF_EXTENDEDKEY

    def test_rate_limiting(self, executor: KeyboardExecutor):
        req = ActionRequest(action_name="shortcut_ctrl_c")
        res1 = executor.execute(req)
        assert res1.status == ActionStatus.EXECUTED

        # Immediate second call should be refused by rate limit
        res2 = executor.execute(req)
        assert res2.status == ActionStatus.REFUSED
        assert "Rate limit exceeded" in res2.reason

    def test_release_all_releases_held_keys(
        self, executor: KeyboardExecutor, mock_recorder: MockKeyboardInputRecorder
    ):
        # Manually register a held key (simulating mid-execution interruption)
        executor._held_keys.add(VK_CONTROL)
        executor._held_keys.add(ord("C"))

        mock_recorder.calls.clear()
        executor.release_all()

        assert len(executor.held_keys) == 0
        assert len(mock_recorder.calls) == 1
        released_inputs = mock_recorder.calls[0]
        assert len(released_inputs) == 2
        for inp in released_inputs:
            assert inp._input.ki.dwFlags & KEYEVENTF_KEYUP


# ---------------------------------------------------------------------------
# 3. CompositeExecutor tests
# ---------------------------------------------------------------------------


class MockAllRecorder:
    def __init__(self) -> None:
        self.mouse_calls = []
        self.keyboard_calls = []

    def send_mouse(self, *inputs) -> bool:
        self.mouse_calls.append(inputs)
        return True

    def send_keyboard(self, *inputs) -> bool:
        self.keyboard_calls.append(inputs)
        return True


class TestCompositeExecutor:
    def test_routes_mouse_and_keyboard(self):
        rec = MockAllRecorder()
        mouse = MouseExecutor(send_mouse_fn=rec.send_mouse)
        keyboard = KeyboardExecutor(send_input_fn=rec.send_keyboard)
        comp = CompositeExecutor(mouse_executor=mouse, keyboard_executor=keyboard)

        # Mouse action
        res_m = comp.execute(ActionRequest(action_name="click_left"))
        assert res_m.status == ActionStatus.EXECUTED
        assert len(rec.mouse_calls) == 1
        assert len(rec.keyboard_calls) == 0

        # Keyboard action
        res_k = comp.execute(ActionRequest(action_name="shortcut_ctrl_c"))
        assert res_k.status == ActionStatus.EXECUTED
        assert len(rec.keyboard_calls) == 1

        # Release all cascades to both
        mouse._drag_active = True
        keyboard._held_keys.add(VK_CONTROL)
        comp.release_all()
        assert not mouse.is_dragging
        assert len(keyboard.held_keys) == 0


# ---------------------------------------------------------------------------
# 4. Phase 5 Integration tests
# ---------------------------------------------------------------------------


class TestPhase5Integration:
    def test_allowlist_validates_shortcuts(self):
        allowlist = Allowlist()

        # Valid shortcut
        errs = allowlist.validate("shortcut", {"keys": "Ctrl+C"})
        assert errs == []

        # Dangerous shortcut rejected by allowlist
        errs_danger = allowlist.validate("shortcut", {"keys": "Ctrl+Alt+Del"})
        assert len(errs_danger) == 1
        assert "Dangerous shortcut" in errs_danger[0]

        # Missing required 'keys' param
        errs_missing = allowlist.validate("shortcut", {})
        assert len(errs_missing) == 1
        assert "Missing required parameter 'keys'" in errs_missing[0]

    def test_full_controller_with_composite_executor(self):
        rec = MockAllRecorder()
        mouse = MouseExecutor(send_mouse_fn=rec.send_mouse)
        keyboard = KeyboardExecutor(send_input_fn=rec.send_keyboard)
        composite = CompositeExecutor(mouse_executor=mouse, keyboard_executor=keyboard)

        controller = ActionController(executor=composite)
        safety = SafetyManager(controller, None)
        safety.enable_real_mode(composite)

        # Execute shortcut via controller
        res = controller.submit(ActionRequest(action_name="shortcut_ctrl_v"))
        assert res.status == ActionStatus.EXECUTED

        # Execute media action via controller
        res_media = controller.submit(ActionRequest(action_name="volume_up"))
        assert res_media.status == ActionStatus.EXECUTED

        # Emergency stop calls release_all on composite
        controller.emergency_stop()
        assert controller.is_emergency
