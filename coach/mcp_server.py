"""
coach/mcp_server.py
CAIRN Remote MCP Server — Streamable HTTP transport via FastMCP.

Security contract:
- Bearer token auth via MCP_API_KEY env var (never logged or echoed)
- No Garmin credentials in tool responses or logs
- Garmin write tools log to garmin_mcp_log (idempotent via external_id)
- preview_garmin_workout makes zero network calls
"""

from __future__ import annotations

import datetime
import decimal
import json
import logging
import os
from typing import Any

try:
    from mcp.server import MCPServer as FastMCP  # mcp 2.0+
except ImportError:
    from mcp.server import FastMCP  # type: ignore[assignment]  # mcp 1.x
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from coach.garmin_push import (
    GarminPushError,
    build_workout_payload,
    garmin_client,
    push_workout,
    schedule_workout,
)
from database.connection import get_connection

logger = logging.getLogger(__name__)

# ── FastMCP instance ───────────────────────────────────────────────────────────

mcp = FastMCP("CAIRN")

# ── Auth middleware ────────────────────────────────────────────────────────────

class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Enforce Authorization: Bearer <MCP_API_KEY> on every request."""

    async def dispatch(self, request: Request, call_next):
        api_key = os.getenv("MCP_API_KEY", "")
        if not api_key:
            logger.error("MCP_API_KEY is not set — rejecting all MCP requests")
            return Response("Server misconfigured: MCP_API_KEY missing", status_code=500)

        auth = request.headers.get("Authorization", "")
        token = auth[len("Bearer "):] if auth.startswith("Bearer ") else ""
        if token != api_key:
            return Response("Unauthorized", status_code=401)

        return await call_next(request)


# ── DB helpers ─────────────────────────────────────────────────────────────────

def _to_serializable(obj: Any) -> Any:
    """Recursively convert psycopg2 native types to JSON-safe Python types."""
    if isinstance(obj, dict):
        return {k: _to_serializable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_serializable(v) for v in obj]
    if isinstance(obj, (datetime.datetime, datetime.date)):
        return obj.isoformat()
    if isinstance(obj, decimal.Decimal):
        return float(obj)
    return obj


def _fetchall(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, row)) for row in cur.fetchall()]
        return _to_serializable(rows)
    finally:
        conn.close()


def _fetchone(sql: str, params: tuple = ()) -> dict[str, Any] | None:
    rows = _fetchall(sql, params)
    return rows[0] if rows else None


# ── Read-only tools ────────────────────────────────────────────────────────────

@mcp.tool()
def get_athlete_profile() -> dict:
    """
    Return Alexander's athlete profile, active training plan metadata, and coach context.
    Always call this first to understand the athlete before suggesting workouts or plan changes.
    """
    profile = _fetchone("SELECT * FROM athlete_profile ORDER BY id DESC LIMIT 1")
    plan = _fetchone(
        "SELECT id, goal_type, race_name, race_date, race_distance_km, "
        "start_date, end_date, status, created_at "
        "FROM plans ORDER BY created_at DESC LIMIT 1"
    )
    context = _fetchone("SELECT * FROM coach_context ORDER BY id DESC LIMIT 1")
    return {
        "athlete_profile": profile,
        "current_plan": plan,
        "coach_context": context,
    }


@mcp.tool()
def get_recent_activities(days: int = 28) -> list[dict]:
    """
    Return recent Garmin activities from the last N days (default 28).
    Includes distance, duration, elevation, heart rate, and training load.
    """
    return _fetchall(
        """
        SELECT activity_id, activity_name, activity_type, start_time,
               distance_km, duration_secs, elevation_gain_m,
               avg_hr, max_hr, avg_pace_per_km, training_load
        FROM trainings
        WHERE start_time >= now() - (INTERVAL '1 day' * %s)
        ORDER BY start_time DESC
        """,
        (days,),
    )


@mcp.tool()
def get_training_summary(weeks: int = 4) -> dict:
    """
    Return weekly training volume for the last N weeks (default 4).
    Shows total km, duration, elevation gain, and average heart rate per week.
    """
    rows = _fetchall(
        """
        SELECT
            date_trunc('week', start_time)::date    AS week_start,
            COUNT(*)                                 AS sessions,
            ROUND(SUM(distance_km)::numeric, 2)     AS total_km,
            SUM(duration_secs)                      AS total_secs,
            ROUND(AVG(avg_hr)::numeric, 1)           AS avg_hr,
            SUM(elevation_gain_m)                   AS total_elevation_m
        FROM trainings
        WHERE start_time >= now() - (INTERVAL '1 week' * %s)
        GROUP BY week_start
        ORDER BY week_start DESC
        """,
        (weeks,),
    )
    return {"weeks": rows, "period_weeks": weeks}


@mcp.tool()
def get_health_data(days: int = 7) -> list[dict]:
    """
    Return daily health metrics for the last N days (default 7).
    Includes sleep, HRV status, body battery, and resting heart rate.
    """
    return _fetchall(
        """
        SELECT *
        FROM daily_logs
        WHERE log_date >= CURRENT_DATE - (INTERVAL '1 day' * %s)
        ORDER BY log_date DESC
        """,
        (days,),
    )


@mcp.tool()
def get_checkins(days: int = 14) -> list[dict]:
    """
    Return athlete subjective check-ins and free-text notes for the last N days (default 14).
    Shows feel, structured notes, and athlete free-text entries.
    """
    return _fetchall(
        """
        SELECT log_date, feel, notes, athlete_text
        FROM daily_logs
        WHERE log_date >= CURRENT_DATE - (INTERVAL '1 day' * %s)
          AND (feel IS NOT NULL OR notes IS NOT NULL OR athlete_text IS NOT NULL)
        ORDER BY log_date DESC
        """,
        (days,),
    )


@mcp.tool()
def get_races_and_goals() -> dict:
    """Return upcoming races, training goals, and current plan targets."""
    plans = _fetchall(
        """
        SELECT id, goal_type, race_name, race_date, race_distance_km,
               start_date, end_date, status, created_at
        FROM plans
        ORDER BY created_at DESC
        LIMIT 5
        """
    )
    profile = _fetchone(
        "SELECT goal, current_fitness_level, weekly_volume_km "
        "FROM athlete_profile ORDER BY id DESC LIMIT 1"
    )
    return {"plans": plans, "profile_goals": profile}


@mcp.tool()
def get_planned_workouts(days: int = 14) -> list[dict]:
    """
    Return planned training sessions for the next N days (default 14).
    Includes session type, distance, target zone, structure, and Garmin workout ID if already pushed.
    """
    return _fetchall(
        """
        SELECT id, session_date, session_type, distance_km, duration_min,
               notes, zone, warmup_km, main_sets, main_pace,
               elevation_gain_m, garmin_workout_id
        FROM training_plan
        WHERE session_date BETWEEN CURRENT_DATE
                               AND CURRENT_DATE + (INTERVAL '1 day' * %s)
        ORDER BY session_date ASC
        """,
        (days,),
    )


# ── Garmin write tools ─────────────────────────────────────────────────────────

@mcp.tool()
def preview_garmin_workout(workout_def: dict) -> dict:
    """
    Validate a workout definition and return a parsed preview — zero network calls.
    Always call this before create_garmin_workout to confirm structure.

    workout_def shape:
    {
        "name": "CAIRN – 4x2min Z3",
        "estimated_duration_secs": 2160,
        "description": "optional",
        "steps": [
            {"type": "warmup", "duration_secs": 600, "target": {"type": "hr_zone", "zone": 2}},
            {"type": "repeat", "iterations": 4, "steps": [
                {"type": "interval", "duration_secs": 120, "target": {"type": "hr_zone", "zone": 3}},
                {"type": "recovery", "duration_secs": 120, "target": {"type": "hr_zone", "zone": 1}}
            ]},
            {"type": "cooldown", "duration_secs": 600, "target": {"type": "hr_zone", "zone": 2}}
        ]
    }
    """
    try:
        payload = build_workout_payload(workout_def)
        segment = payload.workoutSegments[0]
        steps_preview = []
        for i, step in enumerate(segment.workoutSteps):
            entry: dict[str, Any] = {"order": i + 1}
            if hasattr(step, "numberOfIterations"):
                entry["type"] = "repeat"
                entry["iterations"] = step.numberOfIterations
                entry["inner_steps"] = len(step.workoutSteps)
            else:
                step_type = getattr(step, "stepType", {})
                entry["type"] = step_type.get("stepTypeKey", "unknown") if isinstance(step_type, dict) else str(step_type)
                entry["duration_secs"] = getattr(step, "endConditionValue", None)
                target_type = getattr(step, "targetType", {})
                entry["target"] = (
                    target_type.get("workoutTargetTypeKey", "no.target")
                    if isinstance(target_type, dict) else str(target_type)
                )
                zone = getattr(step, "zoneNumber", None)
                if zone is not None:
                    entry["zone"] = zone
            steps_preview.append(entry)
        return {
            "valid": True,
            "workout_name": payload.workoutName,
            "estimated_duration_secs": payload.estimatedDurationInSecs,
            "description": payload.description,
            "step_count": len(segment.workoutSteps),
            "steps": steps_preview,
        }
    except Exception as exc:
        return {"valid": False, "error": str(exc)}


@mcp.tool()
def create_garmin_workout(
    workout_def: dict,
    date_str: str,
    external_id: str,
) -> dict:
    """
    Create and schedule a structured running workout on Garmin Connect.
    Idempotent: a duplicate external_id returns the existing record without re-uploading.

    Args:
        workout_def:  Workout definition (validate first with preview_garmin_workout).
        date_str:     Target date in YYYY-MM-DD format.
        external_id:  Unique caller ID, e.g. "plan_session_42_2026-08-15".
                      Use the training_plan row ID + date for natural idempotency.
    Returns:
        garmin_workout_id, garmin_schedule_id, workout_name, scheduled_date
    """
    conn = get_connection()
    try:
        # Idempotency: return existing success record if present
        with conn.cursor() as cur:
            cur.execute(
                "SELECT garmin_workout_id, garmin_schedule_id, workout_name, scheduled_date "
                "FROM garmin_mcp_log WHERE external_id = %s AND status = 'success'",
                (external_id,),
            )
            row = cur.fetchone()
        if row:
            logger.info("Idempotent hit: external_id=%s workout_id=%s", external_id, row[0])
            return {
                "garmin_workout_id": row[0],
                "garmin_schedule_id": row[1],
                "workout_name": row[2],
                "scheduled_date": row[3].isoformat() if hasattr(row[3], "isoformat") else str(row[3]),
                "idempotent": True,
            }

        # Insert pending log (ON CONFLICT DO NOTHING: safe for concurrent retries)
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO garmin_mcp_log
                       (external_id, action, status, workout_name, scheduled_date, metadata)
                   VALUES (%s, 'push', 'pending', %s, %s, %s)
                   ON CONFLICT (external_id) DO NOTHING""",
                (
                    external_id,
                    workout_def.get("name"),
                    date_str,
                    json.dumps({"source": "mcp_create_garmin_workout"}),
                ),
            )
            conn.commit()

        # Execute Garmin push (create + schedule)
        result = push_workout(workout_def, date_str)

        # Mark success
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE garmin_mcp_log
                   SET garmin_workout_id = %s,
                       garmin_schedule_id = %s,
                       status = 'success',
                       workout_name = %s
                   WHERE external_id = %s""",
                (
                    result["garmin_workout_id"],
                    result["garmin_schedule_id"],
                    result.get("workout_name"),
                    external_id,
                ),
            )
            conn.commit()

        logger.info(
            "MCP workout pushed: external_id=%s garmin_workout_id=%s date=%s",
            external_id,
            result["garmin_workout_id"],
            date_str,
        )
        return result

    except GarminPushError as exc:
        # Log failure — never log credential values
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE garmin_mcp_log SET status='failed', last_error=%s "
                    "WHERE external_id=%s",
                    (f"{type(exc).__name__}: {exc}", external_id),
                )
                conn.commit()
        except Exception:
            pass
        raise
    finally:
        conn.close()


@mcp.tool()
def move_garmin_workout(
    garmin_workout_id: int,
    new_date_str: str,
    old_schedule_id: int | None = None,
) -> dict:
    """
    Reschedule an existing Garmin workout to a new date.
    Deletes the old schedule entry (if provided) and creates a new one.

    Args:
        garmin_workout_id:  Garmin workout ID (from create_garmin_workout).
        new_date_str:       New target date in YYYY-MM-DD format.
        old_schedule_id:    Garmin schedule ID to remove (from garmin_mcp_log). Optional.
    """
    client = garmin_client()
    if old_schedule_id is not None:
        try:
            client.delete_workout_schedule(old_schedule_id)
            logger.info("Deleted old Garmin schedule_id=%s", old_schedule_id)
        except Exception as exc:
            logger.warning(
                "Could not delete old schedule %s: %s — continuing with reschedule",
                old_schedule_id,
                type(exc).__name__,
            )
    return schedule_workout(garmin_workout_id, new_date_str, client=client)


@mcp.tool()
def delete_garmin_workout(garmin_workout_id: int) -> dict:
    """
    Delete a workout from Garmin Connect (removes the workout definition and all schedule entries).

    Args:
        garmin_workout_id: Garmin workout ID to delete (from create_garmin_workout or garmin_mcp_log).
    """
    client = garmin_client()
    try:
        client.delete_workout(garmin_workout_id)
        logger.info("Deleted Garmin workout_id=%s", garmin_workout_id)
    except Exception as exc:
        raise RuntimeError(
            f"Failed to delete Garmin workout {garmin_workout_id}: {type(exc).__name__}"
        ) from exc
    return {"deleted": True, "garmin_workout_id": garmin_workout_id}


# ── ASGI app factory ───────────────────────────────────────────────────────────

def create_mcp_asgi_app() -> ASGIApp:
    """
    Return the MCPServer ASGI app wrapped in BearerAuthMiddleware.

    The inner app handles POST /mcp (Streamable HTTP transport).
    BearerAuthMiddleware is applied directly — no Starlette Mount wrapper —
    so the full path (/mcp) reaches the inner app unchanged.

    Requires: mcp>=1.0, MCP_API_KEY env var set.
    """
    inner = mcp.streamable_http_app()
    return BearerAuthMiddleware(inner)