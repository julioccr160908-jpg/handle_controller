"""Tests for Phase 3 — Mapping, ActionController, Allowlist, SafetyManager.

Covers:
- Gestures generate ActionResult with status SIMULATED.
- Actions not on the allowlist are REFUSED and logged.
- Per-rule cooldown prevents re-triggering.
- Repeat limit allows only N activations per activation.
- Priority resolution (highest wins on conflict).
- Pause state suppresses commands (except pause_commands itself).
- Emergency stop blocks all actions and is recorded in history.
- Emergency stop calls release_all on the executor.
- Full reset clears history and state.
- SimulatedExecutor logs all calls.

No UI, no Windows, no camera required.
"""

from __future__ import annotations

from handcontrol.actions.allowlist import Allowlist
from handcontrol.actions.controller import ActionController
from handcontrol.actions.simulation import SimulatedExecutor
from handcontrol.mapping.profile import GestureMapper, MappingRule
from handcontrol.safety.manager import SafetyManager
from handcontrol.tracking.models import (
    ActionRequest,
    ActionStatus,
    GestureEvent,
    GesturePhase,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

T0 = 1_000.0  # base timestamp for tests


def _ev(
    gesture_id: str = "pinch_index",
    phase: GesturePhase = GesturePhase.STARTED,
    hand: str = "Right",
    timestamp: float = T0,
) -> GestureEvent:
    return GestureEvent(
        gesture_id=gesture_id,
        phase=phase,
        hand=hand,
        timestamp=timestamp,
        confidence=0.99,
    )


def _req(action: str = "click_left", params: dict | None = None) -> ActionRequest:
    return ActionRequest(
        action_name=action,
        parameters=params or {},
        source_gesture="pinch_index",
    )


# ---------------------------------------------------------------------------
# Allowlist tests
# ---------------------------------------------------------------------------


class TestAllowlist:
    def test_known_actions_are_allowed(self):
        al = Allowlist()
        for name in ("click_left", "click_right", "scroll", "noop"):
            assert al.is_allowed(name), f"'{name}' should be allowed"

    def test_unknown_action_not_allowed(self):
        al = Allowlist()
        assert not al.is_allowed("rm_rf_slash")

    def test_validate_allowed_action_no_errors(self):
        al = Allowlist()
        errors = al.validate("click_left", {})
        assert errors == []

    def test_validate_scroll_requires_delta_y(self):
        al = Allowlist()
        errors = al.validate("scroll", {})
        assert any("delta_y" in e for e in errors)

    def test_validate_scroll_with_delta_y_ok(self):
        al = Allowlist()
        errors = al.validate("scroll", {"delta_y": -3})
        assert errors == []

    def test_validate_unknown_action_returns_error(self):
        al = Allowlist()
        errors = al.validate("rm_rf_slash", {})
        assert len(errors) == 1
        assert "allowlist" in errors[0].lower()

    def test_action_names_sorted(self):
        al = Allowlist()
        names = al.action_names
        assert names == sorted(names)


# ---------------------------------------------------------------------------
# SimulatedExecutor tests
# ---------------------------------------------------------------------------


class TestSimulatedExecutor:
    def test_returns_simulated_status(self):
        ex = SimulatedExecutor()
        result = ex.execute(_req())
        assert result.status == ActionStatus.SIMULATED

    def test_logs_are_recorded(self):
        ex = SimulatedExecutor()
        ex.execute(_req("click_left"))
        ex.execute(_req("click_right"))
        assert len(ex.log) == 2
        assert ex.log[0].action_name == "click_left"
        assert ex.log[1].action_name == "click_right"

    def test_clear_log(self):
        ex = SimulatedExecutor()
        ex.execute(_req())
        ex.clear_log()
        assert ex.log == []

    def test_release_all_is_noop(self):
        ex = SimulatedExecutor()
        ex.release_all()  # Should not raise.

    def test_action_name_preserved(self):
        ex = SimulatedExecutor()
        result = ex.execute(_req("window_grab"))
        assert result.action_name == "window_grab"


# ---------------------------------------------------------------------------
# ActionController tests
# ---------------------------------------------------------------------------


class TestActionController:
    def test_allowed_action_returns_simulated(self):
        ctrl = ActionController()
        result = ctrl.submit(_req("click_left"))
        assert result.status == ActionStatus.SIMULATED

    def test_unknown_action_is_refused(self):
        ctrl = ActionController()
        result = ctrl.submit(_req("rm_rf_slash"))
        assert result.status == ActionStatus.REFUSED
        assert "allowlist" in result.reason.lower()

    def test_refused_action_recorded_in_history(self):
        ctrl = ActionController()
        ctrl.submit(_req("rm_rf_slash"))
        hist = ctrl.history
        assert len(hist) == 1
        assert hist[0].status == ActionStatus.REFUSED

    def test_history_accumulates_results(self):
        ctrl = ActionController()
        ctrl.submit(_req("click_left"))
        ctrl.submit(_req("click_right"))
        ctrl.submit(_req("noop"))
        assert len(ctrl.history) == 3

    def test_emergency_stop_blocks_actions(self):
        ctrl = ActionController()
        ctrl.emergency_stop()
        result = ctrl.submit(_req("click_left"))
        assert result.status == ActionStatus.REFUSED
        assert "emergency" in result.reason.lower()

    def test_emergency_stop_calls_release_all(self):
        ex = SimulatedExecutor()
        release_called = []

        original_release = ex.release_all

        def mock_release():
            release_called.append(True)
            original_release()

        ex.release_all = mock_release

        ctrl = ActionController(executor=ex)
        ctrl.emergency_stop()
        assert len(release_called) == 1

    def test_reset_emergency_allows_actions_again(self):
        ctrl = ActionController()
        ctrl.emergency_stop()
        ctrl.reset_emergency()
        result = ctrl.submit(_req("click_left"))
        assert result.status == ActionStatus.SIMULATED

    def test_reset_clears_history(self):
        ctrl = ActionController()
        ctrl.submit(_req("click_left"))
        ctrl.reset()
        assert ctrl.history == []

    def test_history_is_bounded(self):
        ctrl = ActionController(history_maxlen=5)
        for _ in range(10):
            ctrl.submit(_req("noop"))
        assert len(ctrl.history) == 5

    def test_missing_required_param_is_refused(self):
        ctrl = ActionController()
        result = ctrl.submit(_req("scroll", {}))  # scroll needs delta_y
        assert result.status == ActionStatus.REFUSED
        assert "delta_y" in result.reason


# ---------------------------------------------------------------------------
# GestureMapper tests
# ---------------------------------------------------------------------------


class TestGestureMapper:
    def test_default_profile_loads(self):
        mapper = GestureMapper()
        assert mapper is not None

    def test_pinch_index_maps_to_click_left(self):
        mapper = GestureMapper()
        ev = _ev("pinch_index", GesturePhase.STARTED, "Right", T0)
        requests = mapper.process_event(ev)
        assert len(requests) == 1
        assert requests[0].action_name == "click_left"

    def test_pinch_middle_maps_to_click_right(self):
        mapper = GestureMapper()
        ev = _ev("pinch_middle", GesturePhase.STARTED, "Right", T0)
        requests = mapper.process_event(ev)
        assert len(requests) == 1
        assert requests[0].action_name == "click_right"

    def test_unmapped_gesture_returns_empty(self):
        mapper = GestureMapper()
        ev = _ev("unknown_gesture", GesturePhase.STARTED, "Right", T0)
        requests = mapper.process_event(ev)
        assert requests == []

    def test_cooldown_prevents_second_activation(self):
        rules = [
            MappingRule(
                gesture_id="pinch_index",
                action_name="click_left",
                hand="Any",
                cooldown_s=1.0,
            )
        ]
        mapper = GestureMapper(rules=rules)
        ev1 = _ev("pinch_index", GesturePhase.STARTED, "Right", T0)
        ev2 = _ev("pinch_index", GesturePhase.STARTED, "Right", T0 + 0.5)

        r1 = mapper.process_event(ev1)
        r2 = mapper.process_event(ev2)
        assert len(r1) == 1
        assert len(r2) == 0  # still in cooldown

    def test_cooldown_expires(self):
        rules = [
            MappingRule(
                gesture_id="pinch_index",
                action_name="click_left",
                hand="Any",
                cooldown_s=0.5,
            )
        ]
        mapper = GestureMapper(rules=rules)
        ev1 = _ev("pinch_index", GesturePhase.STARTED, "Right", T0)
        ev2 = _ev("pinch_index", GesturePhase.STARTED, "Right", T0 + 0.6)

        r1 = mapper.process_event(ev1)
        r2 = mapper.process_event(ev2)
        assert len(r1) == 1
        assert len(r2) == 1  # cooldown expired

    def test_repeat_limit_of_one(self):
        rules = [
            MappingRule(
                gesture_id="pinch_index",
                action_name="click_left",
                hand="Any",
                cooldown_s=0.0,
                repeat_limit=1,
            )
        ]
        mapper = GestureMapper(rules=rules)
        ev1 = _ev("pinch_index", GesturePhase.STARTED, "Right", T0)
        ev2 = _ev("pinch_index", GesturePhase.STARTED, "Right", T0 + 0.01)

        r1 = mapper.process_event(ev1)
        r2 = mapper.process_event(ev2)
        assert len(r1) == 1
        assert len(r2) == 0

    def test_priority_higher_wins(self):
        rules = [
            MappingRule(
                gesture_id="test",
                action_name="click_left",
                hand="Any",
                priority=5,
            ),
            MappingRule(
                gesture_id="test",
                action_name="click_right",
                hand="Any",
                priority=10,
            ),
        ]
        mapper = GestureMapper(rules=rules)
        ev = _ev("test", GesturePhase.STARTED, "Right", T0)
        requests = mapper.process_event(ev)
        assert len(requests) == 1
        assert requests[0].action_name == "click_right"

    def test_commands_paused_suppresses_action(self):
        mapper = GestureMapper()
        mapper.commands_paused = True
        ev = _ev("pinch_index", GesturePhase.STARTED, "Right", T0)
        requests = mapper.process_event(ev)
        assert requests == []

    def test_pause_commands_action_bypasses_pause(self):
        rules = [
            MappingRule(
                gesture_id="two_open_palms",
                action_name="pause_commands",
                hand="Both",
            )
        ]
        mapper = GestureMapper(rules=rules)
        mapper.commands_paused = True
        ev = _ev("two_open_palms", GesturePhase.STARTED, "Both", T0)
        requests = mapper.process_event(ev)
        assert len(requests) == 1

    def test_hand_filter_right_only(self):
        rules = [
            MappingRule(
                gesture_id="pinch_index",
                action_name="click_left",
                hand="Right",
            )
        ]
        mapper = GestureMapper(rules=rules)
        ev_right = _ev("pinch_index", GesturePhase.STARTED, "Right", T0)
        ev_left = _ev("pinch_index", GesturePhase.STARTED, "Left", T0)
        assert len(mapper.process_event(ev_right)) == 1
        assert len(mapper.process_event(ev_left)) == 0

    def test_triggered_phase_filter(self):
        rules = [
            MappingRule(
                gesture_id="fist",
                action_name="window_release",
                hand="Any",
                trigger_phase=GesturePhase.ENDED,
            )
        ]
        mapper = GestureMapper(rules=rules)
        ev_started = _ev("fist", GesturePhase.STARTED, "Left", T0)
        ev_ended = _ev("fist", GesturePhase.ENDED, "Left", T0 + 0.5)
        assert len(mapper.process_event(ev_started)) == 0
        assert len(mapper.process_event(ev_ended)) == 1

    def test_disabled_rule_skipped(self):
        rules = [
            MappingRule(
                gesture_id="pinch_index",
                action_name="click_left",
                hand="Any",
                enabled=False,
            )
        ]
        mapper = GestureMapper(rules=rules)
        ev = _ev("pinch_index", GesturePhase.STARTED, "Right", T0)
        assert len(mapper.process_event(ev)) == 0

    def test_source_gesture_set_in_request(self):
        mapper = GestureMapper()
        ev = _ev("pinch_index", GesturePhase.STARTED, "Right", T0)
        requests = mapper.process_event(ev)
        assert requests[0].source_gesture == "pinch_index"


# ---------------------------------------------------------------------------
# SafetyManager tests
# ---------------------------------------------------------------------------


class TestSafetyManager:
    def _make(self):
        ctrl = ActionController()
        mapper = GestureMapper()
        safety = SafetyManager(ctrl, mapper)
        return safety, ctrl, mapper

    def test_initial_simulation_mode(self):
        safety, _, _ = self._make()
        assert safety.simulation_mode is True

    def test_pause_suppresses_mapper(self):
        safety, ctrl, mapper = self._make()
        safety.pause_commands()
        assert mapper.commands_paused is True

    def test_resume_enables_mapper(self):
        safety, ctrl, mapper = self._make()
        safety.pause_commands()
        safety.resume_commands()
        assert mapper.commands_paused is False

    def test_toggle_pause(self):
        safety, ctrl, mapper = self._make()
        state = safety.toggle_pause()
        assert state is True
        assert mapper.commands_paused is True
        state = safety.toggle_pause()
        assert state is False

    def test_emergency_stop_blocks_controller(self):
        safety, ctrl, mapper = self._make()
        safety.emergency_stop()
        assert ctrl.is_emergency is True
        assert mapper.commands_paused is True

    def test_reset_emergency_clears_controller(self):
        safety, ctrl, mapper = self._make()
        safety.emergency_stop()
        safety.reset_emergency()
        assert ctrl.is_emergency is False

    def test_full_reset_clears_all(self):
        safety, ctrl, mapper = self._make()
        safety.emergency_stop()
        ctrl.submit(_req("click_left"))  # this will be refused
        safety.full_reset()
        assert ctrl.is_emergency is False
        assert ctrl.history == []
        assert mapper.commands_paused is False


# ---------------------------------------------------------------------------
# End-to-end: gesture → mapping → controller → SIMULATED
# ---------------------------------------------------------------------------


class TestEndToEnd:
    def test_pinch_generates_simulated_click(self):
        mapper = GestureMapper()
        ctrl = ActionController()

        ev = _ev("pinch_index", GesturePhase.STARTED, "Right", T0)
        requests = mapper.process_events([ev])
        results = ctrl.submit_many(requests)

        assert len(results) == 1
        assert results[0].status == ActionStatus.SIMULATED
        assert results[0].action_name == "click_left"

    def test_unknown_action_in_profile_is_refused(self):
        """If a profile maps a gesture to a non-allowlisted action, it's refused."""
        rules = [
            MappingRule(
                gesture_id="pinch_index",
                action_name="rm_rf_slash",  # not on allowlist
                hand="Any",
            )
        ]
        mapper = GestureMapper(rules=rules)
        ctrl = ActionController()

        ev = _ev("pinch_index", GesturePhase.STARTED, "Right", T0)
        requests = mapper.process_events([ev])
        results = ctrl.submit_many(requests)

        assert len(results) == 1
        assert results[0].status == ActionStatus.REFUSED

    def test_full_pipeline_records_in_history(self):
        mapper = GestureMapper()
        ctrl = ActionController()

        events = [
            _ev("pinch_index", GesturePhase.STARTED, "Right", T0),
            _ev("pinch_middle", GesturePhase.STARTED, "Right", T0 + 0.5),
        ]
        requests = mapper.process_events(events)
        ctrl.submit_many(requests)

        hist = ctrl.history
        assert len(hist) == 2
        names = {r.action_name for r in hist}
        assert "click_left" in names
        assert "click_right" in names
