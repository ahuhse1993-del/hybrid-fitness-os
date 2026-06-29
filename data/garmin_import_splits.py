"""
CAIRN Garmin Splits Import
Holt Splits für alle Garmin-Aktivitäten und speichert sie in die DB.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from garminconnect import Garmin
from database.connection import get_connection
import time
from dotenv import load_dotenv

load_dotenv()

def get_garmin_client():
    client = Garmin(
        os.getenv("GARMIN_EMAIL"),
        os.getenv("GARMIN_PASSWORD")
    )
    client.login()
    return client

def _insert_splits(cur, training_id, splits):
    """Hilfsfunktion: Splits für eine Aktivität in DB schreiben."""
    # Sequence sicherstellen
    cur.execute("SELECT setval('splits_id_seq', (SELECT MAX(id) FROM splits))")

    for i, split in enumerate(splits):
        distance_km = round(split.get("distance", 0) / 1000, 3)
        duration_s = split.get("duration", 0)
        avg_hr = split.get("averageHR")
        elevation = split.get("elevationGain")

        if distance_km and distance_km > 0 and duration_s:
            pace_s = round(duration_s / distance_km)
        else:
            pace_s = None

        cur.execute("""
            INSERT INTO splits
                (training_id, split_number, distance_km,
                 pace_seconds, heart_rate_avg, elevation_gain)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (
            training_id,
            i + 1,
            distance_km if distance_km > 0 else None,
            pace_s,
            int(avg_hr) if avg_hr else None,
            round(elevation, 1) if elevation else None,
        ))
    return len(splits)

def import_splits_for_activity(client, training_id, garmin_id):
    """
    Schnelle Funktion: Splits für eine einzelne Aktivität importieren.
    Wird vom Daily Sync nach jedem neuen Import aufgerufen.
    """
    conn = get_connection()
    cur = conn.cursor()

    try:
        # Prüfen ob Splits bereits vorhanden
        cur.execute("SELECT COUNT(*) FROM splits WHERE training_id = %s", (training_id,))
        if cur.fetchone()[0] > 0:
            print(f"  Splits bereits vorhanden für ID {training_id}")
            conn.close()
            return

        splits_data = client.get_activity_splits(garmin_id)
        splits = splits_data.get("lapDTOs", [])

        if not splits:
            print(f"  Keine Splits verfügbar für Garmin ID {garmin_id}")
            conn.close()
            return

        count = _insert_splits(cur, training_id, splits)
        conn.commit()
        print(f"  ✅ {count} Splits importiert für Training ID {training_id}")

    except Exception as e:
        conn.rollback()
        print(f"  ❌ Splits Fehler: {e}")
        raise
    finally:
        conn.close()

def import_splits():
    """
    Vollständiger Splits Import für alle Aktivitäten ohne Splits.
    Für Backfill oder wöchentlichen Sync.
    """
    client = get_garmin_client()
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT t.id, t.garmin_id, t.type, t.date
        FROM trainings t
        LEFT JOIN splits s ON s.training_id = t.id
        WHERE t.garmin_id IS NOT NULL AND s.id IS NULL
        ORDER BY t.date DESC
    """)
    activities = cur.fetchall()
    conn.close()

    print(f"=== CAIRN Garmin Splits Import ===")
    print(f"Aktivitäten ohne Splits: {len(activities)}\n")

    total_imported = 0
    total_skipped = 0

    client2 = get_garmin_client()

    for idx, (training_id, garmin_id, act_type, act_date) in enumerate(activities):
        try:
            import_splits_for_activity(client2, training_id, int(garmin_id))
            total_imported += 1
            print(f"  [{idx+1}/{len(activities)}] {act_date} | {act_type} ✅")
        except Exception as e:
            print(f"  [{idx+1}/{len(activities)}] {act_date} | Fehler: {e}")
            total_skipped += 1

        time.sleep(2)

    print(f"\n=== FERTIG ===")
    print(f"Importiert: {total_imported}")
    print(f"Fehler: {total_skipped}")

if __name__ == "__main__":
    import_splits()