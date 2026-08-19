"""
CAIRN Completed-Activity Sync
==============================
Läuft als Railway Cron täglich um 05:00 UTC (0 5 * * *).
Kann auch manuell via MCP-Tool sync_completed_activities getriggert werden.

Garmin: letzte 26h Aktivitäten → trainings + splits + activity_stream
Hevy:   letzte 7 Tage Workouts → trainings + hevy_exercises

Beide Syncs laufen unabhängig — Garmin-Fehler blockt Hevy nicht und umgekehrt.
Berührt nur die trainings / hevy_exercises Tabellen, nie training_plan.
"""

import logging
import os
import requests
from datetime import datetime, timedelta, timezone

from database.connection import get_connection

logger = logging.getLogger(__name__)

GARMIN_LOOKBACK_HOURS = 26   # Puffer über 24h für den täglichen Cron
HEVY_LOOKBACK_DAYS = 7
HEVY_MAX_PAGES = 3

GARMIN_TYPE_MAP = {
    "running": "Run",
    "trail_running": "TrailRun",
    "cycling": "Ride",
    "road_biking": "Ride",
    "strength_training": "WeightTraining",
    "swimming": "Swim",
    "walking": "Walk",
    "hiking": "Hike",
    "yoga": "Yoga",
    "cardio": "Cardio",
}


# ---------------------------------------------------------------------------
# Garmin
# ---------------------------------------------------------------------------

def _find_existing_training_id(cur, garmin_id: str, start_time_str: str,
                                activity_type: str, duration_min) -> int | None:
    """Findet eine bereits importierte Aktivität: zuerst via garmin_id, dann via Datum+Typ+Dauer±10%."""
    cur.execute("SELECT id FROM trainings WHERE garmin_id = %s", (garmin_id,))
    row = cur.fetchone()
    if row:
        return row[0]

    try:
        start_dt = datetime.fromisoformat(start_time_str.replace("Z", ""))
    except Exception:
        return None

    dur_low = (duration_min or 0) * 0.9
    dur_high = (duration_min or 0) * 1.1
    cur.execute("""
        SELECT id FROM trainings
        WHERE type = %s
          AND date = %s
          AND duration_minutes BETWEEN %s AND %s
    """, (activity_type, start_dt.date().isoformat(), dur_low, dur_high))
    row = cur.fetchone()
    return row[0] if row else None


def _backfill_missing_details(cur, client, training_id: int, garmin_id: str, activity_type: str) -> list[str]:
    """
    Ergänzt fehlende Detaildaten (native Laps, Activity-Stream) für eine
    BEREITS vorhandene Aktivität — behebt das frühere Verhalten, das eine
    vorhandene Aktivität komplett übersprang, selbst wenn ihr Splits/Stream/
    GPS fehlten. Fehler pro Kategorie sind nicht fatal (isolierte try/except).
    """
    added: list[str] = []

    cur.execute("SELECT COUNT(*) FROM splits WHERE training_id = %s", (training_id,))
    if cur.fetchone()[0] == 0:
        try:
            from data.garmin_import_splits import import_splits_for_activity
            import_splits_for_activity(client, training_id, int(garmin_id))
            added.append("native_laps")
        except Exception as exc:
            logger.warning("Backfill native_laps Fehler für training_id=%s garmin_id=%s: %s",
                            training_id, garmin_id, exc)

    if activity_type in ("Run", "TrailRun", "Ride", "Walk", "Hike", "Swim"):
        cur.execute("SELECT COUNT(*) FROM activity_stream WHERE training_id = %s", (training_id,))
        if cur.fetchone()[0] == 0:
            try:
                from data.garmin_import_stream import import_stream_for_activity
                result = import_stream_for_activity(client, training_id, int(garmin_id), conn=None)
                if "imported" in result:
                    added.append("activity_stream")
            except Exception as exc:
                logger.warning("Backfill activity_stream Fehler für training_id=%s garmin_id=%s: %s",
                                training_id, garmin_id, exc)

    return added


