"""
CAIRN Garmin Daily Sync
Holt nur neue Aktivitäten der letzten 5 Tage.
Matching via Startzeit + Sporttyp + Dauer (nicht Garmin-ID).
Wird via Strava Webhook getriggert.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from garminconnect import Garmin
from database.connection import get_connection
from datetime import date, timedelta, datetime
import time
from dotenv import load_dotenv

load_dotenv()

def map_activity_type(garmin_type: str) -> str:
    mapping = {
        "running": "Run",
        "trail_running": "TrailRun",
        "cycling": "Ride",
        "strength_training": "WeightTraining",
        "swimming": "Swim",
        "walking": "Walk",
        "hiking": "Hike",
        "yoga": "Yoga",
    }
    return mapping.get(garmin_type, "Other")

def is_duplicate(cur, start_time, activity_type, duration_min, distance_km):
    """
    Matching via Startzeit ±5 Min + Sporttyp + Dauer ±10%
    """
    try:
        start_dt = datetime.fromisoformat(start_time.replace("Z", ""))
    except Exception:
        return False

    window_start = start_dt - timedelta(minutes=5)
    window_end = start_dt + timedelta(minutes=5)
    dur_min_low = (duration_min or 0) * 0.9
    dur_min_high = (duration_min or 0) * 1.1

    cur.execute("""
        SELECT id FROM trainings
        WHERE type = %s
          AND date = %s
          AND duration_minutes BETWEEN %s AND %s
    """, (
        activity_type,
        start_dt.date().isoformat(),
        dur_min_low,
        dur_min_high
    ))
    return cur.fetchone() is not None

def sync_daily():
    client = Garmin(os.getenv("GARMIN_EMAIL"), os.getenv("GARMIN_PASSWORD"))
    client.login()

    conn = get_connection()
    cur = conn.cursor()

    # Sync Job starten
    cur.execute("""
        INSERT INTO sync_jobs (type, status, started_at, triggered_by)
        VALUES ('activity', 'running', NOW(), 'webhook')
        RETURNING id
    """)
    job_id = cur.fetchone()[0]
    conn.commit()

    imported = 0
    skipped = 0

    try:
        print("=== CAIRN Daily Sync — letzte 5 Tage ===\n")

        # Nur letzte 5 Tage holen — max 50 Aktivitäten
        activities = client.get_activities(0, 50)

        cutoff = date.today() - timedelta(days=5)

        for a in activities:
            date_str = a.get("startTimeLocal", "")[:10]
            if not date_str or date_str < str(cutoff):
                continue

            garmin_id = str(a.get("activityId", ""))
            activity_type = map_activity_type(
                a.get("activityType", {}).get("typeKey", "")
            )
            distance_km = round(a.get("distance", 0) / 1000, 2) or None
            duration_min = round(a.get("duration", 0) / 60) or None
            avg_hr = a.get("averageHR")
            elevation = a.get("elevationGain")
            name = a.get("activityName", "")
            start_time = a.get("startTimeLocal", "")

            # Duplikat-Check via Startzeit + Typ + Dauer
            if is_duplicate(cur, start_time, activity_type, duration_min, distance_km):
                skipped += 1
                print(f"  ⏭️  Bereits vorhanden: {date_str} | {activity_type} | {name}")
                continue

            # Auch via garmin_id prüfen als Fallback
            cur.execute("SELECT id FROM trainings WHERE garmin_id = %s", (garmin_id,))
            if cur.fetchone():
                skipped += 1
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
                imported += 1
                print(f"  ✅ {date_str} | {activity_type} | {name} → ID {training_id}")

                conn.commit()

                # Splits importieren
                try:
                    from data.garmin_import_splits import import_splits_for_activity
                    import_splits_for_activity(client, training_id, int(garmin_id))
                    print(f"     Splits importiert")
                except Exception as e:
                    print(f"     ⚠️ Splits Fehler: {e}")

                # Granulare Herzfrequenz-Zeitreihe importieren
                try:
                    from data.garmin_import_hr import import_hr_for_activity
                    import_hr_for_activity(client, training_id, int(garmin_id))
                    print(f"     HR-Zeitreihe importiert")
                except Exception as e:
                    print(f"     ⚠️ HR Fehler: {e}")

            except Exception as e:
                print(f"  ❌ Fehler bei {name}: {e}")
                conn.rollback()
                continue

        # Job abschliessen
        cur.execute("""
            UPDATE sync_jobs
            SET status='success', finished_at=NOW()
            WHERE id=%s
        """, (job_id,))
        conn.commit()

    except Exception as e:
        cur.execute("""
            UPDATE sync_jobs
            SET status='failed', finished_at=NOW(), last_error=%s
            WHERE id=%s
        """, (str(e), job_id))
        conn.commit()
        raise

    finally:
        conn.close()

    print(f"\n✅ Daily Sync fertig — Importiert: {imported}, Übersprungen: {skipped}")

if __name__ == "__main__":
    sync_daily()