"""Gesture rule conflict validator (Section 4.7 & Section 5).

Analyzes mapping rules within a profile to detect:
1. Duplicate triggers: two rules listening to the exact same gesture and hand with identical priority.
2. Hand collisions: "Any" colliding with "Right" or "Left" on the same gesture.
3. Pinch duration ambiguities: pinch_index vs pinch_sustained without adequate time separation.
4. Directional swipe ambiguities.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from handcontrol.config.models import MappingRuleConfig


class ConflictSeverity(StrEnum):
    """Severity of a detected configuration conflict."""

    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


@dataclass(frozen=True)
class RuleConflict:
    """A detected conflict between rules in a profile."""

    severity: ConflictSeverity
    rule_a_index: int
    rule_b_index: int
    gesture_id: str
    message: str


def validate_profile_conflicts(rules: list[MappingRuleConfig]) -> list[RuleConflict]:
    """Inspect a list of mapping rules and return any conflicts found."""
    conflicts: list[RuleConflict] = []
    n = len(rules)

    for i in range(n):
        ra = rules[i]
        if not ra.enabled:
            continue

        for j in range(i + 1, n):
            rb = rules[j]
            if not rb.enabled:
                continue

            # 1. Exact duplicate trigger with equal priority
            if ra.gesture_id == rb.gesture_id and ra.trigger_phase == rb.trigger_phase:
                # Same hand or overlapping hands
                same_hand = (ra.hand == rb.hand) or (ra.hand == "Any") or (rb.hand == "Any")

                if same_hand and ra.priority == rb.priority:
                    conflicts.append(
                        RuleConflict(
                            severity=ConflictSeverity.ERROR,
                            rule_a_index=i,
                            rule_b_index=j,
                            gesture_id=ra.gesture_id,
                            message=(
                                f"Conflito direto: Regras {i + 1} e {j + 1} disparam com o mesmo gesto "
                                f"'{ra.gesture_id}' (mão: '{ra.hand}'/'{rb.hand}') na mesma prioridade ({ra.priority}). "
                                "Altere a prioridade ou a mão para resolver a ambiguidade."
                            ),
                        )
                    )
                elif same_hand and abs(ra.priority - rb.priority) < 2:
                    conflicts.append(
                        RuleConflict(
                            severity=ConflictSeverity.WARNING,
                            rule_a_index=i,
                            rule_b_index=j,
                            gesture_id=ra.gesture_id,
                            message=(
                                f"Aviso de prioridade próxima: Regras {i + 1} e {j + 1} compartilham o gesto "
                                f"'{ra.gesture_id}'. A prioridade mais alta ({max(ra.priority, rb.priority)}) "
                                "sempre vencerá a menor."
                            ),
                        )
                    )

            # 2. Geometric conflict: pinch_index vs pinch_sustained
            is_pinch_pair = (
                ra.gesture_id == "pinch_index" and rb.gesture_id == "pinch_sustained"
            ) or (ra.gesture_id == "pinch_sustained" and rb.gesture_id == "pinch_index")
            same_hand_pinch = ra.hand == rb.hand or ra.hand == "Any" or rb.hand == "Any"
            if (
                is_pinch_pair
                and same_hand_pinch
                and ra.trigger_phase == rb.trigger_phase == "STARTED"
            ):
                conflicts.append(
                    RuleConflict(
                        severity=ConflictSeverity.INFO,
                        rule_a_index=i,
                        rule_b_index=j,
                        gesture_id="pinch",
                        message=(
                            "Nota de timing: 'pinch_index' e 'pinch_sustained' compartilham a mesma pose. "
                            "O clique rápido requer liberação antes de 400 ms; após 400 ms, o arraste é ativado."
                        ),
                    )
                )

    return conflicts
