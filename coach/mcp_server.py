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
from mcp.server.transport_security import TransportSecuritySettings
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
from coach.session_routing import classify_for_push, resolve_sync_target, split_training_block
from coach.sync_utils import compute_content_hash
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
        "total_weeks, current_week, status, created_at "
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
    Includes distance, duration, heart rate.
    """
    return _fetchall(
        """
        SELECT id, date, type, notes,
               distance_km, duration_minutes,
               heart_rate_avg, garmin_id
        FROM trainings
        WHERE date >= CURRENT_DATE - (INTERVAL '1 day' * %s)
        ORDER BY date DESC
        """,
        (days,),
    )


@mcp.tool()
def get_training_summary(weeks: int = 4) -> dict:
    """
    Return weekly training volume for the last N weeks (default 4).
    Shows session count, total km, total duration, and average heart rate
    per week.
    """
    rows = _fetchall(
        """
        SELECT
            date_trunc('week', date)::date          AS week_start,
            COUNT(*)                                 AS sessions,
            ROUND(SUM(distance_km)::numeric, 2)     AS total_km,
            SUM(duration_minutes)                   AS total_duration_min,
            ROUND(AVG(heart_rate_avg)::numeric, 1)   AS avg_hr
        FROM trainings
        WHERE date >= CURRENT_DATE - (INTERVAL '1 week' * %s)
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
        WHERE date >= CURRENT_DATE - (INTERVAL '1 day' * %s)
        ORDER BY date DESC
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
        SELECT date, feel, notes, athlete_text
        FROM daily_logs
        WHERE date >= CURRENT_DATE - (INTERVAL '1 day' * %s)
          AND (feel IS NOT NULL OR notes IS NOT NULL OR athlete_text IS NOT NULL)
        ORDER BY date DESC
        """,
        (days,),
    )


@mcp.tool()
def get_races_and_goals() -> dict:
    """Return upcoming races, training goals, and current plan targets."""
    plans = _fetchall(
        """
        SELECT id, goal_type, race_name, race_date, race_distance_km,
               total_weeks, current_week, status, created_at
        FROM plans
        ORDER BY created_at DESC
        LIMIT 5
        """
    )
    # athlete_profile has no goal/current_fitness_level/weekly_volume_km columns —
    # long_term_goals is the only goal-related field that actually exists.
    profile = _fetchone(
        "SELECT long_term_goals FROM athlete_profile ORDER BY id DESC LIMIT 1"
    )
    return {"plans": plans, "profile_goals": profile}


@mcp.tool()
def get_planned_workouts(days: int = 14, start_date: str | None = None) -> list[dict]:
    """
    Return planned training sessions for N days starting at start_date
    (default: today). Includes session type, distance, target zone,
    structure, elevation_gain_m (CAIRN-only planning metadata, never sent to
    Garmin), and Garmin workout ID if already pushed.

    Args:
        days:       Number of days to include, counted from start_date.
        start_date: Optional YYYY-MM-DD. Omit for today (previous default
                    behavior unchanged). E.g. start_date="2026-08-16", days=3
                    returns sessions from 2026-08-16 through 2026-08-18.
    """
    if start_date:
        try:
            datetime.date.fromisoformat(start_date)
        except ValueError:
            return [{"error": f"start_date {start_date!r} ist kein gueltiges YYYY-MM-DD"}]
        range_start_sql = "%s::date"
        params = (start_date, start_date, days)
    else:
        range_start_sql = "CURRENT_DATE"
        params = (days,)

    return _fetchall(
        f"""
        SELECT id,
               (week_date + (day_of_week - 1) * INTERVAL '1 day')::date AS session_date,
               session_type, session_zone, distance_km, duration_min,
               notes, phase, garmin_workout_id, workout_steps, plan_week,
               elevation_gain_m
        FROM training_plan
        WHERE (week_date + (day_of_week - 1) * INTERVAL '1 day')::date
              BETWEEN {range_start_sql} AND {range_start_sql} + (INTERVAL '1 day' * %s)
        ORDER BY session_date ASC
        """,
        params,
    )


@mcp.tool()
def get_hevy_workouts(days: int = 60) -> list[dict]:
    """
    Return completed Hevy strength workouts from the last N days, with their
    logged exercises. Read-only, CAIRN DB only — reads trainings/hevy_exercises
    as already synced by data/hevy_sync.py, no live Hevy API call.

    Returns: [{date, hevy_id, title, duration_minutes,
               exercises: [{exercise_name, sets, reps_per_set, weight_kg_per_set}]}]
    Newest workout first; exercises within a workout in logged order.
    """
    rows = _fetchall(
        """
        SELECT t.id AS training_id, t.date, t.hevy_id, t.notes, t.duration_minutes,
               e.exercise_name, e.sets, e.reps_per_set, e.weight_kg_per_set, e.exercise_index
        FROM trainings t
        LEFT JOIN hevy_exercises e ON e.training_id = t.id
        WHERE t.type = 'WeightTraining'
          AND t.hevy_id IS NOT NULL
          AND t.date >= CURRENT_DATE - (INTERVAL '1 day' * %s)
        ORDER BY t.date DESC, t.id DESC, e.exercise_index ASC
        """,
        (days,),
    )

    workouts: dict[int, dict] = {}
    order: list[int] = []
    for row in rows:
        tid = row["training_id"]
        if tid not in workouts:
            workouts[tid] = {
                "date": row["date"], "hevy_id": row["hevy_id"], "title": row["notes"],
                "duration_minutes": row["duration_minutes"], "exercises": [],
            }
            order.append(tid)
        if row["exercise_name"] is not None:
            workouts[tid]["exercises"].append({
                "exercise_name": row["exercise_name"], "sets": row["sets"],
                "reps_per_set": row["reps_per_set"], "weight_kg_per_set": row["weight_kg_per_set"],
            })
    return [workouts[tid] for tid in order]


@mcp.tool()
def get_hevy_routines() -> list[dict]:
    """
    Return all CAIRN-known Hevy routines (synced via data/hevy_routines_sync.py
    into cairn_routines). Read-only, CAIRN DB only — no live Hevy API call.

    Returns: [{title, exercises: [str, ...]}]
    """
    return _fetchall("SELECT title, exercises FROM cairn_routines ORDER BY title")


@mcp.tool()
def list_garmin_workouts(start_date: str, end_date: str) -> list[dict]:
    """
    List existing Garmin calendar entries (workouts) in a date range, with their
    Garmin workout_id and schedule_id. Read-only — makes real Garmin API calls
    (client.get_scheduled_workouts per month) but writes nothing.

    Use this before upsert_training_block / reconciliation to see what's
    already on the Garmin calendar and avoid blind overwrites or duplicates.

    Args:
        start_date: YYYY-MM-DD
        end_date:   YYYY-MM-DD (inclusive)
    Returns:
        [{date, title, workout_id, schedule_id, sport_type}]
    """
    start = datetime.date.fromisoformat(start_date)
    end = datetime.date.fromisoformat(end_date)
    if end < start:
        return []

    client = garmin_client()
    months: set[tuple[int, int]] = set()
    cursor = start.replace(day=1)
    while cursor <= end:
        months.add((cursor.year, cursor.month))
        cursor = (cursor.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)

    seen_schedule_ids: set[int] = set()
    entries: list[dict] = []
    for year, month in sorted(months):
        data = client.get_scheduled_workouts(year, month)
        for item in data.get("calendarItems", []):
            if item.get("itemType") != "workout":
                continue
            item_date_str = item.get("date")
            if not item_date_str:
                continue
            item_date = datetime.date.fromisoformat(item_date_str)
            if not (start <= item_date <= end):
                continue
            schedule_id = item.get("id")
            if schedule_id in seen_schedule_ids:
                continue
            seen_schedule_ids.add(schedule_id)
            sport = item.get("sportTypeKey") or (item.get("sportType") or {}).get("sportTypeKey")
            entries.append({
                "date": item_date_str,
                "title": item.get("title"),
                "workout_id": item.get("workoutId"),
                "schedule_id": schedule_id,
                "sport_type": sport,
            })
    entries.sort(key=lambda e: e["date"])
    return entries


# ── Garmin write tools ─────────────────────────────────────────────────────────

@mcp.tool()
def preview_garmin_workout(workout_def: dict, sport: str = "running") -> dict:
    """
    Validate a workout definition and return a parsed preview — zero network calls.
    Always call this before create_garmin_workout to confirm structure.

    sport: "running" | "cycling" only. Strength Training must NEVER be pushed to
    Garmin (Hevy is the sole source for strength) — this is hard-rejected in
    coach/garmin_push.py, both by sport value and by workout_def['session_type'].

    workout_def shape:
    {
        "name": "CAIRN – 4x2min Z3",
        "estimated_duration_secs": 2160,
        "description": "optional",
        "optional": false,
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
        payload = build_workout_payload(workout_def, sport=sport)
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
            "sport": sport,
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
    sport: str = "running",
) -> dict:
    """
    Create and schedule a structured workout on Garmin Connect.
    Idempotent: a duplicate external_id returns the existing record without re-uploading.

    Args:
        workout_def:  Workout definition (validate first with preview_garmin_workout).
        date_str:     Target date in YYYY-MM-DD format.
        external_id:  Unique caller ID, e.g. "plan_session_42_2026-08-15".
                      Use the training_plan row ID + date for natural idempotency.
        sport:        "running" | "cycling" only. Strength Training must never
                      reach Garmin — Hevy is the sole source for strength training.
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
        result = push_workout(workout_def, date_str, sport=sport)

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
            # garminconnect 0.3.6 has no delete_workout_schedule() method —
            # the actual API is unschedule_workout(scheduled_workout_id).
            # Confirmed live against the real Garmin API on 2026-08-14.
            client.unschedule_workout(old_schedule_id)
            logger.info("Deleted old Garmin schedule_id=%s", old_schedule_id)
        except Exception as exc:
            logger.warning(
                "Could not delete old schedule %s: %s — continuing with reschedule",
                old_schedule_id,
                type(exc).__name__,
            )
    return schedule_workout(garmin_workout_id, new_date_str, client=client)


def _clear_training_plan_sync_state(conn, garmin_workout_id: int) -> int:
    """
    Räumt training_plan nach einer erfolgreichen Garmin-Löschung auf.
    training_plan hat keine garmin_schedule_id-Spalte (nur garmin_workout_id
    VARCHAR(100) — Schedule-IDs leben ausschließlich in garmin_mcp_log), daher
    nicht Teil dieses UPDATEs. garmin_workout_id wird als str verglichen,
    passend zum tatsächlichen Spaltentyp.
    """
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE training_plan
               SET garmin_workout_id = NULL,
                   sync_status = NULL,
                   sync_error = NULL,
                   content_hash = NULL,
                   last_synced_at = NULL
               WHERE garmin_workout_id = %s""",
            (str(garmin_workout_id),),
        )
        return cur.rowcount


