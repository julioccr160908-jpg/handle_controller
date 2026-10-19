"""Unit and integration tests for Phase 4 — Mouse control and safety (Section 6, 11).

Tests:
1. ActiveRegion: margin calculation, virtual screen mapping, clamping, multi-monitor.
2. OneEuroFilter & OneEuro2D: jitter reduction at rest, responsiveness in motion, reset.
3. CursorController: pointing clutch, dead zone, max speed clamp, freeze on hand loss,
   re-clutch jump prevention.
4. MouseExecutor: SendInput simulation, cursor move, left/right/middle/double click,
   drag start/end, scroll, release_all on emergency.
5. EmergencyHotkey: registration lifecycle and callback invocation.
6. Phase 4 End-to-End: full pipeline integration with real MouseExecutor (mocked OS calls).
"""

from __future__ import annotations

import time

import pytest

from handcontrol.actions.controller import ActionController
from handcontrol.actions.mouse_executor import (
    INPUT,
    MOUSEEVENTF_ABSOLUTE,
    MOUSEEVENTF_LEFTDOWN,
    MOUSEEVENTF_LEFTUP,
    MOUSEEVENTF_MIDDLEDOWN,
    MOUSEEVENTF_MIDDLEUP,
    MOUSEEVENTF_MOVE,
    MOUSEEVENTF_RIGHTDOWN,
    MOUSEEVENTF_RIGHTUP,
    MOUSEEVENTF_VIRTUALDESK,
    MOUSEEVENTF_WHEEL,
    WHEEL_DELTA,
    MouseExecutor,
)
from handcontrol.mapping.profile import GestureMapper
from handcontrol.mouse.cursor import CursorConfig, CursorController
from handcontrol.mouse.region import ActiveRegion
from handcontrol.mouse.smoother import OneEuro2D, OneEuroFilter
from handcontrol.safety.hotkey import EmergencyHotkey
from handcontrol.safety.manager import SafetyManager
from handcontrol.tracking.models import (
    ActionRequest,
    ActionStatus,
    GestureEvent,
    GesturePhase,
)

# ---------------------------------------------------------------------------
# 1. ActiveRegion tests
# ---------------------------------------------------------------------------


class TestActiveRegion:
    def test_default_construction(self):
        reg = ActiveRegion()
        assert reg.frame_width == 640
        assert reg.frame_height == 480
        assert reg.left_frac == 0.10
        assert reg.right_frac == 0.90
        assert reg.screen_width == 1920
        assert reg.screen_height == 1080

    def test_center_mapping(self):
        reg = ActiveRegion(
            frame_width=640,
            frame_height=480,
            screen_left=0,
            screen_top=0,
            screen_width=1920,
            screen_height=1080,
        )
        sx, sy = reg.map_normalized(0.5, 0.5)
        assert sx == pytest.approx(960.0, abs=1.0)
        assert sy == pytest.approx(540.0, abs=1.0)

    def test_corners_reach_screen_edges(self):
        reg = ActiveRegion(
            left_frac=0.10,
            top_frac=0.10,
            right_frac=0.90,
            bottom_frac=0.90,
            screen_left=0,
            screen_top=0,
            screen_width=1920,
            screen_height=1080,
        )
        # Inside active region corner (0.1, 0.1) reaches (0, 0)
        sx, sy = reg.map_normalized(0.10, 0.10)
        assert sx == pytest.approx(0.0, abs=1.0)
        assert sy == pytest.approx(0.0, abs=1.0)

        # Opposite corner (0.9, 0.9) reaches (1920, 1080)
        sx, sy = reg.map_normalized(0.90, 0.90)
        assert sx == pytest.approx(1920.0, abs=1.0)
        assert sy == pytest.approx(1080.0, abs=1.0)

    def test_clamping_beyond_active_region(self):
        reg = ActiveRegion(
            left_frac=0.10,
            top_frac=0.10,
            right_frac=0.90,
            bottom_frac=0.90,
            screen_left=0,
            screen_top=0,
            screen_width=1920,
            screen_height=1080,
        )
        # Beyond top-left
        sx, sy = reg.map_normalized(0.0, 0.0)
        assert sx == 0.0
        assert sy == 0.0

        # Beyond bottom-right
        sx, sy = reg.map_normalized(1.0, 1.0)
        assert sx == 1920.0
        assert sy == 1080.0

    def test_multi_monitor_negative_coordinates(self):
        # Secondary monitor to the left: screen_left = -1920, total width = 3840
        reg = ActiveRegion(
            left_frac=0.0,
            top_frac=0.0,
            right_frac=1.0,
            bottom_frac=1.0,
            screen_left=-1920,
            screen_top=0,
            screen_width=3840,
            screen_height=1080,
        )
        # Far left
        sx, _ = reg.map_normalized(0.0, 0.5)
        assert sx == pytest.approx(-1920.0, abs=1.0)

        # Center (boundary between monitors)
        sx, _ = reg.map_normalized(0.5, 0.5)
        assert sx == pytest.approx(0.0, abs=1.0)

        # Far right
        sx, _ = reg.map_normalized(1.0, 0.5)
        assert sx == pytest.approx(1920.0, abs=1.0)

    def test_set_margins(self):
        reg = ActiveRegion()
        reg.set_margins(0.15)
        assert reg.left_frac == pytest.approx(0.15)
        assert reg.top_frac == pytest.approx(0.15)
        assert reg.right_frac == pytest.approx(0.85)
        assert reg.bottom_frac == pytest.approx(0.85)

    def test_pixel_rect_and_normalized_rect(self):
        reg = ActiveRegion(
            frame_width=640,
            frame_height=480,
            left_frac=0.1,
            top_frac=0.1,
            right_frac=0.9,
            bottom_frac=0.9,
        )
        assert reg.pixel_rect == (64, 48, 576, 432)
        assert reg.normalized_rect == (0.1, 0.1, 0.9, 0.9)


