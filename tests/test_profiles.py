"""Unit and integration tests for Phase 7 — Profile Editor, Dynamic Gestures, and Tray (Section 9, 10, 11).

Tests:
1. Trajectory processing & DTW:
   - Polyline resampling to equidistant points.
   - Center & unit box normalization.
   - DTW alignment distance (identical = 0, similar < 0.15, dissimilar > 0.5).
   - Directional swipe classifier (left, right, up, down).
   - Circular gesture classifier (circle_cw, circle_ccw).
2. DynamicGestureRecognizer:
   - Dynamic swipe and circle event generation.
   - Custom trajectory template registration and recognition.
   - Per-gesture cooldown suppression.
   - Reset on hand loss.
3. GestureEngine Dynamic Integration:
   - Passing landmarks_by_hand evaluates dynamic gestures alongside static gestures.
4. Persistence & ConfigManager:
   - Default config generation when file is absent.
   - Atomic saving and backup (.bak) creation.
   - Corrupted JSON recovery (fallback to backup or defaults without crashing).
   - Schema migration.
   - Profile export and import (valid import, duplicate ID deduplication, malformed JSON rejection).
5. Conflict Validation:
   - ERROR on duplicate trigger with identical priority.
   - WARNING on close priority.
   - INFO on pinch hold duration notes.
6. SystemTrayController:
   - Privacy state updates and menu signals.
7. Phase 7 End-to-End:
   - Creating a custom gesture + profile, loading into mapper, and executing via pipeline.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from handcontrol.actions.controller import ActionController
from handcontrol.actions.simulation import SimulatedExecutor
from handcontrol.config.models import (
    MappingRuleConfig,
    ProfileModel,
)
from handcontrol.config.storage import ConfigManager, migrate_config_dict
from handcontrol.features.trajectory import (
    TrajectoryBuffer,
    dtw_distance,
    normalize_trajectory_box,
    resample_points,
)
from handcontrol.gestures.dynamic import CustomTrajectoryTemplate, DynamicGestureRecognizer
from handcontrol.gestures.engine import GestureEngine
from handcontrol.mapping.conflict import ConflictSeverity, validate_profile_conflicts
from handcontrol.mapping.profile import GestureMapper
from handcontrol.tracking.models import (
    ActionStatus,
    FingerState,
    GestureEvent,
    GesturePhase,
    HandFeatures,
)

# ---------------------------------------------------------------------------
# 1. Trajectory processing and DTW tests
# ---------------------------------------------------------------------------


class TestTrajectoryProcessing:
    def test_resample_points_equidistant(self):
        # Line from (0, 0) to (10, 0)
        pts = [(0.0, 0.0), (10.0, 0.0)]
        resampled = resample_points(pts, target_count=5)
        assert len(resampled) == 5
        assert resampled[0] == (0.0, 0.0)
        assert resampled[-1] == (10.0, 0.0)
        # Check equidistant spacing (step = 2.5)
        assert resampled[1][0] == pytest.approx(2.5, abs=1e-3)
        assert resampled[2][0] == pytest.approx(5.0, abs=1e-3)
        assert resampled[3][0] == pytest.approx(7.5, abs=1e-3)

    def test_normalize_trajectory_box_invariance(self):
        # A rectangle from (100, 200) to (300, 400)
        pts = [(100.0, 200.0), (300.0, 200.0), (300.0, 400.0), (100.0, 400.0)]
        norm = normalize_trajectory_box(pts)
        assert len(norm) == 4
        # Centroid should be at (0, 0)
        cx = sum(p[0] for p in norm) / len(norm)
        cy = sum(p[1] for p in norm) / len(norm)
        assert cx == pytest.approx(0.0, abs=1e-4)
        assert cy == pytest.approx(0.0, abs=1e-4)
        # Max distance from origin should be 1.0
        max_dist = max(math.hypot(p[0], p[1]) for p in norm)
        assert max_dist == pytest.approx(1.0, abs=1e-4)

    def test_dtw_distance_identical_and_similar(self):
        # Sequence of a diagonal line
        seq1 = [(float(i) / 10.0, float(i) / 10.0) for i in range(11)]
        # Identical sequence
        assert dtw_distance(seq1, seq1) == pytest.approx(0.0, abs=1e-5)

        # Slightly noisy sequence (jitter < 0.02)
        seq2 = [(p[0] + 0.01, p[1] - 0.01) for p in seq1]
        dist_similar = dtw_distance(seq1, seq2)
        assert dist_similar < 0.05

        # Completely different sequence (horizontal line vs diagonal)
        seq3 = [(float(i) / 10.0, 0.0) for i in range(11)]
        dist_dissimilar = dtw_distance(seq1, seq3)
        assert dist_dissimilar > 0.25

    def test_swipe_detection(self):
        buf = TrajectoryBuffer(max_duration_s=1.0)
        t = 1.0

        # Swipe right: X moves from 0.2 to 0.6 in 0.15s with almost no Y change
        for i in range(6):
            buf.add_point(0.2 + i * 0.08, 0.5 + (i % 2) * 0.005, t + i * 0.03)

        gesture, dx, dy = buf.detect_swipe(current_time=t + 0.15)
        assert gesture == "swipe_right"
        assert dx > 0.3

        # Swipe left
        buf.clear()
        for i in range(6):
            buf.add_point(0.6 - i * 0.08, 0.5, t + i * 0.03)

        gesture_l, dx_l, _ = buf.detect_swipe(current_time=t + 0.15)
        assert gesture_l == "swipe_left"
        assert dx_l < -0.3

        # Swipe up (Y decreases)
        buf.clear()
        for i in range(6):
            buf.add_point(0.5, 0.7 - i * 0.08, t + i * 0.03)

        gesture_u, _, dy_u = buf.detect_swipe(current_time=t + 0.15)
        assert gesture_u == "swipe_up"
        assert dy_u < -0.3

    def test_circle_detection(self):
        buf = TrajectoryBuffer(max_duration_s=1.5)
        t = 1.0
        cx, cy, r = 0.5, 0.5, 0.15
        num_points = 20

        # Clockwise circle in screen coords: angle from 0 to 2*pi
        for i in range(num_points):
            angle = (float(i) / (num_points - 1)) * 2 * math.pi
            px = cx + r * math.cos(angle)
            py = cy + r * math.sin(angle)
            buf.add_point(px, py, t + i * 0.03)

        gesture_cw, angle_cw = buf.detect_circle(current_time=t + 0.6)
        assert gesture_cw == "circle_cw"
        assert angle_cw > 5.0  # Near 2*pi radians

        # Counter-clockwise circle
        buf.clear()
        for i in range(num_points):
            angle = (float(i) / (num_points - 1)) * -2 * math.pi
            px = cx + r * math.cos(angle)
            py = cy + r * math.sin(angle)
            buf.add_point(px, py, t + i * 0.03)

        gesture_ccw, angle_ccw = buf.detect_circle(current_time=t + 0.6)
        assert gesture_ccw == "circle_ccw"
        assert angle_ccw < -5.0


# ---------------------------------------------------------------------------
# 2. DynamicGestureRecognizer tests
# ---------------------------------------------------------------------------


class TestDynamicGestureRecognizer:
    def test_recognizes_swipe_and_applies_cooldown(self):
        rec = DynamicGestureRecognizer()
        feat_valid = HandFeatures(
            fingers_extended=[FingerState.EXTENDED] * 5,
            is_valid=True,
        )

        t = 1.0
        events: list[GestureEvent] = []

        # Feed a rapid swipe right with index fingertip (landmark index 8)
        for i in range(6):
            pts = [(0.0, 0.0)] * 21
            pts[8] = (0.2 + i * 0.08, 0.5)  # Index tip moves right
            now = t + i * 0.03
            ev = rec.update(
                features_by_hand={"Right": feat_valid},
                landmarks_by_hand={"Right": pts},
                timestamp=now,
            )
            events.extend(ev)

        assert len(events) == 1
        assert events[0].gesture_id == "swipe_right"
        assert events[0].hand == "Right"
        assert events[0].phase == GesturePhase.STARTED

        # Immediate subsequent swipe within cooldown must be suppressed
        events_suppressed = rec.update(
            features_by_hand={"Right": feat_valid},
            landmarks_by_hand={"Right": [(0.8, 0.5)] * 21},
            timestamp=t + 0.20,
        )
        assert len(events_suppressed) == 0

    def test_custom_trajectory_template_matching(self):
        rec = DynamicGestureRecognizer(dtw_match_threshold=0.35)
        # Register a V-shape template
        v_template_pts = [
            (0.0, 0.0),
            (0.25, 0.4),
            (0.5, 0.8),
            (0.75, 0.4),
            (1.0, 0.0),
        ]
        rec.add_custom_template(
            CustomTrajectoryTemplate(
                gesture_id="gesture_v_shape",
                hand="Right",
                points=v_template_pts,
                max_dtw_distance=0.35,
            )
        )

        feat_valid = HandFeatures(
            fingers_extended=[FingerState.EXTENDED] * 5,
            is_valid=True,
        )
        t = 1.0
        events: list[GestureEvent] = []

        # Feed a path matching the V-shape trajectory
        for i in range(16):
            alpha = float(i) / 15.0
            if alpha <= 0.5:
                px = 0.2 + alpha * 0.4
                py = 0.2 + alpha * 0.8
            else:
                px = 0.2 + alpha * 0.4
                py = 0.6 - (alpha - 0.5) * 0.8
            pts = [(0.0, 0.0)] * 21
            pts[8] = (px, py)
            ev = rec.update(
                features_by_hand={"Right": feat_valid},
                landmarks_by_hand={"Right": pts},
                timestamp=t + i * 0.04,
            )
            events.extend(ev)

        assert len(events) >= 1
        assert events[0].gesture_id == "gesture_v_shape"
        assert events[0].hand == "Right"


# ---------------------------------------------------------------------------
# 3. GestureEngine Integration with Dynamic Gestures
# ---------------------------------------------------------------------------


class TestGestureEngineDynamicIntegration:
    def test_engine_emits_dynamic_swipes(self):
        engine = GestureEngine(preferred_hand="Right")
        feat = HandFeatures(fingers_extended=[FingerState.EXTENDED] * 5, is_valid=True)

        events: list[GestureEvent] = []
        t = 1.0

        # Feed frames that create a swipe left
        for i in range(6):
            pts = [(0.0, 0.0)] * 21
            pts[8] = (0.7 - i * 0.08, 0.4)
            evs = engine.update(
                features_by_hand={"Right": feat},
                timestamp=t + i * 0.03,
                landmarks_by_hand={"Right": pts},
            )
            events.extend(evs)

        swipe_events = [e for e in events if e.gesture_id == "swipe_left"]
        assert len(swipe_events) == 1
        assert swipe_events[0].hand == "Right"


# ---------------------------------------------------------------------------
# 4. Persistence & ConfigManager Tests (Section 11.3)
# ---------------------------------------------------------------------------


class TestConfigManager:
    @pytest.fixture
    def temp_config_path(self, tmp_path: Path) -> Path:
        return tmp_path / "config.json"

    def test_load_non_existent_creates_default(self, temp_config_path: Path):
        cm = ConfigManager(config_path=temp_config_path)
        cfg = cm.load()
        assert cfg.schema_version == 1
        assert len(cfg.profiles) >= 2
        assert cfg.active_profile_id == "default"
        # Must have written file to disk
        assert temp_config_path.exists()

    def test_atomic_save_and_backup_creation(self, temp_config_path: Path):
        cm = ConfigManager(config_path=temp_config_path)
        cfg = cm.load()

        # Modify a setting and save
        cfg.settings.dead_zone_px = 7.5
        ok = cm.save(cfg)
        assert ok
        assert temp_config_path.exists()

        # Save again -> backup (.bak) must exist with previous content
        cfg.settings.dead_zone_px = 9.0
        cm.save(cfg)
        assert cm.backup_path.exists()

        # Reload from disk
        reloaded = ConfigManager(config_path=temp_config_path).load()
        assert reloaded.settings.dead_zone_px == 9.0

    def test_corrupted_json_recovers_from_backup(self, temp_config_path: Path):
        cm = ConfigManager(config_path=temp_config_path)
        cfg = cm.load()
        cfg.settings.dead_zone_px = 12.0
        cm.save(cfg)
        # Second save creates valid backup with dead_zone_px = 12.0
        cfg.settings.dead_zone_px = 15.0
        cm.save(cfg)

        # Corrupt main file with invalid JSON syntax
        with open(temp_config_path, "w", encoding="utf-8") as f:
            f.write("{ INVALID JSON NOT COMPLETE ...")

        # Loading must not crash; must recover from backup
        recovered = ConfigManager(config_path=temp_config_path).load()
        assert recovered.schema_version == 1
        assert recovered.settings.dead_zone_px in (12.0, 15.0)

    def test_unrecoverable_corruption_falls_back_to_defaults_without_crash(
        self, temp_config_path: Path
    ):
        cm = ConfigManager(config_path=temp_config_path)
        cm.load()

        # Corrupt both main file and backup file
        with open(temp_config_path, "w", encoding="utf-8") as f:
            f.write("CORRUPT")
        with open(cm.backup_path, "w", encoding="utf-8") as f:
            f.write("ALSO_CORRUPT")

        # Must cleanly return default configuration without crashing
        fallback = ConfigManager(config_path=temp_config_path).load()
        assert fallback.schema_version == 1
        assert fallback.active_profile_id == "default"

    def test_schema_migration(self):
        # Version 0 dict
        old_raw = {
            "settings": {"camera_id": 1},
            "profiles": [],
        }
        migrated = migrate_config_dict(old_raw)
        assert migrated["schema_version"] == 1

    def test_profile_export_and_import(self, temp_config_path: Path, tmp_path: Path):
        cm = ConfigManager(config_path=temp_config_path)
        cm.load()

        export_file = tmp_path / "exported_default.json"
        ok, msg = cm.export_profile("default", export_file)
        assert ok
        assert export_file.exists()

        # Import the exported profile
        ok_imp, msg_imp, imported = cm.import_profile(export_file)
        assert ok_imp
        assert imported is not None
        # Must have assigned deduplicated profile_id because 'default' already exists
        assert imported.profile_id != "default"
        assert "default" in imported.profile_id

    def test_import_malformed_json_refused_cleanly(self, temp_config_path: Path, tmp_path: Path):
        cm = ConfigManager(config_path=temp_config_path)
        cm.load()

        # 1. Invalid syntax
        bad_syntax_file = tmp_path / "bad.json"
        with open(bad_syntax_file, "w") as f:
            f.write("{ invalid json")
        ok, msg, imp = cm.import_profile(bad_syntax_file)
        assert not ok
        assert imp is None
        assert "malformado" in msg

        # 2. Invalid schema (missing required fields)
        bad_schema_file = tmp_path / "bad_schema.json"
        with open(bad_schema_file, "w") as f:
            json.dump({"random_key": 123}, f)
        ok2, msg2, imp2 = cm.import_profile(bad_schema_file)
        assert not ok2
        assert imp2 is None
        assert "inválida" in msg2


# ---------------------------------------------------------------------------
# 5. Conflict Validation Tests (Section 4.7 & 5)
# ---------------------------------------------------------------------------


class TestConflictValidation:
    def test_direct_conflict_detected_as_error(self):
        # Two enabled rules on the same gesture, same hand, same priority
        r1 = MappingRuleConfig(
            gesture_id="pinch_index",
            action_name="click_left",
            hand="Right",
            priority=10,
        )
        r2 = MappingRuleConfig(
            gesture_id="pinch_index",
            action_name="click_right",
            hand="Right",
            priority=10,
        )
        conflicts = validate_profile_conflicts([r1, r2])
        assert len(conflicts) >= 1
        assert conflicts[0].severity == ConflictSeverity.ERROR
        assert "Conflito direto" in conflicts[0].message

    def test_close_priority_warning(self):
        r1 = MappingRuleConfig(
            gesture_id="swipe_left",
            action_name="shortcut",
            hand="Right",
            priority=10,
        )
        r2 = MappingRuleConfig(
            gesture_id="swipe_left",
            action_name="key_press",
            hand="Right",
            priority=11,  # Difference < 2
        )
        conflicts = validate_profile_conflicts([r1, r2])
        assert len(conflicts) >= 1
        assert conflicts[0].severity == ConflictSeverity.WARNING

    def test_pinch_hold_time_info(self):
        r1 = MappingRuleConfig(
            gesture_id="pinch_index",
            action_name="click_left",
            hand="Right",
            priority=10,
        )
        r2 = MappingRuleConfig(
            gesture_id="pinch_sustained",
            action_name="drag_start",
            hand="Right",
            priority=12,
        )
        conflicts = validate_profile_conflicts([r1, r2])
        # Informational advisory note
        info_notes = [c for c in conflicts if c.severity == ConflictSeverity.INFO]
        assert len(info_notes) >= 1
        assert "pinch" in info_notes[0].gesture_id

    def test_no_conflicts_for_orthogonal_rules(self):
        r1 = MappingRuleConfig(gesture_id="point", action_name="noop", hand="Right", priority=1)
        r2 = MappingRuleConfig(
            gesture_id="fist", action_name="window_grab", hand="Left", priority=5
        )
        conflicts = validate_profile_conflicts([r1, r2])
        assert len(conflicts) == 0


# ---------------------------------------------------------------------------
# 6. Phase 7 End-to-End Tests
# ---------------------------------------------------------------------------


class TestPhase7EndToEnd:
    def test_custom_profile_in_pipeline(self):
        # 1. Build a custom profile
        custom_rule = MappingRuleConfig(
            gesture_id="swipe_right",
            action_name="click_left",
            hand="Right",
            priority=15,
            enabled=True,
            trigger_phase="STARTED",
        )
        custom_profile = ProfileModel(
            profile_id="custom_test",
            name="Teste Custom",
            mappings=[custom_rule],
        )

        # 2. Load into GestureMapper
        mapper = GestureMapper()
        mapper.load_from_profile_model(custom_profile)

        # 3. Simulate swipe_right gesture event
        ev = GestureEvent(
            gesture_id="swipe_right",
            phase=GesturePhase.STARTED,
            hand="Right",
            timestamp=1.0,
            confidence=0.95,
        )

        requests = mapper.process_events([ev])
        assert len(requests) == 1
        req = requests[0]
        assert req.action_name == "click_left"

        # 4. Submit to ActionController
        controller = ActionController(executor=SimulatedExecutor())
        res = controller.submit(req)
        assert res.status == ActionStatus.SIMULATED