def _link_training_plan(cur, garmin_id: str, garmin_activity_id: int,
                         distance_km: float | None) -> bool:
    """
    Matcht eine abgeschlossene Garmin-Aktivität mit einer geplanten Session in
    training_plan, sofern garmin_workout_id übereinstimmt (= Workout wurde direkt
    von der Uhr gestartet). Schreibt actual_distance_km + linked_garmin_activity_id.

    Wird nur ausgeführt wenn linked_garmin_activity_id noch nicht gesetzt ist
    (verhindert Überschreiben einer manuellen Backdoor-Verknüpfung).

    Returns True wenn ein Match gefunden und geschrieben wurde.
    """
    if not garmin_id or distance_km is None:
        return False

    # Suche training_plan-Eintrag via garmin_workout_id
    # garmin_workout_id ist die ID des auf Garmin gepushten Workout-Plans,
    # die beim Watch-Start automatisch in der Aktivität verlinkt wird.
    cur.execute(
        """SELECT id, linked_garmin_activity_id
           FROM training_plan
           WHERE garmin_workout_id = %s
           LIMIT 1""",
        (garmin_id,),
    )
    row = cur.fetchone()
    if not row:
        return False

    session_id, already_linked = row
    if already_linked is not None:
        # Bereits manuell gelinkt (Backdoor) — nicht überschreiben
        logger.debug(
            "training_plan id=%s bereits verknüpft mit activity=%s — Skip auto-link",
            session_id, already_linked,
        )
        return False

    cur.execute(
        """UPDATE training_plan
           SET actual_distance_km = %s,
               linked_garmin_activity_id = %s,
               updated_at = now()
           WHERE id = %s""",
        (distance_km, garmin_activity_id, session_id),
    )
    logger.info(
        "Auto-Link: training_plan id=%s ← garmin_activity_id=%s actual_km=%.2f",
        session_id, garmin_activity_id, distance_km,
    )
    return True


