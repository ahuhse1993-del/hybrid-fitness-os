"""
CAIRN Garmin Splits Import
Holt Splits für alle Garmin-Aktivitäten und speichert sie in die DB.
"""

from garminconnect import Garmin
from database.connection import get_connection
import os, time
from dotenv import load_dotenv

load_dotenv()

def get_garmin_client():
    client = Garmin(
        os.getenv("GARMIN_EMAIL"),
        os.getenv("GARMIN_PASSWORD")
    )
    client.login()
    return client

def import_splits():
    client = get_garmin_client()
    conn = get_connection()
    cur = conn.cursor()

    # Alle Aktivitäten mit garmin_id holen
    cur.execute("""
        SELECT id, garmin_id, type, date 
        FROM trainings 
        WHERE garmin_id IS NOT NULL
        ORDER BY date DESC
    """)
    activities = cur.fetchall()

    print(f"=== CAIRN Garmin Splits Import ===")
    print(f"Aktivitäten total: {len(activities)}\n")

    total_imported = 0
    total_skipped = 0

    for idx, (training_id, garmin_id, act_type, act_date) in enumerate(activities):
        
        # Bereits Splits vorhanden?
        cur.execute("SELECT COUNT(*) FROM splits WHERE training_id = %s", (training_id,))
        if cur.fetchone()[0] > 0:
            total_skipped += 1
            continue

        try:
            splits_data = client.get_activity_splits(int(garmin_id))
            splits = splits_data.get("lapDTOs", [])

            if not splits:
                print(f"  [{idx+1}/{len(activities)}] {act_date} | {act_type} | keine Splits")
                time.sleep(1)
                continue

            for i, split in enumerate(splits):
                distance_km = round(split.get("distance", 0) / 1000, 3)
                duration_s = split.get("duration", 0)
                avg_hr = split.get("averageHR")
                elevation = split.get("elevationGain")

                # Pace berechnen (Sekunden pro km)
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
                total_imported += 1

            conn.commit()
            print(f"  [{idx+1}/{len(activities)}] {act_date} | {act_type} | {len(splits)} Splits ✅")

        except Exception as e:
            print(f"  [{idx+1}/{len(activities)}] {act_date} | {act_type} | Fehler: {e}")
            conn.rollback()

        time.sleep(2)

    conn.close()
    print(f"\n=== FERTIG ===")
    print(f"Splits importiert: {total_imported}")
    print(f"Aktivitäten übersprungen (bereits vorhanden): {total_skipped}")

if __name__ == "__main__":
    import_splits()
