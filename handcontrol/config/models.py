"""Configuration data models with Pydantic v2 validation (Section 11.3).

Defines schema for:
- Global application settings (camera, smoothing, active region, hotkey, autostart).
- Gesture definitions (static, dynamic, custom trajectories).
- Mapping rules (gesture -> allowlist action).
- Profiles collection and root AppConfig.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class GlobalSettings(BaseModel):
    """Global hardware, control, and application settings."""

    camera_id: int = Field(default=0, ge=0)
    camera_width: int = Field(default=640, ge=320)
    camera_height: int = Field(default=480, ge=240)
    camera_fps: int = Field(default=30, ge=10, le=120)
    preferred_hand: str = Field(default="Right")
    smoothing_min_cutoff: float = Field(default=1.0, gt=0.0)
    smoothing_beta: float = Field(default=0.007, ge=0.0)
    dead_zone_px: float = Field(default=3.0, ge=0.0)
    active_region_margins: tuple[float, float, float, float] = Field(
        default=(0.10, 0.10, 0.10, 0.10)
    )
    emergency_hotkey: str = Field(default="Ctrl+Shift+F12")
    minimize_to_tray: bool = Field(default=True)
    autostart: bool = Field(default=False)
    flip_handedness: bool = Field(default=False)


class GestureRuleConfig(BaseModel):
    """Configuration for a recognizable static or dynamic gesture."""

    gesture_id: str
    name: str
    hand: str = Field(default="Preferred")  # "Preferred", "Left", "Right", "Both", "Any"
    rule_type: str = Field(default="static")  # "static", "dynamic", "custom_trajectory"
    min_confirm_time_s: float = Field(default=0.05, ge=0.0)
    min_release_time_s: float = Field(default=0.05, ge=0.0)
    cooldown_s: float = Field(default=0.1, ge=0.0)
    enter_threshold: float = Field(default=0.30, ge=0.0)
    exit_threshold: float = Field(default=0.38, ge=0.0)
    enabled: bool = Field(default=True)
    trajectory_points: list[tuple[float, float]] = Field(default_factory=list)


class MappingRuleConfig(BaseModel):
    """Configuration mapping a gesture event to an action."""

    gesture_id: str
    action_name: str
    hand: str = Field(default="Any")
    priority: int = Field(default=0)
    cooldown_s: float = Field(default=0.0, ge=0.0)
    repeat_limit: int = Field(default=0, ge=0)
    enabled: bool = Field(default=True)
    parameters: dict[str, Any] = Field(default_factory=dict)
    trigger_phase: str = Field(default="STARTED")


class ProfileModel(BaseModel):
    """A user profile containing gesture configurations and action mappings."""

    profile_id: str
    name: str
    is_builtin: bool = Field(default=False)
    gestures: list[GestureRuleConfig] = Field(default_factory=list)
    mappings: list[MappingRuleConfig] = Field(default_factory=list)


class AppConfig(BaseModel):
    """Root configuration model with schema versioning."""

    schema_version: int = Field(default=1, ge=1)
    settings: GlobalSettings = Field(default_factory=GlobalSettings)
    active_profile_id: str = Field(default="default")
    profiles: list[ProfileModel] = Field(default_factory=list)


def create_default_app_config() -> AppConfig:
    """Create a fully populated default AppConfig with standard profiles (Section 5)."""
    default_mappings = [
        MappingRuleConfig(
            gesture_id="point",
            action_name="noop",
            hand="Right",
            priority=1,
            cooldown_s=0.0,
            trigger_phase="STARTED",
        ),
        MappingRuleConfig(
            gesture_id="pinch_index",
            action_name="click_left",
            hand="Right",
            priority=10,
            cooldown_s=0.15,
            repeat_limit=1,
            trigger_phase="STARTED",
        ),
        MappingRuleConfig(
            gesture_id="pinch_middle",
            action_name="click_right",
            hand="Right",
            priority=10,
            cooldown_s=0.15,
            repeat_limit=1,
            trigger_phase="STARTED",
        ),
        MappingRuleConfig(
            gesture_id="pinch_sustained",
            action_name="drag_start",
            hand="Right",
            priority=12,
            cooldown_s=0.10,
            repeat_limit=1,
            trigger_phase="STARTED",
        ),
        MappingRuleConfig(
            gesture_id="two_fingers",
            action_name="scroll",
            hand="Right",
            priority=8,
            cooldown_s=0.05,
            trigger_phase="STARTED",
        ),
        MappingRuleConfig(
            gesture_id="fist",
            action_name="window_grab",
            hand="Left",
            priority=5,
            cooldown_s=0.10,
            repeat_limit=1,
            trigger_phase="STARTED",
        ),
        MappingRuleConfig(
            gesture_id="two_open_palms",
            action_name="pause_commands",
            hand="Both",
            priority=20,
            cooldown_s=0.5,
            repeat_limit=1,
            trigger_phase="STARTED",
        ),
        # Dynamic swipe mappings
        MappingRuleConfig(
            gesture_id="swipe_left",
            action_name="shortcut",
            hand="Right",
            priority=7,
            cooldown_s=0.4,
            parameters={"keys": "alt+tab"},
            trigger_phase="STARTED",
        ),
        MappingRuleConfig(
            gesture_id="swipe_right",
            action_name="shortcut",
            hand="Right",
            priority=7,
            cooldown_s=0.4,
            parameters={"keys": "win+d"},
            trigger_phase="STARTED",
        ),
    ]

    default_gestures = [
        GestureRuleConfig(gesture_id="point", name="Apontar", hand="Preferred", rule_type="static"),
        GestureRuleConfig(
            gesture_id="pinch_index", name="Pinça Indicador", hand="Preferred", rule_type="static"
        ),
        GestureRuleConfig(
            gesture_id="pinch_middle", name="Pinça Médio", hand="Preferred", rule_type="static"
        ),
        GestureRuleConfig(
            gesture_id="pinch_sustained",
            name="Pinça Sustentada",
            hand="Preferred",
            rule_type="static",
            min_confirm_time_s=0.4,
        ),
        GestureRuleConfig(
            gesture_id="two_fingers", name="Dois Dedos", hand="Preferred", rule_type="static"
        ),
        GestureRuleConfig(gesture_id="fist", name="Punho Fechado", hand="Left", rule_type="static"),
        GestureRuleConfig(
            gesture_id="two_open_palms",
            name="Duas Palmas",
            hand="Both",
            rule_type="static",
            min_confirm_time_s=1.0,
        ),
        GestureRuleConfig(
            gesture_id="swipe_left",
            name="Deslizar para Esquerda",
            hand="Right",
            rule_type="dynamic",
        ),
        GestureRuleConfig(
            gesture_id="swipe_right",
            name="Deslizar para Direita",
            hand="Right",
            rule_type="dynamic",
        ),
        GestureRuleConfig(
            gesture_id="swipe_up", name="Deslizar para Cima", hand="Right", rule_type="dynamic"
        ),
        GestureRuleConfig(
            gesture_id="swipe_down", name="Deslizar para Baixo", hand="Right", rule_type="dynamic"
        ),
        GestureRuleConfig(
            gesture_id="circle_cw", name="Círculo Horário", hand="Right", rule_type="dynamic"
        ),
        GestureRuleConfig(
            gesture_id="circle_ccw", name="Círculo Anti-horário", hand="Right", rule_type="dynamic"
        ),
    ]

    default_profile = ProfileModel(
        profile_id="default",
        name="Padrão",
        is_builtin=True,
        gestures=default_gestures,
        mappings=default_mappings,
    )

    presentation_profile = ProfileModel(
        profile_id="presentation",
        name="Apresentação",
        is_builtin=True,
        gestures=default_gestures,
        mappings=[
            MappingRuleConfig(
                gesture_id="swipe_right",
                action_name="shortcut",
                hand="Right",
                priority=10,
                parameters={"keys": "right"},
            ),
            MappingRuleConfig(
                gesture_id="swipe_left",
                action_name="shortcut",
                hand="Right",
                priority=10,
                parameters={"keys": "left"},
            ),
            MappingRuleConfig(
                gesture_id="two_open_palms",
                action_name="pause_commands",
                hand="Both",
                priority=20,
                cooldown_s=0.5,
            ),
        ],
    )

    return AppConfig(
        schema_version=1,
        settings=GlobalSettings(),
        active_profile_id="default",
        profiles=[default_profile, presentation_profile],
    )