def _garmin_sync(conn) -> dict:
    """Holt neue Garmin-Aktivitäten der letzten GARMIN_LOOKBACK_HOURS Stunden."""
    from coach.garmin_push import garmin_client  # Token-Cache aus garmin_session_cache

    client = garmin_client()
    cur = conn.cursor()

    cutoff = datetime.now() - timedelta(hours=GARMIN_LOOKBACK_HOURS)
    activities = client.get_activities(0, 50)  # max 50, neueste zuerst

    imported = 0
    skipped = 0
    backfilled = 0

    for a in activities:
        start_str = a.get("startTimeLocal", "")
        if not start_str:
            continue

        try:
            act_dt = datetime.fromisoformat(start_str)
        except Exception:
            continue

        if act_dt < cutoff:
            break  # Aktivitäten sind chronologisch absteigend sortiert

        garmin_id = str(a.get("activityId", ""))
        raw_type = a.get("activityType", {}).get("typeKey", "")
        activity_type = GARMIN_TYPE_MAP.get(raw_type, "Other")
        distance_km = round(a.get("distance", 0) / 1000, 2) or None
        duration_min = round(a.get("duration", 0) / 60) or None
        avg_hr = a.get("averageHR")
        elevation = a.get("elevationGain")
        name = a.get("activityName", "")
        date_str = start_str[:10]

        existing_id = _find_existing_training_id(cur, garmin_id, start_str, activity_type, duration_min)
        if existing_id:
            skipped += 1
            added = _backfill_missing_details(cur, client, existing_id, garmin_id, activity_type)
            if added:
                conn.commit()
                backfilled += 1
                logger.info("Backfill für bestehende Aktivität ID %s: %s", existing_id, added)
            else:
                logger.debug("Skip (dupe, bereits vollständig): %s %s %s", date_str, activity_type, name)
            continue

        notes = name
        if elevation:
            notes += f" | +{round(elevation)}m"

        try:
            cur.execute("""
                INSERT INTO trainings
                    (date, type, duration_minutes, distance_km,
                     heart_rate_avg, notes, garmin_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                date_str,
                activity_type,
                duration_min,
                distance_km if distance_km and distance_km > 0 else None,
                int(avg_hr) if avg_hr else None,
                notes,
                garmin_id,
            ))
            training_id = cur.fetchone()[0]

            # Auto-Link: training_plan via garmin_workout_id matchen
            # Die Garmin-Aktivität enthält die parentId / workoutId des geplanten
            # Workouts wenn der Athlete ihn direkt von der Uhr gestartet hat.
            # Garmin API gibt workoutId im Activity-Dict zurekt.
            workout_id_raw = a.get("workoutId") or a.get("parentId")
            if workout_id_raw:
                linked = _link_training_plan(
                    cur,
                    str(workout_id_raw),
                    int(garmin_id),
                    distance_km if distance_km and distance_km > 0 else None,
                )
                if linked:
                    logger.info("Auto-Link OK: training_plan via workoutId=%s", workout_id_raw)

            conn.commit()
            imported += 1
            logger.info("Importiert: %s | %s | %s → ID %s", date_str, activity_type, name, training_id)

            # Splits importieren — Fehler sind nicht fatal
            try:
                from data.garmin_import_splits import import_splits_for_activity
                import_splits_for_activity(client, training_id, int(garmin_id))
            except Exception as exc:
                logger.warning("Splits Fehler für %s: %s", garmin_id, exc)

            # Activity Stream importieren — Fehler sind nicht fatal
            # Nur für Lauf/Rad-Aktivitäten sinnvoll (nicht Kraft, Yoga etc.)
            if activity_type in ("Run", "TrailRun", "Ride", "Walk", "Hike", "Swim"):
                try:
                    from data.garmin_import_stream import import_stream_for_activity
                    stream_result = import_stream_for_activity(
                        client, training_id, int(garmin_id), conn=None
                    )
                    logger.info("Stream für %s: %s", garmin_id, stream_result)
                except Exception as exc:
                    logger.warning("Stream Fehler für %s: %s", garmin_id, exc)

        except Exception as exc:
            logger.error("Insert Fehler für %s %s: %s", date_str, name, exc)
            conn.rollback()

    return {"imported": imported, "skipped": skipped, "backfilled": backfilled}


# ---------------------------------------------------------------------------
# Hevy
# ---------------------------------------------------------------------------

def _hevy_fetch_pages(api_key: str, max_pages: int) -> list:
    """Holt Hevy-Workouts seitenweise, stoppt nach max_pages oder leerer Seite."""
    all_workouts = []
    for page in range(1, max_pages + 1):
        resp = requests.get(
            "https://api.hevyapp.com/v1/workouts",
            headers={"api-key": api_key},
            params={"pageSize": 10, "page": page},
            timeout=15,
        )
        resp.raise_for_status()
        workouts = resp.json().get("workouts", [])
        all_workouts.extend(workouts)
        if len(workouts) < 10:
            break  # letzte Seite
    return all_workouts


def _hevy_sync(conn) -> dict:
    """Holt neue Hevy-Workouts der letzten HEVY_LOOKBACK_DAYS Tage."""
    api_key = os.environ["HEVY_API_KEY"]
    cur = conn.cursor()

    cutoff_date = (datetime.now() - timedelta(days=HEVY_LOOKBACK_DAYS)).date().isoformat()
    all_workouts = _hevy_fetch_pages(api_key, HEVY_MAX_PAGES)

    imported = 0
    skipped = 0

    for workout in all_workouts:
        start_str = workout.get("start_time", "")
        date_str = start_str[:10] if start_str else ""
        if not date_str or date_str < cutoff_date:
            continue

        hevy_id = str(workout.get("id", ""))
        name = workout.get("title", "")

        # Dauer berechnen
        duration = None
        if workout.get("duration"):
            duration = round(workout["duration"] / 60)
        else:
            try:
                t_start = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                t_end = datetime.fromisoformat(workout.get("end_time", "").replace("Z", "+00:00"))
                duration = round((t_end - t_start).total_seconds() / 60)
            except Exception:
                pass

        # Duplikat-Check
        cur.execute("SELECT id FROM trainings WHERE hevy_id = %s", (hevy_id,))
        if cur.fetchone():
            skipped += 1
            continue

        try:
            cur.execute("""
                INSERT INTO trainings (date, type, duration_minutes, distance_km, notes, hevy_id)
                VALUES (%s, 'WeightTraining', %s, 0, %s, %s)
                RETURNING id
            """, (date_str, duration, name, hevy_id))
            training_id = cur.fetchone()[0]

            # Übungen speichern
            exercises = workout.get("exercises", [])
            for ex in exercises:
                sets = ex.get("sets", [])
                reps_list = []
                weight_list = []
                for s in sets:
                    r = s.get("reps")
                    w = s.get("weight_kg")
                    dur_s = s.get("duration_seconds")
                    reps_list.append(str(r) if r is not None else (f"{dur_s}s" if dur_s is not None else ""))
                    if w is not None:
                        weight_list.append(str(w))

                cur.execute("""
                    INSERT INTO hevy_exercises
                        (training_id, exercise_index, exercise_name, sets,
                         reps_per_set, weight_kg_per_set, notes)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (
                    training_id,
                    ex.get("index", 0),
                    ex.get("title", ""),
                    len(sets),
                    ", ".join(r for r in reps_list if r) or None,
                    ", ".join(weight_list) or None,
                    ex.get("notes") or None,
                ))

            conn.commit()
            imported += 1
            logger.info("Hevy importiert: %s | %s → ID %s", date_str, name, training_id)

        except Exception as exc:
            logger.error("Hevy Insert Fehler %s %s: %s", date_str, name, exc)
            conn.rollback()

    return {"imported": imported, "skipped": skipped}


# ---------------------------------------------------------------------------
# Hauptfunktion
# ---------------------------------------------------------------------------

def run_sync() -> dict:
    """
    Synct Garmin + Hevy unabhängig voneinander.
    Fehler einer Quelle blockieren die andere nicht.
    """
    conn = get_connection()
    results = {}
    try:
        try:
            results["garmin"] = _garmin_sync(conn)
            logger.info("Garmin-Sync OK: %s", results["garmin"])
        except Exception as exc:
            logger.error("Garmin-Sync fehlgeschlagen: %s", exc)
            results["garmin"] = {"error": str(exc)}

        try:
            results["hevy"] = _hevy_sync(conn)
            logger.info("Hevy-Sync OK: %s", results["hevy"])
        except Exception as exc:
            logger.error("Hevy-Sync fehlgeschlagen: %s", exc)
            results["hevy"] = {"error": str(exc)}
    finally:
        conn.close()

    return results


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(message)s")
    print(run_sync())