@mcp.tool()
def delete_garmin_workout(garmin_workout_id: int) -> dict:
    """
    Delete a workout from Garmin Connect (removes the workout definition and all schedule entries).
    Also clears the matching training_plan row's sync state (garmin_workout_id,
    sync_status, sync_error, content_hash, last_synced_at), so a later
    push_sessions_to_garmin treats it as never-pushed rather than stale-synced.

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

    conn = get_connection()
    try:
        db_rows_cleared = _clear_training_plan_sync_state(conn, garmin_workout_id)
        conn.commit()
    finally:
        conn.close()

    return {"deleted": True, "garmin_workout_id": garmin_workout_id, "db_rows_cleared": db_rows_cleared}


@mcp.tool()
def bulk_delete_garmin_workouts(garmin_workout_ids: list[int]) -> dict:
    """
    Delete multiple Garmin workouts in one call — a single Garmin login for
    the whole batch, isolated try/except per ID (one failure never blocks
    the rest). Clears each deleted workout's training_plan sync state, same
    as delete_garmin_workout.

    Args:
        garmin_workout_ids: Garmin workout IDs to delete.
    Returns:
        {deleted: [ids...], failed: [{garmin_workout_id, error}...], db_rows_cleared: int}
    """
    client = garmin_client()
    conn = get_connection()
    deleted: list[int] = []
    failed: list[dict] = []
    db_rows_cleared = 0

    try:
        for gw_id in garmin_workout_ids:
            try:
                client.delete_workout(gw_id)
                logger.info("Deleted Garmin workout_id=%s", gw_id)
                db_rows_cleared += _clear_training_plan_sync_state(conn, gw_id)
                conn.commit()
                deleted.append(gw_id)
            except Exception as exc:
                conn.rollback()
                logger.error("Failed to delete Garmin workout_id=%s: %s", gw_id, exc)
                failed.append({"garmin_workout_id": gw_id, "error": f"{type(exc).__name__}: {exc}"})
    finally:
        conn.close()

    return {"deleted": deleted, "failed": failed, "db_rows_cleared": db_rows_cleared}


@mcp.tool()
def get_activity_analysis_data(
    activity_id: int | None = None,
    garmin_id: str | None = None,
    include_stream: bool = True,
    stream_resolution_m: int = 100,
) -> dict:
    """
    Vollständige Analyse-Daten für eine abgeschlossene Einheit.
    Zuerst get_recent_activities() aufrufen um die activity_id zu erhalten.
    Deckt das vollständige 25-Punkte-Analyse-Schema ab.

    Args:
        activity_id:         CAIRN trainings.id
        garmin_id:           Alternativ: Garmin activityId als String
        include_stream:      True = Activity Stream mitliefern (Standard).
                             False = nur Splits/Kopf, schnellere Antwort.
        stream_resolution_m: Auflösung des Streams in Metern (Standard 100m).
                             50 = feinkörniger, 200 = kompakter.
                             HR und Elevation sind auf Distanz-Achse ausgerichtet
                             → ermöglicht HR-Terrain-Korrelation, Pace-HR-Effizienz,
                             Cardiac Drift, Steigungsanalyse.

    Gibt zurück:
    - summary:        Aktivitätskopf mit allen Kennzahlen
    - splits:         km-weise Pace, HR, Cadence, Elevation
    - trail_metrics:  Aufstieg, Abstieg, Max-Steigung, Vertikalgeschwindigkeit
    - trail_segments: Aufstieg/Abstieg/Flach-Phasen mit Pace+HR pro Segment
    - stream:         HR + Elevation + Pace + Cadence auf Distanz-Achse
                      (aus activity_stream falls verfügbar, sonst hr_tracks Fallback)
    - hr_zones:       Z1–Z5 aus athlete_profile Grenzen, in Zeit + Prozent
    """
    # ── Aktivität laden ──
    if activity_id:
        training = _fetchone(
            "SELECT id, date, type, notes, distance_km, duration_minutes, "
            "heart_rate_avg, garmin_id, elevation_gain_m, elevation_loss_m, "
            "max_hr, avg_cadence, training_load, aerobic_effect, anaerobic_effect, "
            "vo2max_estimate, avg_power "
            "FROM trainings WHERE id = %s",
            (activity_id,)
        )
    elif garmin_id:
        training = _fetchone(
            "SELECT id, date, type, notes, distance_km, duration_minutes, "
            "heart_rate_avg, garmin_id, elevation_gain_m, elevation_loss_m, "
            "max_hr, avg_cadence, training_load, aerobic_effect, anaerobic_effect, "
            "vo2max_estimate, avg_power "
            "FROM trainings WHERE garmin_id = %s",
            (str(garmin_id),)
        )
    else:
        return {"error": "activity_id oder garmin_id erforderlich"}

    if not training:
        return {"error": "Aktivität nicht gefunden"}

    tid = training["id"]

    # ── Splits ──
    splits_raw = _fetchall(
        "SELECT split_number, distance_km, pace_seconds, heart_rate_avg, elevation_gain, cadence_avg "
        "FROM splits WHERE training_id = %s ORDER BY split_number",
        (tid,)
    )

    splits_out = []
    total_up = 0.0
    total_down = 0.0
    for s in splits_raw:
        elev = float(s["elevation_gain"]) if s["elevation_gain"] else 0.0
        if elev > 0:
            total_up += elev
        else:
            total_down += abs(elev)
        pace_s = s["pace_seconds"]
        splits_out.append({
            "km": s["split_number"],
            "distance_km": float(s["distance_km"]) if s["distance_km"] else None,
            "pace_per_km": f"{pace_s//60}:{str(pace_s%60).zfill(2)}" if pace_s else None,
            "pace_seconds": pace_s,
            "hr_avg": s["heart_rate_avg"],
            "elevation_m": round(elev, 1),
            "cadence": s["cadence_avg"],
        })

    # ── Trail-Segmente (aus Splits: Aufstieg / Abstieg / Flach) ──
    UPHILL_T, DOWNHILL_T = 30, -30   # m/km Steigung als Grenze
    classified = []
    for s in splits_raw:
        elev = float(s["elevation_gain"]) if s["elevation_gain"] else 0.0
        dist = float(s["distance_km"]) if s["distance_km"] else 1.0
        gradient = elev / dist if dist > 0 else 0
        terrain = "uphill" if gradient > UPHILL_T else ("downhill" if gradient < DOWNHILL_T else "flat")
        classified.append({"km": s["split_number"], "distance_km": dist, "pace_s": s["pace_seconds"],
                           "hr": s["heart_rate_avg"] or 0, "elevation_m": elev, "terrain": terrain})

    segments, current = [], None
    for point in classified:
        if current is None or point["terrain"] != current["terrain"]:
            if current: segments.append(current)
            current = {"terrain": point["terrain"], "start_km": point["km"] - 1, "end_km": point["km"],
                      "total_elevation_m": point["elevation_m"], "total_distance_km": point["distance_km"],
                      "pace_seconds": [point["pace_s"]] if point["pace_s"] else [],
                      "hr_values": [point["hr"]] if point["hr"] else []}
        else:
            current["end_km"] = point["km"]
            current["total_elevation_m"] += point["elevation_m"]
            current["total_distance_km"] += point["distance_km"]
            if point["pace_s"]: current["pace_seconds"].append(point["pace_s"])
            if point["hr"]: current["hr_values"].append(point["hr"])
    if current: segments.append(current)

    trail_segments = []
    for seg in segments:
        avg_pace_s = int(sum(seg["pace_seconds"]) / len(seg["pace_seconds"])) if seg["pace_seconds"] else None
        avg_hr = int(sum(seg["hr_values"]) / len(seg["hr_values"])) if seg["hr_values"] else None
        gradient_pct = round(abs(seg["total_elevation_m"]) / (seg["total_distance_km"] * 1000) * 100, 1) if seg["total_distance_km"] > 0 else 0
        trail_segments.append({
            "type": seg["terrain"],
            "start_km": seg["start_km"],
            "end_km": seg["end_km"],
            "distance_km": round(seg["total_distance_km"], 2),
            "elevation_m": round(seg["total_elevation_m"], 1),
            "gradient_pct": gradient_pct,
            "avg_pace_per_km": f"{avg_pace_s//60}:{str(avg_pace_s%60).zfill(2)}" if avg_pace_s else None,
            "avg_hr": avg_hr,
        })

    # ── Trail-Metriken (aus activity_stream falls verfügbar, sonst Splits-Summe) ──
    stream_ele_rows = _fetchall(
        "SELECT distance_m, elevation_m FROM activity_stream "
        "WHERE training_id = %s AND elevation_m IS NOT NULL ORDER BY distance_m",
        (tid,)
    )

    if stream_ele_rows:
        ascent = descent = 0.0
        max_grade_pct = 0.0
        elevations = [r["elevation_m"] for r in stream_ele_rows]
        for i in range(1, len(elevations)):
            diff = elevations[i] - elevations[i - 1]
            if diff > 0: ascent += diff
            else: descent += abs(diff)

        # Max-Steigung über 100m-Fenster
        pts = [(r["distance_m"], r["elevation_m"]) for r in stream_ele_rows]
        for i, (d1, e1) in enumerate(pts):
            for d2, e2 in pts[i + 1:]:
                if d2 - d1 >= 100:
                    grade = abs(e2 - e1) / (d2 - d1) * 100
                    if grade > max_grade_pct: max_grade_pct = grade
                    break

        total_dist_km = float(training["distance_km"]) if training["distance_km"] else 0
        avg_grade = (ascent / (total_dist_km * 1000) * 100) if total_dist_km > 0 else 0
        dur_h = (training["duration_minutes"] or 0) / 60
        trail_metrics = {
            "ascent_m": round(ascent, 1),
            "descent_m": round(descent, 1),
            "avg_grade_pct": round(avg_grade, 1),
            "max_grade_pct": round(max_grade_pct, 1),
            "vertical_speed_m_per_h": round(ascent / dur_h, 1) if dur_h > 0 else None,
            "source": "activity_stream",
        }
    else:
        trail_metrics = {
            "ascent_m": training["elevation_gain_m"] or round(total_up, 1),
            "descent_m": training["elevation_loss_m"] or round(total_down, 1),
            "avg_grade_pct": None,
            "max_grade_pct": None,
            "vertical_speed_m_per_h": None,
            "source": "splits_fallback",
        }

    # ── Activity Stream (distanzbasiert, ausgedünnt auf stream_resolution_m) ──
    stream_out = None
    stream_point_count = 0

    if include_stream:
        resolution = max(10, min(500, stream_resolution_m))
        raw_stream = _fetchall(
            "SELECT distance_m, heart_rate, elevation_m, speed_ms, cadence, power "
            "FROM activity_stream WHERE training_id = %s ORDER BY distance_m",
            (tid,)
        )

        if raw_stream:
            points = []
            last_d = -resolution
            for r in raw_stream:
                d = r["distance_m"]
                if d >= last_d + resolution:
                    p: dict[str, Any] = {"d": round(d)}
                    if r["heart_rate"] is not None: p["hr"] = r["heart_rate"]
                    if r["elevation_m"] is not None: p["ele"] = round(r["elevation_m"], 1)
                    if r["speed_ms"] is not None and r["speed_ms"] > 0:
                        p["pace_s"] = round(1000 / r["speed_ms"])
                    if r["cadence"] is not None: p["cad"] = r["cadence"]
                    if r["power"] is not None: p["pwr"] = r["power"]
                    points.append(p)
                    last_d = d
            stream_point_count = len(points)
            stream_out = {
                "source": "activity_stream",
                "resolution_m": resolution,
                "point_count": stream_point_count,
                "fields": "d=distance_m | hr=heart_rate_bpm | ele=elevation_m | pace_s=sec_per_km | cad=cadence_spm | pwr=power_watt",
                "points": points,
            }
        else:
            # Fallback: hr_tracks (zeitbasiert) — deutlich weniger nützlich für Terrain-Korrelation
            hr_fallback = _fetchall(
                "SELECT point_index, timestamp_ms, heart_rate FROM hr_tracks "
                "WHERE training_id = %s AND MOD(point_index, 30) = 0 ORDER BY point_index",
                (tid,)
            )
            if hr_fallback:
                stream_point_count = len(hr_fallback)
                stream_out = {
                    "source": "hr_tracks_fallback",
                    "note": "activity_stream noch nicht importiert — nur HR auf Zeitachse, keine Distanz-Korrelation möglich. Nächster sync_completed_activities Aufruf importiert den Stream.",
                    "resolution_s": 30,
                    "point_count": stream_point_count,
                    "fields": "point_index | timestamp_ms | heart_rate",
                    "data": [[p["point_index"], p["timestamp_ms"], p["heart_rate"]] for p in hr_fallback],
                }
            else:
                stream_out = {"available": False, "reason": "Kein Stream vorhanden"}

    # ── HR-Zonen (aus athlete_profile Grenzen) ──
    zones_out = []
    profile = _fetchone(
        "SELECT hr_z1_max, hr_z2_max, hr_z3_max, hr_z4_max, hr_z5_max "
        "FROM athlete_profile ORDER BY id DESC LIMIT 1"
    )
    if profile and any(v for v in profile.values() if v is not None):
        # HR-Daten: bevorzuge activity_stream, Fallback hr_tracks
        hr_vals_raw = _fetchall(
            "SELECT heart_rate FROM activity_stream "
            "WHERE training_id = %s AND heart_rate IS NOT NULL",
            (tid,)
        )
        if not hr_vals_raw:
            hr_vals_raw = _fetchall(
                "SELECT heart_rate FROM hr_tracks WHERE training_id = %s AND heart_rate IS NOT NULL",
                (tid,)
            )

        if hr_vals_raw:
            zone_bounds = [
                (1, "Regeneration", 0,                             profile["hr_z1_max"] or 130),
                (2, "Basis",        (profile["hr_z1_max"] or 130) + 1, profile["hr_z2_max"] or 148),
                (3, "Tempo",        (profile["hr_z2_max"] or 148) + 1, profile["hr_z3_max"] or 162),
                (4, "Schwelle",     (profile["hr_z3_max"] or 162) + 1, profile["hr_z4_max"] or 174),
                (5, "VO2max",       (profile["hr_z4_max"] or 174) + 1, 999),
            ]
            total_pts = len(hr_vals_raw)
            dur_min = training["duration_minutes"] or 0
            for z_num, z_label, z_min, z_max in zone_bounds:
                count = sum(1 for p in hr_vals_raw if z_min <= (p["heart_rate"] or 0) <= z_max)
                zones_out.append({
                    "zone": z_num,
                    "label": z_label,
                    "min_hr": z_min,
                    "max_hr": z_max if z_max < 999 else None,
                    "time_min": round(count / total_pts * dur_min, 1) if total_pts > 0 else 0,
                    "pct": round(count / total_pts * 100, 1) if total_pts > 0 else 0,
                })

    # ── Summary ──
    dist = training["distance_km"]
    dur = training["duration_minutes"]
    avg_pace_s = int(dur * 60 / float(dist)) if dist and dur and float(dist) > 0 else None

    summary = {
        "training_id": tid,
        "garmin_id": training["garmin_id"],
        "date": str(training["date"]) if training["date"] else None,
        "type": training["type"],
        "activity_name": training["notes"],
        "distance_km": float(dist) if dist else None,
        "duration_min": dur,
        "avg_pace_per_km": f"{avg_pace_s//60}:{str(avg_pace_s%60).zfill(2)}" if avg_pace_s else None,
        "avg_hr": training["heart_rate_avg"],
        "max_hr": training["max_hr"],
        "elevation_gain_m": trail_metrics["ascent_m"],
        "elevation_loss_m": trail_metrics["descent_m"],
        "avg_cadence": training["avg_cadence"],
        "training_load": training["training_load"],
        "aerobic_effect": training["aerobic_effect"],
        "anaerobic_effect": training["anaerobic_effect"],
        "vo2max_estimate": training["vo2max_estimate"],
        "avg_power": training["avg_power"],
        "splits_count": len(splits_out),
        "trail_segments_count": len(trail_segments),
        "hr_zones_available": len(zones_out) > 0,
        "stream_available": stream_out is not None and stream_out.get("available") is not False,
        "stream_source": stream_out.get("source") if stream_out else None,
        "stream_points": stream_point_count,
    }

    return {
        "summary": summary,
        "splits": splits_out,
        "trail_metrics": trail_metrics,
        "trail_segments": trail_segments,
        "hr_zones": zones_out if zones_out else None,
        "stream": stream_out,
    }


# ── Training-Block-Verwaltung (Rennen + Sessions + Milestones) ─────────────────

def _validate_session_fields(idx: int, s: dict) -> list[str]:
    errors = []
    if not s.get("external_id"):
        errors.append(f"Session {idx}: external_id fehlt")
    date_str = s.get("date")
    if not date_str:
        errors.append(f"Session {idx}: date fehlt")
    else:
        try:
            datetime.date.fromisoformat(date_str)
        except ValueError:
            errors.append(f"Session {idx}: date {date_str!r} ist kein gueltiges YYYY-MM-DD")
    if not s.get("session_type"):
        errors.append(f"Session {idx}: session_type fehlt")
    return errors


def _week_date_and_dow(session_date: datetime.date) -> tuple[datetime.date, int]:
    """training_plan speichert week_date (Montag der Woche) + day_of_week (1=Mo..7=So)."""
    dow = session_date.isoweekday()
    week_date = session_date - datetime.timedelta(days=dow - 1)
    return week_date, dow


@mcp.tool()
def preview_training_block(plan: dict) -> dict:
    """
    Validate a full training block (race + sessions) — NO writes, read-only.
    Always call this before upsert_training_block and show the result to the
    athlete for confirmation, especially when race_date_change is non-null.

    plan shape:
    {
      "race": {"name": str, "race_date": "YYYY-MM-DD", "race_distance_km": float, "goal_type": str},
      "sessions": [
        {"external_id": str, "date": "YYYY-MM-DD", "session_type": str,
         "distance_km": float, "duration_min": int, "session_zone": str, "notes": str,
         "elevation_gain_m": int},  # optional, CAIRN-only — never sent to Garmin
        ...
      ]
    }

    Returns: valid, summary (create/update/unchanged), race_date_change,
    conflicts (hard errors — missing fields, duplicate external_id within the
    block), warnings (collisions, empty block), sessions_preview.
    """
    conflicts: list[str] = []
    warnings: list[str] = []
    race = plan.get("race") or {}
    sessions = plan.get("sessions") or []

    if not race.get("race_date"):
        conflicts.append("race.race_date fehlt")
    if not sessions:
        warnings.append("Keine Sessions im Block")

    for i, s in enumerate(sessions):
        conflicts.extend(_validate_session_fields(i, s))

    # Duplikate innerhalb des eingereichten Blocks
    ext_ids = [s.get("external_id") for s in sessions if s.get("external_id")]
    for dupe in sorted({x for x in ext_ids if ext_ids.count(x) > 1}):
        conflicts.append(f"external_id {dupe!r} kommt mehrfach im selben Block vor")

    # Kollisionen: gleiches Datum + gleicher session_type mehrfach im Block
    # (unterschiedliche session_types am selben Tag sind normal, z.B. Gym + Lauf)
    date_type_pairs = [(s.get("date"), s.get("session_type")) for s in sessions if s.get("date") and s.get("session_type")]
    for pair in sorted({p for p in date_type_pairs if date_type_pairs.count(p) > 1}):
        warnings.append(f"Kollision: {pair[1]!r} kommt am {pair[0]} mehrfach im selben Block vor")

    # Race-Date-Aenderung gegen den aktuell aktiven Plan — eigenes Feld, blockiert
    # 'valid' NICHT (strukturell ok), aber upsert_training_block verlangt dafuer
    # explizit confirm_race_date_change=True.
    active_plan = _fetchone(
        "SELECT id, race_date, race_name FROM plans WHERE status='active' ORDER BY created_at DESC LIMIT 1"
    )
    race_date_change = None
    if active_plan and race.get("race_date"):
        old_date = active_plan["race_date"]
        old_date_str = old_date.isoformat() if hasattr(old_date, "isoformat") else str(old_date)
        new_date_str = race["race_date"]
        if old_date_str != new_date_str:
            race_date_change = {
                "plan_id": active_plan["id"],
                "race_name": active_plan["race_name"],
                "old_race_date": old_date_str,
                "new_race_date": new_date_str,
            }
            warnings.append(
                f"Race-Date-Aenderung: aktiver Plan (id={active_plan['id']}, {active_plan['race_name']}) "
                f"hat race_date={old_date_str}, dieser Block will {new_date_str} — "
                f"upsert_training_block lehnt das ohne confirm_race_date_change=True ab."
            )

    # Sessions klassifizieren: create / update / unchanged, gegen echte DB-Zeilen
    create_count = update_count = unchanged_count = 0
    sessions_preview = []
    for s in sessions:
        ext_id = s.get("external_id")
        existing = _fetchone(
            "SELECT id, session_type, distance_km, duration_min, session_zone "
            "FROM training_plan WHERE external_id = %s", (ext_id,)
        ) if ext_id else None

        action = "create"
        if existing:
            existing_distance = float(existing["distance_km"]) if existing.get("distance_km") is not None else None
            same = (
                str(existing.get("session_type")) == str(s.get("session_type"))
                and existing_distance == s.get("distance_km")
                and existing.get("duration_min") == s.get("duration_min")
                and (existing.get("session_zone") or None) == (s.get("session_zone") or None)
            )
            action = "unchanged" if same else "update"

        if action == "create":
            create_count += 1
        elif action == "update":
            update_count += 1
        else:
            unchanged_count += 1

        routing = classify_for_push(s.get("session_type", ""), s.get("sport_hint"))
        sessions_preview.append({
            "external_id": ext_id, "date": s.get("date"), "session_type": s.get("session_type"),
            "action": action, "existing_training_plan_id": existing["id"] if existing else None,
            "sync_target": routing["sync_target"], "garmin_sport": routing["garmin_sport"],
        })

        if routing["sync_target"] == "hevy" and s.get("hevy_routine_key"):
            match = _fetchone("SELECT 1 AS ok FROM cairn_routines WHERE title = %s", (s["hevy_routine_key"],))
            if not match:
                warnings.append(
                    f"Session {ext_id!r}: hevy_routine_key {s['hevy_routine_key']!r} "
                    f"nicht in cairn_routines gefunden."
                )

    routing_split = split_training_block(sessions)
    routing_summary = {k: len(v) for k, v in routing_split.items()}

    return {
        "valid": len(conflicts) == 0,
        "summary": {"create": create_count, "update": update_count, "unchanged": unchanged_count, "total": len(sessions)},
        "routing_summary": routing_summary,
        "race_date_change": race_date_change,
        "conflicts": conflicts,
        "warnings": warnings,
        "sessions_preview": sessions_preview,
    }


@mcp.tool()
def upsert_planned_workout(
    external_id: str,
    date: str,
    session_type: str,
    sport: str | None = None,
    name: str | None = None,
    duration_min: int | None = None,
    distance_km: float | None = None,
    structure: dict | list | None = None,
    target: dict | None = None,
    notes: str | None = None,
    source: str | None = None,
    sync_target: str | None = None,
    elevation_gain_m: int | None = None,
) -> dict:
    """
    Save a single planned session in CAIRN. Idempotent via external_id
    (ON CONFLICT DO UPDATE, never duplicates). CAIRN-only write — never
    calls Garmin or Hevy directly; call create_garmin_workout separately
    afterward for sessions where the result's sync_target == "garmin".

    Args:
        external_id:  Stable caller ID for idempotency.
        date:         YYYY-MM-DD.
        session_type: e.g. "Easy Run", "Cross Training", "Strength Training".
        sport:        Optional hint, e.g. "cycling" for a Cross Training
                       session that IS a bike ride (see sync_target rules).
        name:         Display title, e.g. "Upper Body" — separate from notes.
        structure:    Workout step structure (stored in training_plan.workout_steps).
        target:       HR/pace target structure, e.g. {"type":"hr_zone","zone":2}.
        source:       Optional origin marker, e.g. "hevy" for strength sessions.
        sync_target:  Optional caller-requested "garmin"|"hevy"|"cairn_only".
                      VALIDATED, never trusted blindly: requesting "garmin" for
                      a Strength/Mobility/Core/Rest Day session_type is a hard
                      error (see coach/session_routing.py). A more conservative
                      request (e.g. "cairn_only" for a run) is honored as-is.
                      If omitted, sync_target is computed automatically.
        elevation_gain_m: Optional planned elevation gain in meters. CAIRN-only
                      metadata — never sent to Garmin (garmin_push.py has no
                      concept of this field and workout_def is built explicitly
                      key-by-key, so it can never leak into a Garmin push).

    Returns: training_plan id, sync_target (the actually applied value,
    post-validation), garmin_sport, created (bool).
    """
    if not external_id or not date or not session_type:
        return {"error": "external_id, date und session_type sind erforderlich."}
    try:
        session_date = datetime.date.fromisoformat(date)
    except ValueError:
        return {"error": f"date {date!r} ist kein gueltiges YYYY-MM-DD"}

    try:
        routing = resolve_sync_target(session_type, requested_sync_target=sync_target, sport_hint=sport)
    except ValueError as exc:
        return {"error": str(exc)}

    week_date, day_of_week = _week_date_and_dow(session_date)
    resolved_source = source or routing["source"]

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM plans WHERE status='active' ORDER BY created_at DESC LIMIT 1"
            )
            active = cur.fetchone()
            plan_id = active[0] if active else None

            # Vor dem Schreiben: bestehenden garmin_workout_id/session_zone
            # merken, um danach zu erkennen ob eine bereits gepushte Session
            # inhaltlich geaendert wurde (-> dirty markieren, Schritt 6).
            cur.execute(
                "SELECT garmin_workout_id, session_zone FROM training_plan WHERE external_id = %s",
                (external_id,),
            )
            prior = cur.fetchone()
            existing_garmin_id, existing_session_zone = prior if prior else (None, None)

            columns = [
                "external_id", "week_date", "day_of_week", "session_type", "sport",
                "name", "distance_km", "duration_min", "notes", "workout_steps",
                "target", "source", "garmin_push_required", "sync_target",
                "elevation_gain_m",
            ]
            values = [
                external_id, week_date, day_of_week, session_type, sport,
                name, distance_km, duration_min, notes,
                json.dumps(structure) if structure is not None else None,
                json.dumps(target) if target is not None else None,
                resolved_source, routing["garmin_push_required"], routing["sync_target"],
                elevation_gain_m,
            ]
            if plan_id is not None:
                columns.append("plan_id")
                values.append(plan_id)

            placeholders = ", ".join(["%s"] * len(columns))
            update_clause = ", ".join(
                f"{c}=EXCLUDED.{c}" for c in columns if c != "external_id"
            ) + ", updated_at=now()"
            cur.execute(
                f"""INSERT INTO training_plan ({", ".join(columns)})
                    VALUES ({placeholders})
                    ON CONFLICT (external_id) DO UPDATE SET {update_clause}
                    RETURNING id, (xmax = 0) AS inserted""",
                values,
            )
            row_id, inserted = cur.fetchone()

            # Schritt 6 — Dirty-Marking: wenn diese Session schon einen
            # garmin_workout_id hatte (also bereits gepusht war) UND sich der
            # Inhalt gegenueber dem gespeicherten content_hash geaendert hat,
            # sync_status='dirty' setzen, damit der naechste Batch-Push sie
            # erneut aufnimmt (sessions_to_push() filtert genau darauf).
            if existing_garmin_id:
                new_hash = compute_content_hash({
                    "date": date, "session_type": session_type,
                    "distance_km": distance_km, "duration_min": duration_min,
                    "notes": notes, "workout_steps": structure,
                    "session_zone": existing_session_zone,
                    "name": name, "target": target,
                    "elevation_gain_m": elevation_gain_m,
                })
                cur.execute("SELECT content_hash FROM training_plan WHERE id = %s", (row_id,))
                old_hash = cur.fetchone()[0]
                if new_hash != old_hash:
                    cur.execute(
                        "UPDATE training_plan SET sync_status='dirty', content_hash=%s WHERE id=%s",
                        (new_hash, row_id),
                    )
        conn.commit()
        logger.info(
            "upsert_planned_workout committed: id=%s external_id=%s sync_target=%s created=%s",
            row_id, external_id, routing["sync_target"], inserted,
        )
        return {
            "id": row_id, "created": bool(inserted),
            "sync_target": routing["sync_target"], "garmin_sport": routing["garmin_sport"],
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@mcp.tool()
def upsert_training_block(plan: dict, confirm_race_date_change: bool = False) -> dict:
    """
    Save race + training block + all sessions atomically (single DB transaction —
    all rows or none). Idempotent per session via external_id (ON CONFLICT DO
    UPDATE, never duplicates). Always call preview_training_block first.

    If the block's race.race_date differs from the currently active plan's
    race_date, this call is REFUSED unless confirm_race_date_change=True is
    passed explicitly — race-date changes on an active plan must be a
    deliberate, confirmed decision, never a silent side effect.

    plan shape: same as preview_training_block.
    Returns: plan_id, created, updated, race_date_changed.
    """
    race = plan.get("race") or {}
    sessions = plan.get("sessions") or []

    errors = []
    if not race.get("race_date"):
        errors.append("race.race_date fehlt")
    for i, s in enumerate(sessions):
        errors.extend(_validate_session_fields(i, s))
    ext_ids = [s.get("external_id") for s in sessions if s.get("external_id")]
    for dupe in sorted({x for x in ext_ids if ext_ids.count(x) > 1}):
        errors.append(f"external_id {dupe!r} kommt mehrfach im selben Block vor")
    if errors:
        return {"error": "Validierung fehlgeschlagen — zuerst preview_training_block aufrufen.", "conflicts": errors}

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, race_date, race_name FROM plans WHERE status='active' ORDER BY created_at DESC LIMIT 1"
            )
            row = cur.fetchone()
            active_plan_id, active_race_date, active_race_name = row if row else (None, None, None)

            race_date_changed = False
            if active_plan_id and active_race_date and str(active_race_date) != race["race_date"]:
                if not confirm_race_date_change:
                    conn.rollback()
                    return {
                        "error": "Race-Date-Aenderung erkannt, aber nicht bestaetigt.",
                        "plan_id": active_plan_id,
                        "old_race_date": str(active_race_date),
                        "new_race_date": race["race_date"],
                        "hint": "upsert_training_block(plan, confirm_race_date_change=True) erneut aufrufen, "
                                "um diese Aenderung bewusst zu bestaetigen.",
                    }
                race_date_changed = True
                logger.info(
                    "Race date change CONFIRMED for plan_id=%s: %s -> %s",
                    active_plan_id, active_race_date, race["race_date"],
                )

            if active_plan_id:
                plan_id = active_plan_id
                cur.execute(
                    """UPDATE plans SET name=%s, goal_type=%s, race_name=%s, race_date=%s,
                           race_distance_km=%s
                       WHERE id=%s""",
                    (
                        race.get("name") or race.get("race_name"), race.get("goal_type"),
                        race.get("name") or race.get("race_name"), race["race_date"],
                        race.get("race_distance_km"), plan_id,
                    ),
                )
            else:
                cur.execute(
                    """INSERT INTO plans (name, goal_type, race_name, race_date, race_distance_km, status)
                       VALUES (%s, %s, %s, %s, %s, 'active') RETURNING id""",
                    (
                        race.get("name") or race.get("race_name"), race.get("goal_type"),
                        race.get("name") or race.get("race_name"), race["race_date"],
                        race.get("race_distance_km"),
                    ),
                )
                plan_id = cur.fetchone()[0]

            created = updated = 0
            routing_warnings: list[str] = []
            routing_summary = {"garmin": 0, "hevy": 0, "cairn_only": 0}
            for s in sessions:
                session_date = datetime.date.fromisoformat(s["date"])
                week_date, day_of_week = _week_date_and_dow(session_date)

                # Zentrale Klassifizierung — einzige Stelle, die ueber Garmin-
                # Zustaendigkeit entscheidet. Kraft/Mobility/Core/Rest Day
                # bekommen hier garmin_push_required=False und werden von
                # KEINEM Code-Pfad automatisch an Garmin geschickt.
                routing = classify_for_push(s["session_type"], s.get("sport_hint"))
                routing_summary[routing["sync_target"]] += 1

                hevy_routine_key = s.get("hevy_routine_key") if routing["sync_target"] == "hevy" else None
                if hevy_routine_key:
                    cur.execute("SELECT 1 FROM cairn_routines WHERE title = %s", (hevy_routine_key,))
                    if not cur.fetchone():
                        routing_warnings.append(
                            f"Session {s['external_id']!r}: hevy_routine_key {hevy_routine_key!r} "
                            f"nicht in cairn_routines gefunden — Referenz wird trotzdem gespeichert, "
                            f"aber ohne bestaetigte Hevy-Routine (nichts erfunden)."
                        )

                # sport: expliziter sport_hint hat Vorrang, sonst der aus session_type
                # berechnete garmin_sport (None fuer hevy/cairn_only) — damit die
                # Garmin-Batch-Engine (coach/garmin_batch.py) running/cycling immer
                # korrekt unterscheiden kann, ohne dass jeder Aufrufer sport explizit
                # mitgeben muss.
                resolved_sport = s.get("sport_hint") or routing["garmin_sport"]

                # Vor dem Schreiben: bestehenden garmin_workout_id/content_hash
                # merken, um danach zu erkennen ob eine bereits gepushte Session
                # inhaltlich geaendert wurde (-> dirty markieren, Schritt 6).
                cur.execute(
                    "SELECT garmin_workout_id, content_hash FROM training_plan WHERE external_id = %s",
                    (s["external_id"],),
                )
                prior = cur.fetchone()
                existing_garmin_id, old_hash = prior if prior else (None, None)

                # status wird bei INSERT gesetzt, aber bei ON CONFLICT bewusst NICHT
                # überschrieben — ein erneutes upsert_training_block darf einen bereits
                # von sync_hevy_completions auf 'completed' gesetzten Status nicht
                # stillschweigend auf 'planned' zurücksetzen.
                cur.execute(
                    """INSERT INTO training_plan
                           (external_id, week_date, day_of_week, session_type, session_zone,
                            distance_km, duration_min, notes, plan_id,
                            status, source, garmin_push_required, hevy_routine_key,
                            sport, sync_target, name, target, elevation_gain_m)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (external_id) DO UPDATE SET
                           week_date=EXCLUDED.week_date, day_of_week=EXCLUDED.day_of_week,
                           session_type=EXCLUDED.session_type, session_zone=EXCLUDED.session_zone,
                           distance_km=EXCLUDED.distance_km, duration_min=EXCLUDED.duration_min,
                           notes=EXCLUDED.notes, plan_id=EXCLUDED.plan_id,
                           source=EXCLUDED.source, garmin_push_required=EXCLUDED.garmin_push_required,
                           hevy_routine_key=EXCLUDED.hevy_routine_key,
                           sport=EXCLUDED.sport, sync_target=EXCLUDED.sync_target,
                           name=EXCLUDED.name, target=EXCLUDED.target,
                           elevation_gain_m=EXCLUDED.elevation_gain_m, updated_at=now()
                       RETURNING id, (xmax = 0) AS inserted""",
                    (
                        s["external_id"], week_date, day_of_week, s["session_type"],
                        s.get("session_zone"), s.get("distance_km"), s.get("duration_min"),
                        s.get("notes"), plan_id,
                        s.get("status", "planned"), routing["source"],
                        routing["garmin_push_required"], hevy_routine_key,
                        resolved_sport, routing["sync_target"], s.get("name"),
                        json.dumps(s["target"]) if s.get("target") is not None else None,
                        s.get("elevation_gain_m"),
                    ),
                )
                row_id, inserted = cur.fetchone()
                if inserted:
                    created += 1
                else:
                    updated += 1

                # Schritt 6 — Dirty-Marking (siehe upsert_planned_workout fuer
                # dieselbe Logik): bereits gepushte Session inhaltlich geaendert?
                if existing_garmin_id:
                    new_hash = compute_content_hash({
                        "date": s["date"], "session_type": s["session_type"],
                        "distance_km": s.get("distance_km"), "duration_min": s.get("duration_min"),
                        "notes": s.get("notes"), "workout_steps": None,
                        "session_zone": s.get("session_zone"),
                        "name": s.get("name"), "target": s.get("target"),
                        "elevation_gain_m": s.get("elevation_gain_m"),
                    })
                    if new_hash != old_hash:
                        cur.execute(
                            "UPDATE training_plan SET sync_status='dirty', content_hash=%s WHERE id=%s",
                            (new_hash, row_id),
                        )

        conn.commit()
        logger.info(
            "upsert_training_block committed: plan_id=%s created=%s updated=%s race_date_changed=%s routing=%s",
            plan_id, created, updated, race_date_changed, routing_summary,
        )
        return {
            "plan_id": plan_id, "created": created, "updated": updated,
            "race_date_changed": race_date_changed,
            "routing_summary": routing_summary,
            "warnings": routing_warnings,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@mcp.tool()
def upsert_milestones(milestones: list) -> dict:
    """
    Save milestones (title, criterion, target_date, status, evidence) into the
    milestones table. Matched on (plan_id, step_number) — updates existing rows
    instead of duplicating (no DB-level unique constraint on that pair exists,
    so this does an explicit check-then-update-or-insert per row).

    milestones shape:
    [{"plan_id": int, "step_number": int, "title": str, "criterion": str,
      "target_date": "YYYY-MM-DD", "status": "open|achieved|changed",
      "evidence": str, "notes": str}, ...]
    """
    if not milestones:
        return {"created": 0, "updated": 0}

    conn = get_connection()
    try:
        created = updated = 0
        with conn.cursor() as cur:
            for m in milestones:
                if not m.get("plan_id") or m.get("step_number") is None or not m.get("title"):
                    raise ValueError(f"Milestone fehlt plan_id/step_number/title: {m!r}")
                cur.execute(
                    "SELECT id FROM milestones WHERE plan_id=%s AND step_number=%s",
                    (m["plan_id"], m["step_number"]),
                )
                row = cur.fetchone()
                if row:
                    cur.execute(
                        """UPDATE milestones SET title=%s, criterion=%s, target_date=%s,
                               status=%s, evidence=%s, notes=%s, updated_at=now()
                           WHERE id=%s""",
                        (m["title"], m.get("criterion"), m.get("target_date"),
                         m.get("status", "open"), m.get("evidence"), m.get("notes"), row[0]),
                    )
                    updated += 1
                else:
                    cur.execute(
                        """INSERT INTO milestones
                               (plan_id, step_number, title, criterion, target_date, status, evidence, notes)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                        (m["plan_id"], m["step_number"], m["title"], m.get("criterion"),
                         m.get("target_date"), m.get("status", "open"), m.get("evidence"), m.get("notes")),
                    )
                    created += 1
        conn.commit()
        logger.info("upsert_milestones committed: created=%s updated=%s", created, updated)
        return {"created": created, "updated": updated}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@mcp.tool()