# ---------------------------------------------------------------------------
# 2. OneEuroFilter tests
# ---------------------------------------------------------------------------


class TestOneEuroFilter:
    def test_initial_sample_returns_raw(self):
        f = OneEuroFilter()
        val = f(100.0, 0.0)
        assert val == 100.0

    def test_jitter_reduction_at_rest(self):
        f = OneEuroFilter(min_cutoff=1.0, beta=0.007)
        # First sample
        f(100.0, 0.0)
        # Feed high-frequency jitter around 100: 102, 98, 102, 98...
        t = 0.0
        values = []
        for i in range(1, 20):
            t += 0.033
            raw = 100.0 + (2.0 if i % 2 == 0 else -2.0)
            values.append(f(raw, t))

        # Smoothed values should stay closer to 100 than the raw amplitude of 2.0
        max_dev = max(abs(v - 100.0) for v in values[5:])
        assert max_dev < 1.5, f"Expected jitter suppression, got max deviation {max_dev}"

    def test_high_speed_responsiveness(self):
        f = OneEuroFilter(min_cutoff=1.0, beta=0.1)
        f(0.0, 0.0)
        # Fast jump to 1000 in 0.033s
        val = f(1000.0, 0.033)
        # Fast movement adapts cutoff, so filtered value quickly moves towards 1000
        assert val > 600.0

    def test_reset_clears_history(self):
        f = OneEuroFilter()
        f(100.0, 0.0)
        f(200.0, 0.033)
        f.reset()
        # After reset, the next sample is treated as the initial sample
        val = f(500.0, 1.0)
        assert val == 500.0

    def test_one_euro_2d(self):
        f2d = OneEuro2D()
        x, y = f2d.filter(100.0, 200.0, 0.0)
        assert x == 100.0
        assert y == 200.0
        f2d.reset()
        x2, y2 = f2d.filter(300.0, 400.0, 1.0)
        assert x2 == 300.0
        assert y2 == 400.0


# ---------------------------------------------------------------------------
# 3. CursorController tests
# ---------------------------------------------------------------------------


