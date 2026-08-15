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
def get_planned_workouts(days: int = 14) -> list[dict]:
    """
    Return planned training sessions for the next N days (default 14).
    Includes session type, distance, target zone, structure, and Garmin workout ID if already pushed.
    """
    return _fetchall(
        """
        SELECT id,
               (week_date + (day_of_week - 1) * INTERVAL '1 day')::date AS session_date,
               session_type, session_zone, distance_km, duration_min,
               notes, phase, garmin_workout_id, workout_steps, plan_week
        FROM training_plan
        WHERE (week_date + (day_of_week - 1) * INTERVAL '1 day')::date
              BETWEEN CURRENT_DATE AND CURRENT_DATE + (INTERVAL '1 day' * %s)
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


@mcp.tool()
def get_activity_analysis_data(
    activity_id: int | None = None,
    garmin_id: str | None = None,
    stream_resolution_s: int = 30,
) -> dict:
    """
    Vollständige Analyse-Daten für einen abgeschlossenen Lauf.
    Zuerst get_recent_activities() aufrufen um die activity_id zu erhalten.

    Gibt zurück:
    - summary:          23+ Felder Gesamtübersicht
    - splits:           km-weise Aufschlüsselung mit Pace, HF, Elevation
    - hr_zones:         Zeitverteilung in HR-Zonen (Minuten + Prozent)
    - trail_segments:   Aggregierte Aufstiegs-/Abstiegs-/Flachphasen
    - stream:           Downgesampelter HR-Stream (aus hr_tracks, ~30s Auflösung)
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
        "SELECT split_number, distance_km, pace_seconds, heart_rate_avg, elevation_gain "
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
            "hr_avg": s["heart_rate_avg"],
            "elevation_m": round(elev, 1),
        })

    # ── Trail-Segmente (aus Splits berechnet) ──
    UPHILL_T, DOWNHILL_T = 30, -30
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

    # ── HR-Zonen (aus athlete_profile + hr_tracks) ──
    zones_out = []
    profile = _fetchone(
        "SELECT hr_z1_max, hr_z2_max, hr_z3_max, hr_z4_max, hr_z5_max "
        "FROM athlete_profile ORDER BY id DESC LIMIT 1"
    )
    if profile and any(profile.values()):
        hr_points = _fetchall(
            "SELECT heart_rate FROM hr_tracks WHERE training_id = %s AND heart_rate IS NOT NULL",
            (tid,)
        )
        if hr_points:
            zone_bounds = [
                (1, "Regeneration", 0, profile["hr_z1_max"] or 130),
                (2, "Basis",        (profile["hr_z1_max"] or 130) + 1, profile["hr_z2_max"] or 148),
                (3, "Tempo",        (profile["hr_z2_max"] or 148) + 1, profile["hr_z3_max"] or 162),
                (4, "Schwelle",     (profile["hr_z3_max"] or 162) + 1, profile["hr_z4_max"] or 174),
                (5, "VO2max",       (profile["hr_z4_max"] or 174) + 1, 999),
            ]
            total_pts = len(hr_points)
            for z_num, z_label, z_min, z_max in zone_bounds:
                count = sum(1 for p in hr_points if z_min <= (p["heart_rate"] or 0) <= z_max)
                time_min = round(count / 60, 1)
                zones_out.append({
                    "zone": z_num,
                    "label": z_label,
                    "min_hr": z_min,
                    "max_hr": z_max if z_max < 999 else None,
                    "time_min": time_min,
                    "pct": round(count / total_pts * 100) if total_pts > 0 else 0,
                })

    # ── Stream (downgesampelt aus hr_tracks) ──
    stream_data = _fetchall(
        f"SELECT point_index, timestamp_ms, heart_rate FROM hr_tracks "
        f"WHERE training_id = %s AND MOD(point_index, %s) = 0 "
        f"ORDER BY point_index",
        (tid, stream_resolution_s)
    )

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
        "elevation_gain_m": training["elevation_gain_m"] or round(total_up, 1),
        "elevation_loss_m": training["elevation_loss_m"] or round(total_down, 1),
        "avg_cadence": training["avg_cadence"],
        "training_load": training["training_load"],
        "aerobic_effect": training["aerobic_effect"],
        "anaerobic_effect": training["anaerobic_effect"],
        "vo2max_estimate": training["vo2max_estimate"],
        "avg_power": training["avg_power"],
        "splits_count": len(splits_out),
        "trail_segments_count": len(trail_segments),
        "hr_zones_available": len(zones_out) > 0,
        "stream_points": len(stream_data),
    }

    return {
        "summary": summary,
        "splits": splits_out,
        "trail_segments": trail_segments,
        "hr_zones": zones_out if zones_out else None,
        "stream": {
            "resolution_s": stream_resolution_s,
            "fields": ["point_index", "timestamp_ms", "heart_rate"],
            "data": [[p["point_index"], p["timestamp_ms"], p["heart_rate"]] for p in stream_data],
        } if stream_data else None,
    }


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
