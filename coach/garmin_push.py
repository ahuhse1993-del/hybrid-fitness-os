"""
coach/garmin_push.py
Garmin Connect workout push module for CAIRN MCP.
"""
from __future__ import annotations
import logging
import os
from typing import Any
from garminconnect import Garmin
from garminconnect.workout import (
    ConditionType, ExecutableStep, RunningWorkout,
    StepType, TargetType, WorkoutSegment, create_repeat_group,
)

logger = logging.getLogger(__name__)

class GarminPushError(Exception): pass
class GarminCredentialsError(GarminPushError): pass
class GarminLoginError(GarminPushError): pass
class GarminWorkoutValidationError(GarminPushError): pass
class GarminWorkoutCreateError(GarminPushError): pass
class GarminWorkoutScheduleError(GarminPushError): pass

_STEP_TYPE_MAP: dict[str, dict[str, Any]] = {
    "warmup":   {"stepTypeId": StepType.WARMUP,   "stepTypeKey": "warmup",   "displayOrder": 1},
    "cooldown": {"stepTypeId": StepType.COOLDOWN, "stepTypeKey": "cooldown", "displayOrder": 2},
    "interval": {"stepTypeId": StepType.INTERVAL, "stepTypeKey": "interval", "displayOrder": 3},
    "recovery": {"stepTypeId": StepType.RECOVERY, "stepTypeKey": "recovery", "displayOrder": 4},
}
_NO_TARGET: dict[str, Any] = {
    "workoutTargetTypeId": TargetType.NO_TARGET,
    "workoutTargetTypeKey": "no.target",
    "displayOrder": 1,
}
_HR_ZONE_TARGET_TYPE: dict[str, Any] = {
    "workoutTargetTypeId": 4,
    "workoutTargetTypeKey": "heart.rate.zone",
    "displayOrder": 4,
}
_TIME_END_CONDITION: dict[str, Any] = {
    "conditionTypeId": ConditionType.TIME,
    "conditionTypeKey": "time",
    "displayOrder": 2,
    "displayable": True,
}
_SPORT_TYPE_RUNNING: dict[str, Any] = {
    "sportTypeId": 1, "sportTypeKey": "running", "displayOrder": 1,
}

def garmin_client() -> Garmin:
    email = os.getenv("GARMIN_EMAIL")
    password = os.getenv("GARMIN_PASSWORD")
    missing = [k for k, v in {"GARMIN_EMAIL": email, "GARMIN_PASSWORD": password}.items() if not v]
    if missing:
        raise GarminCredentialsError(f"Missing required environment variable(s): {', '.join(missing)}")
    try:
        client = Garmin(email=email, password=password, is_cn=False)
        client.login()
        return client
    except Exception as exc:
        raise GarminLoginError("Garmin authentication failed") from exc

def _build_target(target_def: dict[str, Any] | None) -> dict[str, Any]:
    if target_def is None or target_def.get("type") == "no_target":
        return {"targetType": _NO_TARGET}
    if target_def["type"] == "hr_zone":
        zone = target_def.get("zone")
        try:
            zone = int(zone)
            assert 1 <= zone <= 5
        except (TypeError, ValueError, AssertionError):
            raise GarminWorkoutValidationError(f"hr_zone 'zone' must be 1-5, got {target_def.get('zone')!r}")
        return {"targetType": _HR_ZONE_TARGET_TYPE, "zoneNumber": zone}
    raise GarminWorkoutValidationError(
        f"Unsupported target type: {target_def['type']!r}. Supported: hr_zone, no_target"
    )

def _build_executable_step(step_def: dict[str, Any], step_order: int) -> ExecutableStep:
    step_type_key = step_def.get("type")
    if step_type_key not in _STEP_TYPE_MAP:
        raise GarminWorkoutValidationError(
            f"Unknown step type: {step_type_key!r}. Supported: {sorted(_STEP_TYPE_MAP)}"
        )
    duration_secs = step_def.get("duration_secs")
    try:
        duration_secs = float(duration_secs)
        assert duration_secs > 0
    except (TypeError, ValueError, AssertionError):
        raise GarminWorkoutValidationError(
            f"Step '{step_type_key}' requires duration_secs > 0, got {step_def.get('duration_secs')!r}"
        )
    target_kwargs = _build_target(step_def.get("target"))
    return ExecutableStep(
        stepOrder=step_order,
        stepType=_STEP_TYPE_MAP[step_type_key],
        endCondition=_TIME_END_CONDITION,
        endConditionValue=duration_secs,
        **target_kwargs,
    )