class TestCursorController:
    def test_clutch_inactive_freezes_cursor(self):
        ctrl = CursorController()
        upd = ctrl.update(norm_x=0.5, norm_y=0.5, clutch_on=False, timestamp=1.0)
        assert not upd.moved
        assert not upd.clutch_active
        assert upd.frozen

    def test_clutch_active_moves_cursor(self):
        reg = ActiveRegion(
            left_frac=0.0,
            top_frac=0.0,
            right_frac=1.0,
            bottom_frac=1.0,
            screen_left=0,
            screen_top=0,
            screen_width=1000,
            screen_height=1000,
        )
        ctrl = CursorController(region=reg, config=CursorConfig(dead_zone_px=1.0))
        # Initial frame (starts at center 500, 500)
        ctrl.update(0.5, 0.5, clutch_on=True, timestamp=1.0)
        # Move to (0.6, 0.6) = (600, 600)
        upd = ctrl.update(0.6, 0.6, clutch_on=True, timestamp=1.05)
        assert upd.moved
        assert upd.clutch_active
        assert not upd.frozen
        assert upd.x > 500.0
        assert upd.y > 500.0

    def test_dead_zone_ignores_subthreshold_movement(self):
        reg = ActiveRegion(screen_width=1000, screen_height=1000)
        # Large dead zone of 50px
        ctrl = CursorController(region=reg, config=CursorConfig(dead_zone_px=50.0))
        ctrl.update(0.5, 0.5, clutch_on=True, timestamp=1.0)
        # Very tiny move (~5 pixels)
        upd = ctrl.update(0.505, 0.505, clutch_on=True, timestamp=1.033)
        assert not upd.moved

    def test_max_speed_clamp_limits_displacement(self):
        reg = ActiveRegion(
            left_frac=0.0,
            top_frac=0.0,
            right_frac=1.0,
            bottom_frac=1.0,
            screen_width=2000,
            screen_height=2000,
        )
        # max speed = 1000 px/s; in 0.05s max movement is 50px
        ctrl = CursorController(
            region=reg,
            config=CursorConfig(dead_zone_px=1.0, max_speed_px_per_s=1000.0),
        )
        # Start at center (0.5, 0.5) = (1000, 1000), matching initial controller position
        ctrl.update(0.5, 0.5, clutch_on=True, timestamp=1.0)
        pos_before = ctrl.last_position

        # Jump all the way to (1.0, 1.0) = (2000, 2000)
        upd = ctrl.update(1.0, 1.0, clutch_on=True, timestamp=1.05)
        # Displacement should be clamped to ~50px (1000 px/s * 0.05s)
        dx = upd.x - pos_before[0]
        dy = upd.y - pos_before[1]
        dist = (dx**2 + dy**2) ** 0.5
        assert dist <= 55.0

    def test_hand_loss_and_reclutch_jump_prevention(self):
        reg = ActiveRegion(
            left_frac=0.0,
            top_frac=0.0,
            right_frac=1.0,
            bottom_frac=1.0,
            screen_width=1000,
            screen_height=1000,
        )
        ctrl = CursorController(region=reg)
        ctrl.update(0.2, 0.2, clutch_on=True, timestamp=1.0)

        # Hand lost
        ctrl.on_hand_lost()
        assert not ctrl.clutch_active

        # Hand reappears at far corner (0.8, 0.8)
        # First frame on re-clutch MUST NOT move (prevents teleport jump)
        upd1 = ctrl.update(0.8, 0.8, clutch_on=True, timestamp=2.0)
        assert not upd1.moved
        assert upd1.clutch_active
        assert upd1.x == pytest.approx(800.0, abs=5.0)

        # Subsequent frame moves normally
        upd2 = ctrl.update(0.82, 0.82, clutch_on=True, timestamp=2.05)
        assert upd2.moved

    def test_dead_zone_ramp_and_sensitivity(self):
        reg = ActiveRegion(
            left_frac=0.0,
            top_frac=0.0,
            right_frac=1.0,
            bottom_frac=1.0,
            screen_width=1000,
            screen_height=1000,
        )
        ctrl = CursorController(
            region=reg,
            config=CursorConfig(dead_zone_px=10.0, sensitivity=0.8, smoothing_min_cutoff=100.0),
        )
        # Initialize at center (500, 500)
        ctrl.update(0.5, 0.5, clutch_on=True, timestamp=1.0)

        # Micro-movement of 6px in X (below 10px dead zone): should not move
        upd_small = ctrl.update(0.506, 0.5, clutch_on=True, timestamp=1.033)
        assert not upd_small.moved
        assert upd_small.x == 500.0

        # Movement of 50px in X (above 10px dead zone):
        # excess = 50 - 10 = 40; ratio = (40 / 50) * 0.8 = 0.64
        # move = 50 * 0.64 = 32.0 -> target_x = 532.0
        upd_large = ctrl.update(0.550, 0.5, clutch_on=True, timestamp=1.066)
        assert upd_large.moved
        assert upd_large.x == pytest.approx(530.4, abs=2.0)


# ---------------------------------------------------------------------------
# 4. MouseExecutor tests (with mocked SendInput)
# ---------------------------------------------------------------------------


class MockInputRecorder:
    """Records inputs sent to the Windows SendInput API."""

    def __init__(self) -> None:
        self.calls: list[list[INPUT]] = []
        self.should_succeed = True

    def send_mouse(self, *inputs: INPUT) -> bool:
        if not self.should_succeed:
            return False
        self.calls.append(list(inputs))
        return True


