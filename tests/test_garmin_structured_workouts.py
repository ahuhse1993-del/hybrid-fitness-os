"""
tests/test_garmin_structured_workouts.py
Tests fuer die Garmin-Struktur-Ueberarbeitung (2026-08-16):
- Distanz-/Lap-Button-Endbedingungen (duration_meters, lap_button) neben duration_secs
- pace_zone-Target fuer explizit pacebasierte Intervalle
- build_garmin_title() ohne Datum, mit Laengenbegrenzung
- Workout-Struktur-Prioritaet in coach.garmin_batch._build_workout_steps
Alles offline: kein echter Garmin-/DB-Call in diesem File (build_workout_payload
und _build_workout_steps sind reine Funktionen ohne Netzwerk-/DB-Zugriff).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from coach.garmin_push import (
    GarminWorkoutValidationError,
    build_garmin_title,
    build_workout_payload,
)
from coach.garmin_batch import _build_workout_steps
from coach.mcp_server import preview_garmin_workout


def _step_target_key(step) -> str:
    t = getattr(step, "targetType", {})
    return t.get("workoutTargetTypeKey") if isinstance(t, dict) else str(t)


def _step_end_condition_key(step) -> str:
    c = getattr(step, "endCondition", {})
    return c.get("conditionTypeKey") if isinstance(c, dict) else str(c)


BASE = {"name": "Test", "estimated_duration_secs": 600}


# ── 1: preview_garmin_workout (MCP-Tool) akzeptiert duration_meters ─────────

class TestPreviewGarminWorkoutMcpTool:
    def test_accepts_simple_duration_meters_step(self):
        result = preview_garmin_workout({**BASE, "steps": [{"type": "interval", "duration_meters": 2000}]})
        assert result["valid"] is True
        assert result["steps"][0]["duration_secs"] == 2000.0  # endConditionValue, hier Meter statt Sekunden

    def test_rejects_step_with_no_end_condition(self):
        result = preview_garmin_workout({**BASE, "steps": [{"type": "interval"}]})
        assert result["valid"] is False
        assert "exactly one" in result["error"]


# ── 2+3: duration_meters / warmup+cooldown / gemischte Schritte ─────────────

class TestDistanceEndCondition:
    def test_simple_duration_meters_step_accepted(self):
        w = {**BASE, "steps": [{"type": "interval", "duration_meters": 2000}]}
        p = build_workout_payload(w)
        step = p.workoutSegments[0].workoutSteps[0]
        assert _step_end_condition_key(step) == "distance"
        assert step.endConditionValue == 2000.0

    def test_warmup_and_cooldown_with_duration_meters_accepted(self):
        w = {**BASE, "steps": [
            {"type": "warmup", "duration_meters": 2000},
            {"type": "interval", "duration_secs": 300},
            {"type": "cooldown", "duration_meters": 2000},
        ]}
        p = build_workout_payload(w)
        steps = p.workoutSegments[0].workoutSteps
        assert _step_end_condition_key(steps[0]) == "distance"
        assert _step_end_condition_key(steps[2]) == "distance"

    def test_mixed_distance_and_time_steps_accepted(self):
        w = {**BASE, "steps": [
            {"type": "warmup", "duration_meters": 2000},
            {"type": "interval", "duration_secs": 180},
            {"type": "cooldown", "duration_meters": 1000},
        ]}
        p = build_workout_payload(w)
        steps = p.workoutSegments[0].workoutSteps
        assert [_step_end_condition_key(s) for s in steps] == ["distance", "time", "distance"]

    def test_zero_duration_meters_raises(self):
        w = {**BASE, "steps": [{"type": "interval", "duration_meters": 0}]}
        with pytest.raises(GarminWorkoutValidationError, match="duration_meters"):
            build_workout_payload(w)

    def test_duration_secs_and_duration_meters_together_raises(self):
        w = {**BASE, "steps": [{"type": "interval", "duration_secs": 60, "duration_meters": 200}]}
        with pytest.raises(GarminWorkoutValidationError, match="exactly one"):
            build_workout_payload(w)

    def test_no_end_condition_raises(self):
        w = {**BASE, "steps": [{"type": "interval"}]}
        with pytest.raises(GarminWorkoutValidationError, match="exactly one"):
            build_workout_payload(w)


class TestLapButtonEndCondition:
    def test_lap_button_step_accepted(self):
        w = {**BASE, "steps": [{"type": "recovery", "lap_button": True, "description": "locker bergab"}]}
        p = build_workout_payload(w)
        step = p.workoutSegments[0].workoutSteps[0]
        assert _step_end_condition_key(step) == "lap.button"
        assert step.endConditionValue is None

    def test_lap_button_step_has_no_target(self):
        w = {**BASE, "steps": [{"type": "recovery", "lap_button": True}]}
        p = build_workout_payload(w)
        assert _step_target_key(p.workoutSegments[0].workoutSteps[0]) == "no.target"

    def test_lap_button_and_duration_secs_together_raises(self):
        w = {**BASE, "steps": [{"type": "recovery", "lap_button": True, "duration_secs": 60}]}
        with pytest.raises(GarminWorkoutValidationError, match="exactly one"):
            build_workout_payload(w)


# ── 4: Repeat-Bloecke rekursiv validiert ──────────────────────────────────────

class TestRepeatRecursiveValidation:
    def test_repeat_inner_step_accepts_duration_meters(self):
        w = {**BASE, "steps": [
            {"type": "repeat", "iterations": 3, "steps": [
                {"type": "interval", "duration_meters": 400},
                {"type": "recovery", "duration_secs": 60},
            ]},
        ]}
        p = build_workout_payload(w)
        repeat = p.workoutSegments[0].workoutSteps[0]
        assert _step_end_condition_key(repeat.workoutSteps[0]) == "distance"
        assert _step_end_condition_key(repeat.workoutSteps[1]) == "time"

    def test_repeat_inner_step_missing_end_condition_raises(self):
        w = {**BASE, "steps": [
            {"type": "repeat", "iterations": 3, "steps": [{"type": "interval"}]},
        ]}
        with pytest.raises(GarminWorkoutValidationError, match="exactly one"):
            build_workout_payload(w)

    def test_repeat_inner_lap_button_recovery_accepted(self):
        w = {**BASE, "steps": [
            {"type": "repeat", "iterations": 4, "steps": [
                {"type": "interval", "duration_secs": 240, "description": "RPE 7-8"},
                {"type": "recovery", "lap_button": True, "description": "locker bergab"},
            ]},
        ]}
        p = build_workout_payload(w)
        repeat = p.workoutSegments[0].workoutSteps[0]
        assert _step_end_condition_key(repeat.workoutSteps[1]) == "lap.button"


# ── pace_zone Target ───────────────────────────────────────────────────────

class TestPaceZoneTarget:
    def test_pace_zone_target_builds_speed_range(self):
        w = {**BASE, "session_type": "Interval Session", "steps": [
            {"type": "interval", "duration_meters": 2000, "enforce_garmin_target": True,
             "target": {"type": "pace_zone", "slow_pace_sec_per_km": 270, "fast_pace_sec_per_km": 265}},
        ]}
        p = build_workout_payload(w)
        step = p.workoutSegments[0].workoutSteps[0]
        assert _step_target_key(step) == "pace.zone"
        assert step.targetValueOne == pytest.approx(1000 / 270)
        assert step.targetValueTwo == pytest.approx(1000 / 265)
        assert step.targetValueOne < step.targetValueTwo

    def test_pace_zone_fast_not_faster_than_slow_raises(self):
        w = {**BASE, "session_type": "Interval Session", "steps": [
            {"type": "interval", "duration_meters": 2000, "enforce_garmin_target": True,
             "target": {"type": "pace_zone", "slow_pace_sec_per_km": 265, "fast_pace_sec_per_km": 270}},
        ]}
        with pytest.raises(GarminWorkoutValidationError, match="faster"):
            build_workout_payload(w)

    def test_pace_zone_without_enforce_flag_stays_no_target(self):
        w = {**BASE, "session_type": "Interval Session", "steps": [
            {"type": "interval", "duration_meters": 2000,
             "target": {"type": "pace_zone", "slow_pace_sec_per_km": 270, "fast_pace_sec_per_km": 265}},
        ]}
        p = build_workout_payload(w)
        assert _step_target_key(p.workoutSegments[0].workoutSteps[0]) == "no.target"


# ── build_garmin_title ─────────────────────────────────────────────────────

class TestBuildGarminTitle:
    def test_title_with_name_and_session_type(self):
        assert build_garmin_title("Trail Sharpening", "Hill Session") == "CAIRN – Trail Sharpening · Hill Session"

    def test_title_examples_from_spec(self):
        assert build_garmin_title("Threshold 3×2 km", "Interval Session") == "CAIRN – Threshold 3×2 km · Interval Session"
        assert build_garmin_title("Easy Trail", "Trail Run") == "CAIRN – Easy Trail · Trail Run"
        assert build_garmin_title("Long Trail", "Long Run") == "CAIRN – Long Trail · Long Run"
        assert build_garmin_title("Recovery Ride", "Cross Training") == "CAIRN – Recovery Ride · Cross Training"

    def test_fallback_without_name(self):
        assert build_garmin_title(None, "Easy Run") == "CAIRN – Easy Run"
        assert build_garmin_title("", "Easy Run") == "CAIRN – Easy Run"

    def test_no_date_in_title(self):
        title = build_garmin_title("Trail Sharpening", "Hill Session")
        import re
        assert not re.search(r"\d{4}-\d{2}-\d{2}", title)

    def test_long_name_is_truncated_session_type_preserved(self):
        long_name = "X" * 200
        title = build_garmin_title(long_name, "Hill Session")
        assert len(title) <= 100
        assert title.endswith("· Hill Session")

    def test_short_title_untouched(self):
        title = build_garmin_title("Easy Trail", "Trail Run")
        assert len(title) < 100
        assert title == "CAIRN – Easy Trail · Trail Run"


# ── Strength weiterhin geblockt (Punkt 12) ─────────────────────────────────

class TestStrengthStillBlocked:
    def test_strength_training_rejected_even_with_distance_step(self):
        w = {**BASE, "session_type": "Strength Training",
             "steps": [{"type": "interval", "duration_meters": 1000}]}
        with pytest.raises(GarminWorkoutValidationError, match="niemals"):
            build_workout_payload(w)


# ── Bestehende zeitbasierte Rennrad-Workouts funktionieren weiterhin (Punkt 13) ─

class TestCyclingTimeBasedStillWorks:
    def test_cycling_time_based_workout_builds(self):
        w = {**BASE, "steps": [{"type": "interval", "duration_secs": 3600}]}
        p = build_workout_payload(w, sport="cycling")
        assert p.workoutSegments[0].sportType["sportTypeKey"] == "cycling"
        assert _step_end_condition_key(p.workoutSegments[0].workoutSteps[0]) == "time"


# ── coach.garmin_batch._build_workout_steps: Struktur-Prioritaet ───────────

class TestWorkoutStepsPriority:
    def test_stored_structure_used_unchanged_never_collapsed(self):
        real_steps = [
            {"type": "warmup", "duration_meters": 2000, "target": {"type": "no_target"}},
            {"type": "interval", "duration_secs": 120, "target": {"type": "no_target"}},
            {"type": "cooldown", "duration_meters": 2000, "target": {"type": "no_target"}},
        ]
        session = {"workout_steps": real_steps, "distance_km": 999, "duration_min": 999, "sport": "running"}
        steps = _build_workout_steps(session)
        assert steps == real_steps
        assert len(steps) == 3  # nicht auf einen Gesamtzeitblock reduziert

    def test_easy_run_without_structure_becomes_single_distance_step(self):
        session = {"workout_steps": None, "distance_km": 13, "duration_min": 100, "sport": "running"}
        steps = _build_workout_steps(session)
        assert steps == [{"type": "interval", "duration_meters": 13000, "target": {"type": "no_target"}}]

    def test_cycling_without_structure_stays_time_based(self):
        session = {"workout_steps": None, "distance_km": None, "duration_min": 70, "sport": "cycling"}
        steps = _build_workout_steps(session)
        assert steps == [{"type": "interval", "duration_secs": 4200, "target": {"type": "no_target"}}]

    def test_running_without_distance_falls_back_to_time(self):
        session = {"workout_steps": None, "distance_km": None, "duration_min": 45, "sport": "running"}
        steps = _build_workout_steps(session)
        assert steps == [{"type": "interval", "duration_secs": 2700, "target": {"type": "no_target"}}]

    def test_fallback_never_sets_real_target_from_session_zone(self):
        # Vor der Ueberarbeitung baute der Fallback ein echtes hr_zone-Target
        # aus session_zone -- das widersprach der No-Target-Policy und wurde
        # entfernt: Zone/RPE bleiben reiner Freitext, nie ein Garmin-Alarm.
        session = {"workout_steps": None, "distance_km": 10, "duration_min": 60,
                   "sport": "running", "session_zone": "Z2"}
        steps = _build_workout_steps(session)
        assert steps[0]["target"] == {"type": "no_target"}


# ── Threshold 3×2 km: drei 2000m-Intervalle, nur diese mit Pace-Target ─────

_PACE_TARGET = {"type": "pace_zone", "slow_pace_sec_per_km": 270, "fast_pace_sec_per_km": 265}
_NO_TARGET = {"type": "no_target"}

THRESHOLD_3X2K_STEPS = [
    {"type": "warmup", "duration_meters": 2000, "target": _NO_TARGET},
    {"type": "repeat", "iterations": 4, "steps": [
        {"type": "interval", "duration_secs": 20, "target": _NO_TARGET, "description": "Stride"},
        {"type": "recovery", "duration_secs": 40, "target": _NO_TARGET, "description": "locker"},
    ]},
    {"type": "interval", "duration_meters": 2000, "enforce_garmin_target": True, "target": _PACE_TARGET},
    {"type": "recovery", "duration_secs": 180, "target": _NO_TARGET, "description": "Trab"},
    {"type": "interval", "duration_meters": 2000, "enforce_garmin_target": True, "target": _PACE_TARGET},
    {"type": "recovery", "duration_secs": 180, "target": _NO_TARGET, "description": "Trab"},
    {"type": "interval", "duration_meters": 2000, "enforce_garmin_target": True, "target": _PACE_TARGET},
    {"type": "cooldown", "duration_meters": 2000, "target": _NO_TARGET},
]

HILL_THRESHOLD_STEPS = [
    {"type": "warmup", "duration_meters": 2000, "target": _NO_TARGET},
    {"type": "repeat", "iterations": 4, "steps": [
        {"type": "interval", "duration_secs": 20, "target": _NO_TARGET, "description": "Stride"},
        {"type": "recovery", "duration_secs": 40, "target": _NO_TARGET, "description": "locker"},
    ]},
    {"type": "repeat", "iterations": 5, "steps": [
        {"type": "interval", "duration_secs": 240, "target": _NO_TARGET, "description": "RPE 7-8"},
        {"type": "recovery", "lap_button": True, "target": _NO_TARGET, "description": "locker bergab"},
    ]},
    {"type": "cooldown", "duration_meters": 2000, "target": _NO_TARGET},
]


class TestThreshold3x2kStructure:
    def test_contains_three_2000m_intervals(self):
        w = {**BASE, "name": "Threshold 3×2 km", "session_type": "Interval Session", "steps": THRESHOLD_3X2K_STEPS}
        p = build_workout_payload(w)
        top_steps = p.workoutSegments[0].workoutSteps
        distance_intervals = [
            s for s in top_steps
            if not hasattr(s, "numberOfIterations")
            and s.stepType.get("stepTypeKey") == "interval"
            and _step_end_condition_key(s) == "distance"
            and s.endConditionValue == 2000.0
        ]
        assert len(distance_intervals) == 3

    def test_only_the_three_threshold_intervals_have_pace_target(self):
        w = {**BASE, "name": "Threshold 3×2 km", "session_type": "Interval Session", "steps": THRESHOLD_3X2K_STEPS}
        p = build_workout_payload(w)
        top_steps = p.workoutSegments[0].workoutSteps

        def _flatten(steps):
            for s in steps:
                if hasattr(s, "numberOfIterations"):
                    yield from _flatten(s.workoutSteps)
                else:
                    yield s

        all_steps = list(_flatten(top_steps))
        pace_steps = [s for s in all_steps if _step_target_key(s) == "pace.zone"]
        no_target_steps = [s for s in all_steps if _step_target_key(s) == "no.target"]
        assert len(pace_steps) == 3
        assert len(no_target_steps) == len(all_steps) - 3

    def test_no_extra_recovery_after_last_interval(self):
        # Keine zusaetzliche Trabpause nach dem dritten 2-km-Intervall.
        w = {**BASE, "name": "Threshold 3×2 km", "session_type": "Interval Session", "steps": THRESHOLD_3X2K_STEPS}
        p = build_workout_payload(w)
        top_steps = p.workoutSegments[0].workoutSteps
        last_two = [s.stepType.get("stepTypeKey") for s in top_steps[-2:]]
        assert last_two == ["interval", "cooldown"]


class TestHillIntervalsAndRecoveryNoTarget:
    def test_hill_intervals_and_recovery_have_no_target(self):
        w = {**BASE, "name": "Hill Threshold", "session_type": "Hill Session", "steps": HILL_THRESHOLD_STEPS}
        p = build_workout_payload(w)
        uphill_repeat = p.workoutSegments[0].workoutSteps[2]  # 5x Uphill/locker bergab
        uphill_interval, recovery_lap = uphill_repeat.workoutSteps
        assert _step_target_key(uphill_interval) == "no.target"
        assert _step_target_key(recovery_lap) == "no.target"
        assert _step_end_condition_key(recovery_lap) == "lap.button"

    def test_title_matches_spec_exactly(self):
        # Punkt 10: exakter Titel fuer Trail Sharpening
        assert build_garmin_title("Trail Sharpening", "Hill Session") == "CAIRN – Trail Sharpening · Hill Session"