def _build_steps(steps_def: list[dict[str, Any]]) -> list:
    if not steps_def:
        raise GarminWorkoutValidationError("Steps list must not be empty")
    result = []
    for i, step_def in enumerate(steps_def, start=1):
        step_type = step_def.get("type")
        if step_type == "repeat":
            iterations = step_def.get("iterations")
            try:
                iterations = int(iterations)
                assert iterations >= 1
            except (TypeError, ValueError, AssertionError):
                raise GarminWorkoutValidationError(
                    f"'repeat' requires iterations >= 1, got {step_def.get('iterations')!r}"
                )
            inner_defs = step_def.get("steps")
            if not inner_defs:
                raise GarminWorkoutValidationError("'repeat' block must contain at least one inner step")
            inner_steps = [_build_executable_step(s, order) for order, s in enumerate(inner_defs, start=1)]
            result.append(create_repeat_group(iterations=iterations, workout_steps=inner_steps, step_order=i))
        else:
            result.append(_build_executable_step(step_def, i))
    return result

def build_workout_payload(workout_def: dict[str, Any]) -> RunningWorkout:
    name = str(workout_def.get("name", "")).strip()
    if not name:
        raise GarminWorkoutValidationError("workout_def requires a non-empty 'name'")
    duration = workout_def.get("estimated_duration_secs")
    try:
        duration = float(duration)
        assert duration > 0
    except (TypeError, ValueError, AssertionError):
        raise GarminWorkoutValidationError(
            f"estimated_duration_secs must be a positive number, got {workout_def.get('estimated_duration_secs')!r}"
        )
    steps_def = workout_def.get("steps")
    if not steps_def:
        raise GarminWorkoutValidationError("workout_def requires at least one step")
    steps = _build_steps(steps_def)
    segment = WorkoutSegment(segmentOrder=1, sportType=_SPORT_TYPE_RUNNING, workoutSteps=steps)
    return RunningWorkout(
        workoutName=name,
        estimatedDurationInSecs=int(duration),
        description=workout_def.get("description"),
        workoutSegments=[segment],
    )

def create_workout(workout_def: dict[str, Any], *, client: Garmin | None = None) -> dict[str, Any]:
    payload = build_workout_payload(workout_def)
    _client = client or garmin_client()
    try:
        result = _client.upload_running_workout(payload)
    except GarminPushError:
        raise
    except Exception as exc:
        raise GarminWorkoutCreateError(f"Garmin rejected workout creation: {type(exc).__name__}") from exc
    workout_id = result.get("workoutId")
    if not workout_id:
        raise GarminWorkoutCreateError("Garmin response is missing 'workoutId'")
    logger.info("Garmin workout created: id=%s name=%r", workout_id, workout_def.get("name"))
    return {"garmin_workout_id": int(workout_id), "workout_name": result.get("workoutName")}

def schedule_workout(garmin_workout_id: int, date_str: str, *, client: Garmin | None = None) -> dict[str, Any]:
    _client = client or garmin_client()
    try:
        result = _client.schedule_workout(garmin_workout_id, date_str)
    except GarminPushError:
        raise
    except Exception as exc:
        raise GarminWorkoutScheduleError(
            f"Garmin rejected scheduling for workout {garmin_workout_id}: {type(exc).__name__}"
        ) from exc
    schedule_id = result.get("workoutScheduleId")
    if not schedule_id:
        raise GarminWorkoutScheduleError(
            f"Garmin response is missing 'workoutScheduleId' for workout {garmin_workout_id}"
        )
    logger.info("Garmin workout scheduled: workout_id=%s schedule_id=%s date=%s",
                garmin_workout_id, schedule_id, date_str)
    return {"garmin_schedule_id": int(schedule_id), "scheduled_date": date_str}

def push_workout(workout_def: dict[str, Any], date_str: str) -> dict[str, Any]:
    client = garmin_client()
    created = create_workout(workout_def, client=client)
    scheduled = schedule_workout(created["garmin_workout_id"], date_str, client=client)
    return {**created, **scheduled}
