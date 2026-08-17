"""
coach/sync_utils.py
Content-Hash und Auswahl-Query fuer die Garmin-Batch-Push-Engine.
"""
import hashlib
import json

HASH_FIELDS = ["date", "session_type", "distance_km", "duration_min",
               "notes", "workout_steps", "session_zone", "name", "target",
               "elevation_gain_m"]


def compute_content_hash(session: dict) -> str:
    """
    SHA256 der Push-relevanten Felder. Aenderung = neuer Hash = dirty.
    Erfasst jetzt auch name/target (2026-08-16) — ein geaenderter Titel oder
    ein neu gesetztes/entferntes Pace-Target muss die Session als
    aktualisierungsbeduerftig markieren, genau wie Distanz/Dauer/Struktur.
    Akzeptiert sowohl "date" (z.B. aus upsert_planned_workout) als auch
    "session_date" (aus den SQL-Queries in garmin_batch.py/sync_utils.py) —
    dieselbe Session darf unter beiden Aufrufern denselben Hash ergeben.
    """
    payload = {k: str(session.get(k) or "") for k in HASH_FIELDS}
    payload["date"] = str(session.get("date") or session.get("session_date") or "")
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
                   sync_status, name, target, elevation_gain_m
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
