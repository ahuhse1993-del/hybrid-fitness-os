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
        bad_step = {"type": "warmup", "duration_secs": 600, "target": {"type": "hr_zone", "zone": 6}}
        with pytest.raises(GarminWorkoutValidationError, match="1-5"):
            build_workout_payload({**VALID_WORKOUT, "steps": [bad_step]})

    def test_hr_zone_0_raises(self):
        bad_step = {"type": "warmup", "duration_secs": 600, "target": {"type": "hr_zone", "zone": 0}}
        with pytest.raises(GarminWorkoutValidationError, match="1-5"):
            build_workout_payload({**VALID_WORKOUT, "steps": [bad_step]})

    def test_unknown_target_type_raises(self):
        bad_step = {"type": "warmup", "duration_secs": 600, "target": {"type": "pace_zone"}}
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
