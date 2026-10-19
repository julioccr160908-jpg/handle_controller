"""Geometric rules for gesture recognition (Section 5 + Section 4.6).

Each rule function takes ``HandFeatures`` (and optionally a second hand's
features) and returns ``True`` if the geometric condition is satisfied.

Rules are independent of timing, state machines, UI, mapping, and Windows.
The gesture engine uses them as the ``matched`` input to the state machine.
"""

from __future__ import annotations

from handcontrol.tracking.models import FingerState, HandFeatures

# ---------------------------------------------------------------------------
# Helper — checks finger patterns
# ---------------------------------------------------------------------------


def _fingers_match(
    features: HandFeatures,
    expected: list[FingerState | None],
) -> bool:
    """Check if the finger states match the expected pattern.

    ``None`` in the pattern means "don't care" (any state is OK).
    """
    if not features.is_valid or len(features.fingers_extended) != 5:
        return False

    for actual, exp in zip(features.fingers_extended, expected, strict=False):
        if exp is not None and actual != exp:
            return False
    return True


# Shorthands.
E = FingerState.EXTENDED
F = FingerState.FOLDED
_ = None  # don't care


# ---------------------------------------------------------------------------
# Static gesture rules (Section 5)
# ---------------------------------------------------------------------------


def rule_point(features: HandFeatures) -> bool:
    """Pointing / clutch: index extended, all others folded.

    Thumb is "don't care" — many people can't fully fold it while pointing.
    """
    return _fingers_match(features, [_, E, F, F, F])


def rule_fist(features: HandFeatures) -> bool:
    """Fist: all five fingers folded."""
    return _fingers_match(features, [F, F, F, F, F])


def rule_open_palm(features: HandFeatures) -> bool:
    """Open palm: all five fingers extended."""
    return _fingers_match(features, [E, E, E, E, E])


def rule_two_fingers(features: HandFeatures) -> bool:
    """Two fingers (index + middle): both extended, others folded.

    Thumb is "don't care".
    """
    return _fingers_match(features, [_, E, E, F, F])


def rule_pinch_index(
    features: HandFeatures,
    enter_threshold: float,
    exit_threshold: float,
    currently_active: bool,
) -> bool:
    """Pinch index: thumb tip close to index tip.

    Uses hysteresis: ``enter_threshold`` to activate, ``exit_threshold``
    to deactivate (exit > enter).

    Args:
        features: Computed hand features.
        enter_threshold: Normalized distance to trigger pinch.
        exit_threshold: Normalized distance to release pinch.
        currently_active: Whether the pinch is currently detected.

    Returns:
        True if pinch condition is satisfied.
    """
    if not features.is_valid:
        return False

    dist = features.normalized_distances.get("thumb_index", 999.0)

    if currently_active:
        return dist < exit_threshold
    else:
        return dist < enter_threshold


def rule_pinch_middle(
    features: HandFeatures,
    enter_threshold: float,
    exit_threshold: float,
    currently_active: bool,
) -> bool:
    """Pinch middle: thumb tip close to middle tip.

    Same hysteresis logic as ``rule_pinch_index``.
    """
    if not features.is_valid:
        return False

    dist = features.normalized_distances.get("thumb_middle", 999.0)

    if currently_active:
        return dist < exit_threshold
    else:
        return dist < enter_threshold


def rule_two_open_palms(
    features_left: HandFeatures | None,
    features_right: HandFeatures | None,
) -> bool:
    """Two open palms: both hands present and fully open."""
    if features_left is None or features_right is None:
        return False
    return rule_open_palm(features_left) and rule_open_palm(features_right)
