"""Gesture-to-action mapping profile (Section 4.7 + Section 5).

A ``MappingProfile`` is a collection of ``MappingRule`` objects that associate
a gesture ID + hand combination with a specific allowed action.

Rules carry:
- Priority (higher wins when multiple gestures match simultaneously).
- Per-rule cooldown (min time between successive activations of the same rule).
- Repeat limit (max activations per gesture activation; 0 = unlimited).
- Whether the rule is enabled.

The mapper is independent of UI, executors, and Windows.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from handcontrol.tracking.models import ActionRequest, GestureEvent, GesturePhase

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Rule data structure
# ---------------------------------------------------------------------------


@dataclass
class MappingRule:
    """Associates a gesture with an action.

    Attributes:
        gesture_id: ID of the gesture that triggers this rule.
        action_name: Name of the action from the allowlist.
        hand: "Left", "Right", "Both", or "Any".
        priority: Higher value = higher priority in conflict resolution.
        cooldown_s: Min seconds between successive activations of this rule.
        repeat_limit: Max times action fires per gesture activation (0 = unlimited).
        enabled: If False, this rule is skipped.
        parameters: Static extra parameters forwarded to the action.
        trigger_phase: GesturePhase that triggers the action (default STARTED).
    """

    gesture_id: str
    action_name: str
    hand: str = "Any"
    priority: int = 0
    cooldown_s: float = 0.0
    repeat_limit: int = 0
    enabled: bool = True
    parameters: dict = field(default_factory=dict)
    trigger_phase: GesturePhase = GesturePhase.STARTED


# ---------------------------------------------------------------------------
# Default profile (Section 5 table)
# ---------------------------------------------------------------------------


def default_profile() -> list[MappingRule]:
    """Return the default gesture-to-action mapping (Section 5)."""
    return [
        # Pointing (clutch) → cursor move is handled by the cursor controller in Phase 4.
        # Registered here with noop so the gesture exists in the profile.
        MappingRule(
            gesture_id="point",
            action_name="noop",
            hand="Right",
            priority=1,
            cooldown_s=0.0,
            trigger_phase=GesturePhase.STARTED,
        ),
        # Pinch index → left click (single event per activation).
        MappingRule(
            gesture_id="pinch_index",
            action_name="click_left",
            hand="Right",
            priority=10,
            cooldown_s=0.15,
            repeat_limit=1,
            trigger_phase=GesturePhase.STARTED,
        ),
        # Pinch middle → right click.
        MappingRule(
            gesture_id="pinch_middle",
            action_name="click_right",
            hand="Right",
            priority=10,
            cooldown_s=0.15,
            repeat_limit=1,
            trigger_phase=GesturePhase.STARTED,
        ),
        # Pinch sustained → drag (started = drag_start, ended/cancelled = drag_end).
        MappingRule(
            gesture_id="pinch_sustained",
            action_name="drag_start",
            hand="Right",
            priority=12,
            cooldown_s=0.10,
            repeat_limit=1,
            trigger_phase=GesturePhase.STARTED,
        ),
        MappingRule(
            gesture_id="pinch_sustained",
            action_name="drag_end",
            hand="Right",
            priority=12,
            cooldown_s=0.0,
            trigger_phase=GesturePhase.ENDED,
        ),
        MappingRule(
            gesture_id="pinch_sustained",
            action_name="drag_end",
            hand="Right",
            priority=12,
            cooldown_s=0.0,
            trigger_phase=GesturePhase.CANCELLED,
        ),
        # Two fingers (held) → vertical scroll.
        MappingRule(
            gesture_id="two_fingers",
            action_name="scroll",
            hand="Right",
            priority=2,
            cooldown_s=0.08,
            trigger_phase=GesturePhase.HELD,
        ),
        # Fist left → window grab (stateful: hold to move).
        MappingRule(
            gesture_id="fist",
            action_name="window_grab",
            hand="Left",
            priority=5,
            cooldown_s=0.1,
            trigger_phase=GesturePhase.STARTED,
        ),
        MappingRule(
            gesture_id="fist",
            action_name="window_release",
            hand="Left",
            priority=5,
            cooldown_s=0.0,
            trigger_phase=GesturePhase.ENDED,
        ),
        # Two open palms → toggle pause commands.
        MappingRule(
            gesture_id="two_open_palms",
            action_name="pause_commands",
            hand="Both",
            priority=20,
            cooldown_s=0.5,
            repeat_limit=1,
            trigger_phase=GesturePhase.STARTED,
        ),
    ]


# ---------------------------------------------------------------------------
# Mapper
# ---------------------------------------------------------------------------


class GestureMapper:
    """Resolves ``GestureEvent`` objects into ``ActionRequest`` objects.

    Responsibilities:
    1. Consult the active profile to find matching rules.
    2. Apply priority (highest wins on conflict).
    3. Enforce per-rule cooldowns.
    4. Enforce repeat limits per activation.
    5. Check global commands-paused state.
    6. Return an ``ActionRequest`` or None (no match / refused).

    Independent of executors, UI, and Windows.
    """

    def __init__(self, rules: list[MappingRule] | None = None) -> None:
        self._rules = rules if rules is not None else default_profile()
        # State tracking: last activation time per rule (gesture_id, hand, action).
        self._last_fired: dict[tuple[str, str, str], float] = {}
        # Repeat count per active gesture activation (gesture_id, hand) → count.
        self._repeat_count: dict[tuple[str, str], int] = {}
        self._commands_paused = False

    # ---------------------------------------------------------------- public

    @property
    def commands_paused(self) -> bool:
        """Whether gesture commands are currently paused."""
        return self._commands_paused

    @commands_paused.setter
    def commands_paused(self, value: bool) -> None:
        self._commands_paused = value

    def process_event(self, event: GestureEvent) -> list[ActionRequest]:
        """Convert a gesture event into zero or more action requests.

        Args:
            event: A ``GestureEvent`` emitted by the gesture engine.

        Returns:
            List of ``ActionRequest`` objects to be passed to the controller.
        """
        # Find all matching, enabled rules for this event.
        candidates: list[MappingRule] = []
        for rule in self._rules:
            if not rule.enabled:
                continue
            if rule.gesture_id != event.gesture_id:
                continue
            if rule.trigger_phase != event.phase:
                continue
            if rule.hand not in ("Any", event.hand, "Both"):
                continue
            candidates.append(rule)

        if not candidates:
            return []

        # Sort by priority descending; take the highest-priority rule.
        candidates.sort(key=lambda r: r.priority, reverse=True)
        rule = candidates[0]

        # Check global pause (bypass for pause_commands action itself).
        if self._commands_paused and rule.action_name != "pause_commands":
            logger.debug("Commands paused — gesture '%s' suppressed.", event.gesture_id)
            return []

        now = event.timestamp
        key = (rule.gesture_id, event.hand, rule.action_name)

        # Per-rule cooldown check.
        last = self._last_fired.get(key, 0.0)
        if rule.cooldown_s > 0 and (now - last) < rule.cooldown_s:
            logger.debug(
                "Rule '%s → %s' in cooldown (%.3fs remaining).",
                rule.gesture_id,
                rule.action_name,
                rule.cooldown_s - (now - last),
            )
            return []

        # Repeat limit check.
        repeat_key = (rule.gesture_id, event.hand)
        if rule.repeat_limit > 0:
            count = self._repeat_count.get(repeat_key, 0)
            if count >= rule.repeat_limit:
                logger.debug(
                    "Rule '%s → %s' repeat limit reached (%d).",
                    rule.gesture_id,
                    rule.action_name,
                    rule.repeat_limit,
                )
                return []
            self._repeat_count[repeat_key] = count + 1
        else:
            # Reset on each activation.
            self._repeat_count[repeat_key] = 1

        # Build request.
        params = dict(rule.parameters)
        params.setdefault("hand", event.hand)
        if rule.action_name == "scroll" and "delta_y" not in params:
            vy = event.payload.get("vy", 0.0)
            if abs(vy) > 0.03:
                params["delta_y"] = 1 if vy < 0 else -1
            else:
                return []
        self._last_fired[key] = now

        request = ActionRequest(
            action_name=rule.action_name,
            parameters=params,
            source_gesture=event.gesture_id,
        )
        logger.debug(
            "Mapped '%s' [%s] → '%s'",
            event.gesture_id,
            event.hand,
            rule.action_name,
        )
        return [request]

    def process_events(self, events: list[GestureEvent]) -> list[ActionRequest]:
        """Process a list of gesture events into action requests."""
        requests: list[ActionRequest] = []
        for ev in events:
            requests.extend(self.process_event(ev))
        return requests

    def reset_gesture(self, gesture_id: str, hand: str) -> None:
        """Reset repeat counter when a gesture ends/is cancelled."""
        self._repeat_count.pop((gesture_id, hand), None)

    def reset_all(self) -> None:
        """Reset all internal state (call on stop / emergency)."""
        self._last_fired.clear()
        self._repeat_count.clear()

    def load_profile_rules(self, rules: list[MappingRule]) -> None:
        """Replace active mapping rules with a new set."""
        self._rules = list(rules)
        self.reset_all()

    def load_from_profile_model(self, profile: object) -> None:
        """Load and convert rules from a ProfileModel."""
        from handcontrol.config.models import ProfileModel

        if not isinstance(profile, ProfileModel):
            return

        converted: list[MappingRule] = []
        for m in profile.mappings:
            phase = (
                GesturePhase(m.trigger_phase)
                if m.trigger_phase in GesturePhase._value2member_map_
                else GesturePhase.STARTED
            )
            converted.append(
                MappingRule(
                    gesture_id=m.gesture_id,
                    action_name=m.action_name,
                    hand=m.hand,
                    priority=m.priority,
                    cooldown_s=m.cooldown_s,
                    repeat_limit=m.repeat_limit,
                    enabled=m.enabled,
                    parameters=m.parameters,
                    trigger_phase=phase,
                )
            )
        self.load_profile_rules(converted)