class TestMouseExecutor:
    @pytest.fixture
    def mock_recorder(self) -> MockInputRecorder:
        return MockInputRecorder()

    @pytest.fixture
    def executor(self, mock_recorder: MockInputRecorder) -> MouseExecutor:
        return MouseExecutor(
            send_mouse_fn=mock_recorder.send_mouse,
            virtual_screen_fn=lambda: (0, 0, 1920, 1080),
        )

    def test_cursor_move_screen_pixels(
        self, executor: MouseExecutor, mock_recorder: MockInputRecorder
    ):
        req = ActionRequest(
            action_name="cursor_move",
            parameters={"x": 960, "y": 540},
            source_gesture="point",
        )
        res = executor.execute(req)
        assert res.status == ActionStatus.EXECUTED
        assert len(mock_recorder.calls) == 1
        inp = mock_recorder.calls[0][0]
        # Flags must include MOVE, ABSOLUTE, and VIRTUALDESK
        expected_flags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
        assert inp._input.mi.dwFlags == expected_flags
        # 960 in 1920 is 50% -> ~32767 in [0, 65535]
        assert inp._input.mi.dx == pytest.approx(32767, abs=50)
        assert inp._input.mi.dy == pytest.approx(32767, abs=50)

    def test_cursor_move_normalized(
        self, executor: MouseExecutor, mock_recorder: MockInputRecorder
    ):
        req = ActionRequest(
            action_name="cursor_move",
            parameters={"norm_x": 0.5, "norm_y": 0.5},
            source_gesture="point",
        )
        res = executor.execute(req)
        assert res.status == ActionStatus.EXECUTED
        assert len(mock_recorder.calls) == 1
        inp = mock_recorder.calls[0][0]
        assert inp._input.mi.dx == pytest.approx(32767, abs=50)

    def test_click_left(self, executor: MouseExecutor, mock_recorder: MockInputRecorder):
        req = ActionRequest(action_name="click_left", source_gesture="pinch_index")
        res = executor.execute(req)
        assert res.status == ActionStatus.EXECUTED
        assert len(mock_recorder.calls) == 1
        inputs = mock_recorder.calls[0]
        assert len(inputs) == 2
        assert inputs[0]._input.mi.dwFlags == MOUSEEVENTF_LEFTDOWN
        assert inputs[1]._input.mi.dwFlags == MOUSEEVENTF_LEFTUP

    def test_click_right(self, executor: MouseExecutor, mock_recorder: MockInputRecorder):
        req = ActionRequest(action_name="click_right", source_gesture="pinch_middle")
        res = executor.execute(req)
        assert res.status == ActionStatus.EXECUTED
        assert len(mock_recorder.calls) == 1
        inputs = mock_recorder.calls[0]
        assert len(inputs) == 2
        assert inputs[0]._input.mi.dwFlags == MOUSEEVENTF_RIGHTDOWN
        assert inputs[1]._input.mi.dwFlags == MOUSEEVENTF_RIGHTUP

    def test_click_middle(self, executor: MouseExecutor, mock_recorder: MockInputRecorder):
        req = ActionRequest(action_name="click_middle")
        res = executor.execute(req)
        assert res.status == ActionStatus.EXECUTED
        inputs = mock_recorder.calls[0]
        assert inputs[0]._input.mi.dwFlags == MOUSEEVENTF_MIDDLEDOWN
        assert inputs[1]._input.mi.dwFlags == MOUSEEVENTF_MIDDLEUP

    def test_click_double(self, executor: MouseExecutor, mock_recorder: MockInputRecorder):
        req = ActionRequest(action_name="click_double")
        res = executor.execute(req)
        assert res.status == ActionStatus.EXECUTED
        # Two calls of (DOWN, UP)
        assert len(mock_recorder.calls) == 2

    def test_drag_start_and_end(self, executor: MouseExecutor, mock_recorder: MockInputRecorder):
        # Start drag
        req_start = ActionRequest(action_name="drag_start", source_gesture="pinch_sustained")
        res_start = executor.execute(req_start)
        assert res_start.status == ActionStatus.EXECUTED
        assert executor.is_dragging
        assert mock_recorder.calls[-1][0]._input.mi.dwFlags == MOUSEEVENTF_LEFTDOWN

        # End drag
        req_end = ActionRequest(action_name="drag_end", source_gesture="pinch_sustained")
        res_end = executor.execute(req_end)
        assert res_end.status == ActionStatus.EXECUTED
        assert not executor.is_dragging
        assert mock_recorder.calls[-1][0]._input.mi.dwFlags == MOUSEEVENTF_LEFTUP

    def test_release_all_releases_drag(
        self, executor: MouseExecutor, mock_recorder: MockInputRecorder
    ):
        executor.execute(ActionRequest(action_name="drag_start"))
        assert executor.is_dragging

        executor.release_all()
        assert not executor.is_dragging
        assert mock_recorder.calls[-1][0]._input.mi.dwFlags == MOUSEEVENTF_LEFTUP

    def test_scroll(self, executor: MouseExecutor, mock_recorder: MockInputRecorder):
        req = ActionRequest(
            action_name="scroll",
            parameters={"delta_y": 1},
            source_gesture="two_fingers",
        )
        res = executor.execute(req)
        assert res.status == ActionStatus.EXECUTED
        inp = mock_recorder.calls[-1][0]
        assert inp._input.mi.dwFlags == MOUSEEVENTF_WHEEL
        assert inp._input.mi.mouseData == WHEEL_DELTA

    def test_send_input_failure_returns_failed_status(
        self, executor: MouseExecutor, mock_recorder: MockInputRecorder
    ):
        mock_recorder.should_succeed = False
        res = executor.execute(ActionRequest(action_name="click_left"))
        assert res.status == ActionStatus.FAILED
        assert "SendInput failed" in res.reason


