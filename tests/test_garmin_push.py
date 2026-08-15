"""tests/test_garmin_push.py — alle Tests offline, kein echter Garmin-Call"""
import logging, os, sys
from unittest.mock import MagicMock, patch
import pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from coach.garmin_push import (
    GarminCredentialsError, GarminLoginError, GarminWorkoutCreateError,
    GarminWorkoutScheduleError, GarminWorkoutValidationError,
    build_workout_payload, create_workout, garmin_client, push_workout, schedule_workout,
)

VALID_WORKOUT = {
    "name": "CAIRN - 4x2min Z3 HR",
    "estimated_duration_secs": 2160,
    "description": "Test",
    "steps": [
        {"type": "warmup",   "duration_secs": 600, "target": {"type": "hr_zone", "zone": 2}},
        {"type": "repeat", "iterations": 4, "steps": [
            {"type": "interval", "duration_secs": 120, "target": {"type": "hr_zone", "zone": 3}},
            {"type": "recovery", "duration_secs": 120, "target": {"type": "hr_zone", "zone": 1}},
        ]},
        {"type": "cooldown", "duration_secs": 600, "target": {"type": "hr_zone", "zone": 2}},
    ],
}
EASY_RUN = {"name": "CAIRN - Easy", "estimated_duration_secs": 3600,
            "steps": [{"type": "interval", "duration_secs": 3600, "target": {"type": "no_target"}}]}

def _mock_client(workout_id=12345, schedule_id=99999):
    c = MagicMock()
    c.upload_running_workout.return_value = {"workoutId": workout_id, "workoutName": "CAIRN - 4x2min Z3 HR"}
    c.schedule_workout.return_value = {"workoutScheduleId": schedule_id}
    return c

class TestBuildWorkoutPayload:
    def test_valid_interval_builds(self):
        p = build_workout_payload(VALID_WORKOUT)
        assert p.workoutName == "CAIRN - 4x2min Z3 HR"
        assert p.estimatedDurationInSecs == 2160

    def test_valid_easy_run(self):
        p = build_workout_payload(EASY_RUN)
        assert p.workoutName == "CAIRN - Easy"

    def test_implicit_no_target(self):
        w = {"name": "Easy", "estimated_duration_secs": 3600,
             "steps": [{"type": "interval", "duration_secs": 3600}]}
        build_workout_payload(w)

    def test_top_level_step_count(self):
        p = build_workout_payload(VALID_WORKOUT)
        assert len(p.workoutSegments[0].workoutSteps) == 3

    def test_repeat_iterations(self):
        p = build_workout_payload(VALID_WORKOUT)
        assert p.workoutSegments[0].workoutSteps[1].numberOfIterations == 4

    def test_repeat_inner_steps(self):
        p = build_workout_payload(VALID_WORKOUT)
        assert len(p.workoutSegments[0].workoutSteps[1].workoutSteps) == 2

    def test_deterministic(self):
        p1 = build_workout_payload(VALID_WORKOUT)
        p2 = build_workout_payload(VALID_WORKOUT)
        assert p1.workoutName == p2.workoutName
        assert len(p1.workoutSegments[0].workoutSteps) == len(p2.workoutSegments[0].workoutSteps)

    def test_empty_name_raises(self):
        with pytest.raises(GarminWorkoutValidationError, match="non-empty"):
            build_workout_payload({**VALID_WORKOUT, "name": ""})

    def test_zero_duration_raises(self):
        with pytest.raises(GarminWorkoutValidationError, match="estimated_duration_secs"):
            build_workout_payload({**VALID_WORKOUT, "estimated_duration_secs": 0})

    def test_empty_steps_raises(self):
        with pytest.raises(GarminWorkoutValidationError, match="at least one step"):
            build_workout_payload({**VALID_WORKOUT, "steps": []})

    def test_unknown_step_type_raises(self):
        with pytest.raises(GarminWorkoutValidationError, match="Unknown step type"):
            build_workout_payload({**VALID_WORKOUT, "steps": [{"type": "sprint", "duration_secs": 60}]})

    def test_zero_step_duration_raises(self):
        with pytest.raises(GarminWorkoutValidationError, match="duration_secs"):
            build_workout_payload({**VALID_WORKOUT, "steps": [{"type": "warmup", "duration_secs": 0}]})

    def test_hr_zone_6_raises(self):
        # No-Target-Policy (2026-08-15): warmup/cooldown/recovery bekommen immer
        # no_target und _build_target() wird fuer sie gar nicht mehr aufgerufen —
        # die Zonenvalidierung selbst wird daher ueber einen interval-Step mit
        # enforce_garmin_target=True getestet, dem einzigen Weg noch ein echtes
        # Target zu setzen.
        bad_step = {"type": "interval", "duration_secs": 600,
                    "target": {"type": "hr_zone", "zone": 6}, "enforce_garmin_target": True}
        with pytest.raises(GarminWorkoutValidationError, match="1-5"):
            build_workout_payload({**VALID_WORKOUT, "steps": [bad_step]})

    def test_hr_zone_0_raises(self):
        bad_step = {"type": "interval", "duration_secs": 600,
                    "target": {"type": "hr_zone", "zone": 0}, "enforce_garmin_target": True}
        with pytest.raises(GarminWorkoutValidationError, match="1-5"):
            build_workout_payload({**VALID_WORKOUT, "steps": [bad_step]})

    def test_unknown_target_type_raises(self):
        bad_step = {"type": "interval", "duration_secs": 600,
                    "target": {"type": "pace_zone"}, "enforce_garmin_target": True}
        with pytest.raises(GarminWorkoutValidationError, match="Unsupported target type"):
            build_workout_payload({**VALID_WORKOUT, "steps": [bad_step]})

    def test_repeat_no_iterations_raises(self):
        bad = {"type": "repeat", "steps": [{"type": "interval", "duration_secs": 60}]}
        with pytest.raises(GarminWorkoutValidationError, match="iterations"):
            build_workout_payload({**VALID_WORKOUT, "steps": [bad]})

    def test_repeat_empty_inner_raises(self):
        bad = {"type": "repeat", "iterations": 4, "steps": []}
        with pytest.raises(GarminWorkoutValidationError, match="inner step"):
            build_workout_payload({**VALID_WORKOUT, "steps": [bad]})

