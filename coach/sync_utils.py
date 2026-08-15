"""
coach/sync_utils.py
Content-Hash und Auswahl-Query fuer die Garmin-Batch-Push-Engine.
"""
import hashlib
import json

HASH_FIELDS = ["date", "session_type", "distance_km", "duration_min",
               "notes", "workout_steps", "session_zone"]


def compute_content_hash(session: dict) -> str:
    """SHA256 der Push-relevanten Felder. Änderung = neuer Hash = dirty."""
    payload = {k: str(session.get(k) or "") for k in HASH_FIELDS}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def sessions_to_push(conn) -> list[dict]:
    """
    Alle Sessions die zu Garmin gepusht werden müssen:
    sync_target='garmin', status='planned',
    date BETWEEN heute (Europe/Zurich) AND heute + 14 Tage,
    UND (garmin_workout_id IS NULL OR sync_status IN ('dirty','failed'))
    """
    from zoneinfo import ZoneInfo
    from datetime import datetime, timedelta
    tz = ZoneInfo("Europe/Zurich")
    today = datetime.now(tz).date()
    until = today + timedelta(days=14)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id,
                   (week_date + (day_of_week - 1) * INTERVAL '1 day')::date AS session_date,
                   session_type, session_zone, distance_km, duration_min,
                   notes, workout_steps, plan_week, phase, sport,
                   garmin_workout_id, external_id, content_hash,
                   sync_status
            FROM training_plan
            WHERE sync_target = 'garmin'
              AND status = 'planned'
              AND (week_date + (day_of_week - 1) * INTERVAL '1 day')::date
                  BETWEEN %s AND %s
              AND (
                  garmin_workout_id IS NULL
                  OR sync_status IN ('dirty', 'failed')
              )
            ORDER BY session_date
        """, (today, until))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
