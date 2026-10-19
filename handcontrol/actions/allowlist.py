"""Action allowlist (Section 4.7 + Section 5).

Defines every action the system is allowed to execute.  Any action not on
this list is REFUSED and logged.  Parameter validation happens here so that
executors receive only well-formed requests.

This module is independent of UI, gestures, and Windows.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ActionSpec:
    """Specification for a single allowed action.

    Attributes:
        name: Unique action identifier (used in profiles and allowlist).
        description: Human-readable description.
        required_params: Parameter names that must be present.
        optional_params: Parameter names that may be present.
        is_stateful: If True, the action has a held state (drag, window move).
    """

    name: str
    description: str
    required_params: frozenset[str] = field(default_factory=frozenset)
    optional_params: frozenset[str] = field(default_factory=frozenset)
    is_stateful: bool = False


# ---------------------------------------------------------------------------
# Default allowlist — all actions available in v1 (Section 7 + Section 5)
# ---------------------------------------------------------------------------

_ALL_ACTIONS: list[ActionSpec] = [
    # --- Mouse actions ---
    ActionSpec(
        name="click_left",
        description="Single left mouse button click.",
    ),
    ActionSpec(
        name="click_right",
        description="Single right mouse button click.",
    ),
    ActionSpec(
        name="click_middle",
        description="Single middle mouse button click.",
    ),
    ActionSpec(
        name="drag_start",
        description="Press and hold left mouse button (begin drag).",
        is_stateful=True,
    ),
    ActionSpec(
        name="drag_end",
        description="Release left mouse button (end drag).",
    ),
    ActionSpec(
        name="scroll",
        description="Scroll the mouse wheel.",
        required_params=frozenset({"delta_y"}),
        optional_params=frozenset({"delta_x"}),
    ),
    ActionSpec(
        name="cursor_move",
        description="Move the cursor to a normalized position.",
        required_params=frozenset({"norm_x", "norm_y"}),
    ),
    # --- Window management ---
    ActionSpec(
        name="window_grab",
        description="Grab the active window for movement.",
        is_stateful=True,
    ),
    ActionSpec(
        name="window_release",
        description="Release the grabbed window.",
    ),
    # --- System / media ---
    ActionSpec(
        name="pause_commands",
        description="Toggle pause of all gesture-to-action commands.",
    ),
    # --- Simulation / safety ---
    ActionSpec(
        name="noop",
        description="No-operation placeholder (used in tests and simulation).",
    ),
]

# Build lookup dict at module load time.
_BY_NAME: dict[str, ActionSpec] = {spec.name: spec for spec in _ALL_ACTIONS}


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


class Allowlist:
    """Validates action requests against the allowlist.

    Instantiate with the default actions or supply a custom list for testing.
    """

    def __init__(self, specs: list[ActionSpec] | None = None) -> None:
        if specs is None:
            self._specs = dict(_BY_NAME)
        else:
            self._specs = {s.name: s for s in specs}

    @property
    def action_names(self) -> list[str]:
        """Sorted list of all allowed action names."""
        return sorted(self._specs.keys())

    def is_allowed(self, action_name: str) -> bool:
        """Return True if the action is on the allowlist."""
        return action_name in self._specs

    def get_spec(self, action_name: str) -> ActionSpec | None:
        """Return the spec for an action, or None if not allowed."""
        return self._specs.get(action_name)

    def validate(self, action_name: str, parameters: dict) -> list[str]:
        """Validate an action name and its parameters.

        Returns:
            List of validation error strings (empty list = valid).
        """
        spec = self._specs.get(action_name)
        if spec is None:
            return [f"Action '{action_name}' is not on the allowlist."]

        errors: list[str] = []
        for req in spec.required_params:
            if req not in parameters:
                errors.append(f"Missing required parameter '{req}' for action '{action_name}'.")

        all_known = spec.required_params | spec.optional_params
        for key in parameters:
            if all_known and key not in all_known:
                errors.append(f"Unknown parameter '{key}' for action '{action_name}'.")

        return errors