class TestCredentials:
    def test_both_missing(self, monkeypatch):
        monkeypatch.delenv("GARMIN_EMAIL", raising=False)
        monkeypatch.delenv("GARMIN_PASSWORD", raising=False)
        with pytest.raises(GarminCredentialsError):
            garmin_client()

    def test_email_missing(self, monkeypatch):
        monkeypatch.delenv("GARMIN_EMAIL", raising=False)
        monkeypatch.setenv("GARMIN_PASSWORD", "pw")
        with pytest.raises(GarminCredentialsError, match="GARMIN_EMAIL"):
            garmin_client()

    def test_password_missing(self, monkeypatch):
        monkeypatch.setenv("GARMIN_EMAIL", "t@t.com")
        monkeypatch.delenv("GARMIN_PASSWORD", raising=False)
        with pytest.raises(GarminCredentialsError, match="GARMIN_PASSWORD"):
            garmin_client()

    def test_password_not_in_error(self, monkeypatch):
        monkeypatch.delenv("GARMIN_EMAIL", raising=False)
        monkeypatch.setenv("GARMIN_PASSWORD", "ultra_secret_pw")
        try:
            garmin_client()
        except GarminCredentialsError as exc:
            assert "ultra_secret_pw" not in str(exc)

class TestLoginFailure:
    def test_login_raises_login_error(self, monkeypatch):
        monkeypatch.setenv("GARMIN_EMAIL", "t@t.com")
        monkeypatch.setenv("GARMIN_PASSWORD", "pw")
        with patch("coach.garmin_push.Garmin") as M:
            M.return_value.login.side_effect = Exception("401")
            with pytest.raises(GarminLoginError, match="authentication failed"):
                garmin_client()

    def test_password_not_in_login_error(self, monkeypatch):
        monkeypatch.setenv("GARMIN_EMAIL", "t@t.com")
        monkeypatch.setenv("GARMIN_PASSWORD", "my_super_secret")
        with patch("coach.garmin_push.Garmin") as M:
            M.return_value.login.side_effect = Exception("err: my_super_secret")
            try:
                garmin_client()
            except GarminLoginError as exc:
                assert "my_super_secret" not in str(exc)

