"""Mapping module (Phase 3 & Phase 7)."""

from handcontrol.mapping.conflict import ConflictSeverity, RuleConflict, validate_profile_conflicts
from handcontrol.mapping.profile import GestureMapper, MappingRule, default_profile

__all__ = [
    "ConflictSeverity",
    "GestureMapper",
    "MappingRule",
    "RuleConflict",
    "default_profile",
    "validate_profile_conflicts",
]