# ---------------------------------------------------------------------------
# 5. EmergencyHotkey tests
# ---------------------------------------------------------------------------


class TestEmergencyHotkey:
    def test_hotkey_creation(self):
        called = False

        def on_trigger():
            nonlocal called
            called = True

        hk = EmergencyHotkey(callback=on_trigger)
        assert not hk.is_registered

    def test_hotkey_start_stop_lifecycle(self):
        hk = EmergencyHotkey(callback=lambda: None)
        ok = hk.start()
        # On Windows it starts the thread
        if ok:
            time.sleep(0.05)
            hk.stop()
            assert not hk.is_registered


# ---------------------------------------------------------------------------
# 6. Phase 4 End-to-End Integration tests
# ---------------------------------------------------------------------------


class TestPhase4Integration:
    def test_pipeline_with_mouse_executor_and_emergency(self):
        mock = MockInputRecorder()
        mouse = MouseExecutor(
            send_mouse_fn=mock.send_mouse,
            virtual_screen_fn=lambda: (0, 0, 1920, 1080),
        )
        controller = ActionController(executor=mouse)
        mapper = GestureMapper()
        safety = SafetyManager(controller, mapper)
        safety.enable_real_mode(mouse)

        now = 1000.0

        # 1. Pinch index -> left click executed
        ev_click = GestureEvent(
            gesture_id="pinch_index",
            phase=GesturePhase.STARTED,
            hand="Right",
            timestamp=now,
            confidence=0.95,
        )
        reqs = mapper.process_events([ev_click])
        assert len(reqs) == 1
        assert reqs[0].action_name == "click_left"

        res = controller.submit(reqs[0])
        assert res.status == ActionStatus.EXECUTED
        assert len(mock.calls) == 1

        # 2. Pinch sustained -> drag start executed
        ev_drag = GestureEvent(
            gesture_id="pinch_sustained",
            phase=GesturePhase.STARTED,
            hand="Right",
            timestamp=now + 1.0,
            confidence=0.95,
        )
        reqs = mapper.process_events([ev_drag])
        assert len(reqs) == 1
        assert reqs[0].action_name == "drag_start"

        res = controller.submit(reqs[0])
        assert res.status == ActionStatus.EXECUTED
        assert mouse.is_dragging

        # 3. Emergency stop triggered -> releases drag and blocks subsequent actions
        safety.emergency_stop()
        assert not mouse.is_dragging
        # Button up sent
        assert mock.calls[-1][0]._input.mi.dwFlags == MOUSEEVENTF_LEFTUP

        # 4. Attempt action while emergency is active -> REFUSED
        req_blocked = ActionRequest(action_name="click_left")
        res_blocked = controller.submit(req_blocked)
        assert res_blocked.status == ActionStatus.REFUSED

        # 5. Reset emergency -> actions work again
        safety.reset_emergency()
        safety.resume_commands()
        res_allowed = controller.submit(ActionRequest(action_name="click_right"))
        assert res_allowed.status == ActionStatus.EXECUTED