class TestCreateWorkout:
    def test_success(self):
        result = create_workout(VALID_WORKOUT, client=_mock_client(workout_id=12345))
        assert result["garmin_workout_id"] == 12345

    def test_upload_called_once(self):
        c = _mock_client()
        create_workout(VALID_WORKOUT, client=c)
        c.upload_running_workout.assert_called_once()

    def test_upload_exception_raises_create_error(self):
        c = MagicMock()
        c.upload_running_workout.side_effect = Exception("500")
        with pytest.raises(GarminWorkoutCreateError, match="rejected"):
            create_workout(VALID_WORKOUT, client=c)

    def test_missing_workout_id_raises(self):
        c = MagicMock()
        c.upload_running_workout.return_value = {}
        with pytest.raises(GarminWorkoutCreateError, match="missing 'workoutId'"):
            create_workout(VALID_WORKOUT, client=c)

    def test_validation_error_stops_before_upload(self):
        c = _mock_client()
        with pytest.raises(GarminWorkoutValidationError):
            create_workout({**VALID_WORKOUT, "name": ""}, client=c)
        c.upload_running_workout.assert_not_called()

    def test_no_credentials_in_logs(self, caplog, monkeypatch):
        monkeypatch.setenv("GARMIN_EMAIL", "alex@cairn.app")
        monkeypatch.setenv("GARMIN_PASSWORD", "garmin_secret_999")
        with caplog.at_level(logging.DEBUG, logger="coach.garmin_push"):
            create_workout(VALID_WORKOUT, client=_mock_client())
        assert "garmin_secret_999" not in caplog.text
        assert "alex@cairn.app" not in caplog.text

class TestScheduleWorkout:
    def test_success(self):
        result = schedule_workout(12345, "2026-08-15", client=_mock_client(schedule_id=99999))
        assert result["garmin_schedule_id"] == 99999
        assert result["scheduled_date"] == "2026-08-15"

    def test_called_with_correct_args(self):
        c = _mock_client()
        schedule_workout(12345, "2026-08-15", client=c)
        c.schedule_workout.assert_called_once_with(12345, "2026-08-15")

    def test_exception_raises_schedule_error(self):
        c = MagicMock()
        c.schedule_workout.side_effect = Exception("403")
        with pytest.raises(GarminWorkoutScheduleError, match="rejected scheduling"):
            schedule_workout(12345, "2026-08-15", client=c)

    def test_missing_schedule_id_raises(self):
        c = MagicMock()
        c.schedule_workout.return_value = {}
        with pytest.raises(GarminWorkoutScheduleError, match="missing 'workoutScheduleId'"):
            schedule_workout(12345, "2026-08-15", client=c)

class TestNoSecretsInLogs:
    def test_no_password_in_logs_during_push(self, caplog, monkeypatch):
        monkeypatch.setenv("GARMIN_EMAIL", "alex@cairn.app")
        monkeypatch.setenv("GARMIN_PASSWORD", "top_secret_pw")
        with patch("coach.garmin_push.garmin_client", return_value=_mock_client()):
            with caplog.at_level(logging.DEBUG, logger="coach.garmin_push"):
                push_workout(VALID_WORKOUT, "2026-08-15")
        assert "top_secret_pw" not in caplog.text
        assert "alex@cairn.app" not in caplog.text

    def test_no_password_in_exception(self, monkeypatch):
        monkeypatch.setenv("GARMIN_EMAIL", "alex@cairn.app")
        monkeypatch.setenv("GARMIN_PASSWORD", "top_secret_pw")
        c = MagicMock()
        c.upload_running_workout.side_effect = Exception("some error")
        with patch("coach.garmin_push.garmin_client", return_value=c):
            try:
                push_workout(VALID_WORKOUT, "2026-08-15")
            except Exception as exc:
                assert "top_secret_pw" not in str(exc)

class TestIdempotency:
    def test_same_input_same_structure(self):
        p1 = build_workout_payload(VALID_WORKOUT)
        p2 = build_workout_payload(VALID_WORKOUT)
        assert p1.workoutName == p2.workoutName
        assert len(p1.workoutSegments[0].workoutSteps) == len(p2.workoutSegments[0].workoutSteps)

    def test_result_contains_workout_id(self):
        result = create_workout(VALID_WORKOUT, client=_mock_client(workout_id=77777))
        assert "garmin_workout_id" in result
        assert isinstance(result["garmin_workout_id"], int)


def _target_key(step) -> str:
    """workoutTargetTypeKey eines gebauten ExecutableStep, z.B. 'no.target' oder 'heart.rate.zone'."""
    return step.targetType.get("workoutTargetTypeKey")