def sync_hevy_completions(days: int = 30) -> dict:
    """
    Match completed Hevy workouts (trainings.type='WeightTraining', hevy_id
    IS NOT NULL) to planned CAIRN sessions in training_plan, over the last N
    days. Links via training_plan.hevy_id = trainings.hevy_id (2026-08-17 —
    replaces the earlier matched_training_id approach, which never produced
    matches because training_plan had no hevy_id column at all).

    Match rule, per Hevy workout:
        training_plan.session_date = workout.date
        AND session_type IN ('Strength Training', 'Krafttraining', 'Core', 'Mobility')
        AND status = 'planned'
    On match: training_plan.status = 'completed', training_plan.hevy_id = workout.hevy_id.

    Idempotent: a Hevy workout whose hevy_id is already linked to some
    training_plan row is excluded from consideration up front — repeated
    calls never re-match or double-link the same workout.

    A failure matching one workout does not abort the rest (isolated
    per-workout try/except) — this must never block Garmin sync, a separate tool.

    Returns: {matched: int, unmatched_hevy: [...], unmatched_cairn: [...]}
    - unmatched_hevy:  Hevy workouts in the window with no matching planned session.
    - unmatched_cairn: planned Strength/Core/Mobility sessions in the window
                        that are still unmatched after this run.
    """
    conn = get_connection()
    try:
        matched = 0
        unmatched_hevy: list[dict] = []

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, date, hevy_id, notes
                FROM trainings
                WHERE type = 'WeightTraining'
                  AND hevy_id IS NOT NULL
                  AND date >= CURRENT_DATE - (INTERVAL '1 day' * %s)
                  AND hevy_id NOT IN (
                      SELECT hevy_id FROM training_plan WHERE hevy_id IS NOT NULL
                  )
                ORDER BY date
                """,
                (days,),
            )
            hevy_workouts = cur.fetchall()

            for training_id, workout_date, hevy_id, notes in hevy_workouts:
                try:
                    cur.execute(
                        """
                        SELECT id, external_id FROM training_plan
                        WHERE (week_date + (day_of_week - 1) * INTERVAL '1 day')::date = %s
                          AND session_type IN ('Strength Training', 'Krafttraining', 'Core', 'Mobility')
                          AND status = 'planned'
                        ORDER BY id
                        LIMIT 1
                        """,
                        (workout_date,),
                    )
                    row = cur.fetchone()

                    if row:
                        tp_id, ext_id = row
                        cur.execute(
                            "UPDATE training_plan SET status='completed', hevy_id=%s WHERE id=%s",
                            (hevy_id, tp_id),
                        )
                        matched += 1
                        logger.info(
                            "sync_hevy_completions matched: training_plan_id=%s <- hevy_id=%s (training_id=%s)",
                            tp_id, hevy_id, training_id,
                        )
                    else:
                        unmatched_hevy.append({
                            "hevy_id": hevy_id, "training_id": training_id,
                            "date": str(workout_date), "title": notes,
                        })
                except Exception as exc:
                    unmatched_hevy.append({
                        "hevy_id": hevy_id, "training_id": training_id, "date": str(workout_date),
                        "title": notes, "error": f"{type(exc).__name__}: {exc}",
                    })
                    continue

            cur.execute(
                """
                SELECT id, external_id,
                       (week_date + (day_of_week - 1) * INTERVAL '1 day')::date AS session_date
                FROM training_plan
                WHERE session_type IN ('Strength Training', 'Krafttraining', 'Core', 'Mobility')
                  AND status = 'planned'
                  AND (week_date + (day_of_week - 1) * INTERVAL '1 day')::date
                      >= CURRENT_DATE - (INTERVAL '1 day' * %s)
                ORDER BY session_date
                """,
                (days,),
            )
            unmatched_cairn = [
                {"training_plan_id": r[0], "external_id": r[1], "date": str(r[2])}
                for r in cur.fetchall()
            ]

        conn.commit()
        logger.info(
            "sync_hevy_completions committed: matched=%s unmatched_hevy=%s unmatched_cairn=%s",
            matched, len(unmatched_hevy), len(unmatched_cairn),
        )
        return {"matched": matched, "unmatched_hevy": unmatched_hevy, "unmatched_cairn": unmatched_cairn}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@mcp.tool()
def reconcile_training_block(start_date: str, end_date: str) -> dict:
    """
    Compare CAIRN's training_plan sessions against the real Garmin calendar in
    a date range. Read-only — makes real Garmin API calls (via
    list_garmin_workouts) but writes nothing.

    Categorizes each CAIRN session as:
    - created:   garmin_workout_id set AND a matching Garmin entry exists on the same date
    - updated:   garmin_workout_id set, Garmin entry exists, but on a DIFFERENT date
                 (moved on the Garmin side without CAIRN's training_plan reflecting it)
    - unchanged: garmin_workout_id NOT set, no Garmin entry expected yet
    - conflict:  garmin_workout_id set but NO matching Garmin entry exists anymore
                 (deleted/changed externally), or an unmatched Garmin entry exists
                 on a day with a CAIRN session that has no garmin_workout_id
    - failed:    garmin_mcp_log shows status='failed' for this session's external_id
    """
    plan_sessions = _fetchall(
        """
        SELECT id, external_id,
               (week_date + (day_of_week - 1) * INTERVAL '1 day')::date AS session_date,
               session_type, garmin_workout_id
        FROM training_plan
        WHERE (week_date + (day_of_week - 1) * INTERVAL '1 day')::date BETWEEN %s AND %s
        ORDER BY session_date
        """,
        (start_date, end_date),
    )
    garmin_entries = list_garmin_workouts(start_date, end_date)
    garmin_by_id = {e["workout_id"]: e for e in garmin_entries if e.get("workout_id")}
    garmin_dates_matched: set[str] = set()

    failed_external_ids: set[str] = set()
    ext_ids = [s["external_id"] for s in plan_sessions if s.get("external_id")]
    if ext_ids:
        failed_rows = _fetchall(
            "SELECT external_id FROM garmin_mcp_log WHERE external_id = ANY(%s) AND status = 'failed'",
            (ext_ids,),
        )
        failed_external_ids = {r["external_id"] for r in failed_rows}

    results = []
    counts = {"created": 0, "updated": 0, "unchanged": 0, "conflict": 0, "failed": 0}
    for s in plan_sessions:
        status = "unchanged"
        detail = None
        if s.get("external_id") and s["external_id"] in failed_external_ids:
            status = "failed"
            detail = "garmin_mcp_log status=failed fuer diese external_id"
        elif s.get("garmin_workout_id"):
            gw_id_int = int(s["garmin_workout_id"]) if str(s["garmin_workout_id"]).isdigit() else None
            match = garmin_by_id.get(gw_id_int)
            if not match:
                status = "conflict"
                detail = f"garmin_workout_id={s['garmin_workout_id']} existiert nicht mehr auf Garmin"
            elif match["date"] != str(s["session_date"]):
                status = "updated"
                detail = f"Garmin-Termin liegt jetzt am {match['date']}, CAIRN-Plan hat {s['session_date']}"
                garmin_dates_matched.add(match["date"])
            else:
                status = "created"
                garmin_dates_matched.add(match["date"])
        counts[status] += 1
        results.append({
            "training_plan_id": s["id"], "external_id": s.get("external_id"),
            "date": str(s["session_date"]), "session_type": s["session_type"],
            "garmin_workout_id": s.get("garmin_workout_id"), "status": status, "detail": detail,
        })

    # Unzugeordnete Garmin-Eintraege: existieren auf Garmin, aber keine CAIRN-Session referenziert sie
    orphan_garmin_entries = [e for e in garmin_entries if e["date"] not in garmin_dates_matched]
    if orphan_garmin_entries:
        counts["conflict"] += len(orphan_garmin_entries)

    return {
        "period": {"start_date": start_date, "end_date": end_date},
        "counts": counts,
        "sessions": results,
        "orphan_garmin_entries": orphan_garmin_entries,
    }


@mcp.tool()
def push_sessions_to_garmin(session_ids: list[int], chunk_size: int = 7) -> dict:
    """
    Pusht eine Liste von CAIRN-Sessions gesammelt zu Garmin.
    Ein Login — mehrere Uploads — isolierte Fehlerbehandlung.

    Nutzen:
    - Nach Planänderungen: [session_id] der geänderten Session
    - Nach Wochenumstrukturierung: alle IDs der betroffenen Woche
    - Nachholpush: IDs aller fehlgeschlagenen Sessions

    Werden mehr session_ids übergeben als chunk_size, wird nur der erste
    Chunk gepusht (Timeout-/Rate-Limit-Schutz bei langen Listen). Die
    Antwort enthält pushed_ids, remaining_ids und total_remaining, damit
    ChatGPT sofort mit der nächsten Runde weitermachen kann.

    Gibt zurück: created / updated / moved / unchanged / failed pro Session.
    Strength, Core, Mobility werden vor dem ersten Garmin-Kontakt hart abgelehnt.
    """
    chunk = session_ids[:chunk_size]
    remaining = session_ids[chunk_size:]
    try:
        from coach.garmin_batch import run_batch
        result = run_batch(session_ids=chunk)
        if isinstance(result, dict):
            result["pushed_ids"] = chunk
            result["remaining_ids"] = remaining
            result["total_remaining"] = len(remaining)
        return result
    except Exception as exc:
        return {
            "error": str(exc),
            "type": type(exc).__name__,
            "pushed_ids": [],
            "remaining_ids": session_ids,
            "total_remaining": len(session_ids),
        }


@mcp.tool()
def sync_completed_activities(source: str = "both") -> dict:
    """
    Syncs completed workout sessions from Garmin and/or Hevy into the CAIRN database.

    Call this tool BEFORE get_recent_activities or get_activity_analysis_data whenever
    the user asks about a recently completed session — e.g. 'look at my run today',
    'how was my workout yesterday', 'analyse my last session', 'what did I do today'.

    Args:
        source: Which source to sync. Options: "garmin", "hevy", "both" (default: "both")

    Returns:
        Dict with imported/skipped counts per source, or error messages if a source fails.
        Example: {"garmin": {"imported": 1, "skipped": 0}, "hevy": {"imported": 0, "skipped": 2}}
    """
    from coach.jobs.sync_completed_activities import run_sync, _garmin_sync, _hevy_sync

    if source == "both":
        return run_sync()

    conn = get_connection()
    try:
        if source == "garmin":
            result = _garmin_sync(conn)
            conn.commit()
            return {"garmin": result}
        elif source == "hevy":
            result = _hevy_sync(conn)
            conn.commit()
            return {"hevy": result}
        else:
            return {"error": f"Invalid source '{source}'. Use: garmin, hevy, both"}
    except Exception as exc:
        logger.error("sync_completed_activities failed (source=%s): %s", source, exc)
        return {"error": str(exc)}
    finally:
        conn.close()


# ── ASGI app factory ───────────────────────────────────────────────────────────

def create_mcp_asgi_app() -> ASGIApp:
    """
    Return the MCPServer ASGI app — UNAUTHENTICATED.

    KNOWN, TEMPORARY SECURITY TRADE-OFF (2026-08-14, per Alexander):
    BearerAuthMiddleware is defined above but intentionally NOT applied here.
    ChatGPT's custom-connector setup rejected our Bearer-token auth ("Fehler
    beim Erstellen des Konnektors"), so auth was removed to unblock the
    ChatGPT connector. This means /mcp is reachable by ANYONE who has the
    URL, with no authentication at all — including create_garmin_workout /
    move_garmin_workout / delete_garmin_workout (real writes/deletes on
    Garmin Connect) and every read tool (full training + health history).
    "URL not publicly advertised" is NOT a real control — Railway subdomains
    are enumerable and the URL now lives in the ChatGPT connector config.
    Durable fix: implement proper OAuth (the mcp SDK already exposes
    AuthSettings/token_verifier/auth_server_provider for this — see
    MCPServer.__init__), which ChatGPT connectors do support, instead of
    leaving this endpoint open indefinitely.

    The inner app handles POST /mcp (Streamable HTTP transport).

    transport_security: streamable_http_app() auto-enables DNS-rebinding
    protection with allowed_hosts=["127.0.0.1:*", ...] whenever no `host=`
    is passed (its internal default is host="127.0.0.1"). That rejects any
    real deployment host with "Invalid Host header". We pass an explicit
    TransportSecuritySettings instead — protection stays ON, but the actual
    public host (env var, falls back to the known Railway host) is allowed
    too. Never disable enable_dns_rebinding_protection outright — that would
    remove the protection entirely rather than fixing the allow-list.

    Requires: mcp>=1.0. MCP_API_KEY is no longer read here (BearerAuthMiddleware
    is not applied) — see the security trade-off note above.
    """
    public_host = os.getenv("MCP_PUBLIC_HOST", "web-production-297f2.up.railway.app")
    transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[public_host, "127.0.0.1:*", "localhost:*", "[::1]:*"],
        allowed_origins=[
            f"https://{public_host}",
            "http://127.0.0.1:*",
            "http://localhost:*",
            "http://[::1]:*",
        ],
    )
    return mcp.streamable_http_app(transport_security=transport_security)