class TestNoTargetPolicy:
    """
    No-Target-Policy (2026-08-15): Garmin darf bei lockeren Einheiten
    (Warmup/Cooldown/Recovery, oder ganzen Session-Typen wie Easy Run) nie
    einen Zonen-Alert ausloesen. no_target ist der Default — ein echtes
    Target ist nur die bewusste Ausnahme (enforce_garmin_target=True auf
    einem interval-Step). Reine Objekterstellung, kein echter Garmin-Call
    noetig (garminconnect wird durch keinen dieser Tests kontaktiert —
    build_workout_payload() baut nur das Pydantic-Objekt lokal).
    """

    # 1. Easy Run mit Beschreibung "Z2" → Garmin no_target
    def test_easy_run_with_z2_description_gets_no_target(self):
        wd = {
            "name": "CAIRN – Easy Run", "estimated_duration_secs": 1800,
            "session_type": "Easy Run",
            "steps": [{"type": "interval", "duration_secs": 1800,
                       "intensity_description": "Z2",
                       "target": {"type": "hr_zone", "zone": 2}}],
        }
        p = build_workout_payload(wd)
        assert _target_key(p.workoutSegments[0].workoutSteps[0]) == "no.target"

    # 2. Long Run mit "Z2 / RPE 3–4" → Garmin no_target
    def test_long_run_with_z2_rpe_description_gets_no_target(self):
        wd = {
            "name": "CAIRN – Long Run", "estimated_duration_secs": 7200,
            "session_type": "Long Run",
            "steps": [{"type": "interval", "duration_secs": 7200,
                       "intensity_description": "Z2 / RPE 3–4",
                       "target": {"type": "hr_zone", "zone": 2}}],
        }
        p = build_workout_payload(wd)
        assert _target_key(p.workoutSegments[0].workoutSteps[0]) == "no.target"

    # 3. Warm-up Step mit Z2-Beschreibung → no_target
    def test_warmup_step_with_z2_description_gets_no_target(self):
        wd = {
            "name": "CAIRN – Tempo", "estimated_duration_secs": 1800,
            "session_type": "Tempo Session",  # NICHT in NO_TARGET_SESSION_TYPES
            "steps": [{"type": "warmup", "duration_secs": 600,
                       "intensity_description": "Z2",
                       "target": {"type": "hr_zone", "zone": 2}}],
        }
        p = build_workout_payload(wd)
        assert _target_key(p.workoutSegments[0].workoutSteps[0]) == "no.target"

    # 4. Cooldown Step mit Z1–Z2-Beschreibung → no_target
    def test_cooldown_step_with_z1_z2_description_gets_no_target(self):
        wd = {
            "name": "CAIRN – Tempo", "estimated_duration_secs": 1800,
            "session_type": "Tempo Session",
            "steps": [{"type": "cooldown", "duration_secs": 600,
                       "intensity_description": "Z1–Z2",
                       "target": {"type": "hr_zone", "zone": 1}}],
        }
        p = build_workout_payload(wd)
        assert _target_key(p.workoutSegments[0].workoutSteps[0]) == "no.target"

    # 5. Hill Sprints (8x 20s Belastung / 60s Pause) → beide Steps no_target
    def test_hill_sprints_both_steps_no_target(self):
        wd = {
            "name": "CAIRN – Hill Sprints", "estimated_duration_secs": 640,
            "session_type": "Hill Session",  # NICHT in NO_TARGET_SESSION_TYPES
            "steps": [{"type": "repeat", "iterations": 8, "steps": [
                {"type": "interval", "duration_secs": 20,
                 "intensity_description": "maximal"},  # kein enforce_garmin_target
                {"type": "recovery", "duration_secs": 60},
            ]}],
        }
        p = build_workout_payload(wd)
        repeat_group = p.workoutSegments[0].workoutSteps[0]
        assert _target_key(repeat_group.workoutSteps[0]) == "no.target"
        assert _target_key(repeat_group.workoutSteps[1]) == "no.target"

    # 6. Hill Repeats mit lockerem Bergablaufen (recovery) → Recovery no_target
    def test_hill_repeats_recovery_no_target_even_with_active_target(self):
        wd = {
            "name": "CAIRN – Hill Repeats", "estimated_duration_secs": 900,
            "session_type": "Hill Session",
            "steps": [{"type": "repeat", "iterations": 5, "steps": [
                {"type": "interval", "duration_secs": 90,
                 "enforce_garmin_target": True,
                 "target": {"type": "hr_zone", "zone": 4}},
                {"type": "recovery", "duration_secs": 90,
                 "intensity_description": "locker bergab"},
            ]}],
        }
        p = build_workout_payload(wd)
        repeat_group = p.workoutSegments[0].workoutSteps[0]
        assert _target_key(repeat_group.workoutSteps[1]) == "no.target"

    # 7. Pace-Intervalle mit enforce_garmin_target=True → aktive Intervalle duerfen Target haben
    def test_pace_intervals_with_enforce_flag_keep_real_target(self):
        wd = {
            "name": "CAIRN – Pace Intervalle", "estimated_duration_secs": 1200,
            "session_type": "Interval Session",  # NICHT in NO_TARGET_SESSION_TYPES
            "steps": [{"type": "repeat", "iterations": 5, "steps": [
                {"type": "interval", "duration_secs": 120,
                 "enforce_garmin_target": True,
                 "target": {"type": "hr_zone", "zone": 4}},
                {"type": "recovery", "duration_secs": 60},
            ]}],
        }
        p = build_workout_payload(wd)
        repeat_group = p.workoutSegments[0].workoutSteps[0]
        assert _target_key(repeat_group.workoutSteps[0]) == "heart.rate.zone"
        assert repeat_group.workoutSteps[0].zoneNumber == 4

    # 8. Trabpause zwischen Pace-Intervallen (recovery, ohne enforce) → no_target
    def test_recovery_between_pace_intervals_no_target(self):
        wd = {
            "name": "CAIRN – Pace Intervalle", "estimated_duration_secs": 1200,
            "session_type": "Interval Session",
            "steps": [{"type": "repeat", "iterations": 5, "steps": [
                {"type": "interval", "duration_secs": 120,
                 "enforce_garmin_target": True,
                 "target": {"type": "hr_zone", "zone": 4}},
                {"type": "recovery", "duration_secs": 60,
                 "target": {"type": "hr_zone", "zone": 1}},
            ]}],
        }
        p = build_workout_payload(wd)
        repeat_group = p.workoutSegments[0].workoutSteps[0]
        assert _target_key(repeat_group.workoutSteps[1]) == "no.target"

    # 9. RPE-basierte Uphill-Intervalle ohne enforce_garmin_target → no_target
    def test_rpe_uphill_intervals_without_enforce_flag_get_no_target(self):
        wd = {
            "name": "CAIRN – Uphill Intervalle", "estimated_duration_secs": 900,
            "session_type": "Hill Session",
            "steps": [{"type": "repeat", "iterations": 6, "steps": [
                {"type": "interval", "duration_secs": 90,
                 "intensity_description": "RPE 7"},  # kein enforce_garmin_target
                {"type": "recovery", "duration_secs": 60},
            ]}],
        }
        p = build_workout_payload(wd)
        repeat_group = p.workoutSegments[0].workoutSteps[0]
        assert _target_key(repeat_group.workoutSteps[0]) == "no.target"

    # 10. Rennrad-Endurance Session → alle Steps no_target
    def test_cycling_endurance_session_all_steps_no_target(self):
        wd = {
            "name": "CAIRN – Rennrad Endurance", "estimated_duration_secs": 5400,
            "session_type": "Cycling",  # in NO_TARGET_SESSION_TYPES
            "steps": [
                {"type": "warmup", "duration_secs": 600},
                {"type": "interval", "duration_secs": 4200,
                 "target": {"type": "hr_zone", "zone": 2}},
                {"type": "cooldown", "duration_secs": 600},
            ],
        }
        p = build_workout_payload(wd, sport="cycling")
        for step in p.workoutSegments[0].workoutSteps:
            assert _target_key(step) == "no.target"

    # 11. Recovery-Step erbt nicht das Target des vorherigen enforce_garmin_target-Intervalls
    def test_recovery_does_not_inherit_previous_interval_target(self):
        wd = {
            "name": "CAIRN – Intervalle", "estimated_duration_secs": 900,
            "session_type": "Interval Session",
            "steps": [{"type": "repeat", "iterations": 4, "steps": [
                {"type": "interval", "duration_secs": 120,
                 "enforce_garmin_target": True,
                 "target": {"type": "hr_zone", "zone": 5}},
                {"type": "recovery", "duration_secs": 90},  # kein eigenes target-Feld
            ]}],
        }
        p = build_workout_payload(wd)
        repeat_group = p.workoutSegments[0].workoutSteps[0]
        assert _target_key(repeat_group.workoutSteps[0]) == "heart.rate.zone"
        assert _target_key(repeat_group.workoutSteps[1]) == "no.target"

    # 12. Fehlt enforce_garmin_target → immer no_target (Default)
    def test_missing_enforce_flag_defaults_to_no_target(self):
        wd = {
            "name": "CAIRN – Intervalle", "estimated_duration_secs": 600,
            "session_type": "Interval Session",
            "steps": [{"type": "interval", "duration_secs": 600,
                       "target": {"type": "hr_zone", "zone": 3}}],  # kein enforce_garmin_target
        }
        p = build_workout_payload(wd)
        assert _target_key(p.workoutSegments[0].workoutSteps[0]) == "no.target"
