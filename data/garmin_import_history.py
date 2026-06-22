"""
CAIRN Garmin History Import
Holt alle Aktivitäten von Garmin und speichert sie in die DB.
Duplikate werden via garmin_id verhindert.
"""

from garminconnect import Garmin
from database.connection import get_connection
from datetime import date
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

def import_all_activities():
    client = get_garmin_client()
    conn = get_connection()
    cur = conn.cursor()

    total_imported = 0
    total_skipped = 0
    start = 0
    batch_size = 100

    print("=== CAIRN Garmin History Import ===\n")

    while True:
        print(f"Hole Aktivitäten {start}–{start + batch_size}...")
        
        try:
            activities = client.get_activities(start, batch_size)
        except Exception as e:
            print(f"❌ Fehler beim Holen: {e}")
            break

        if not activities:
            print("Keine weiteren Aktivitäten.")
            break

        for a in activities:
            garmin_id = str(a.get("activityId", ""))
            
            # Duplikat prüfen
            cur.execute("SELECT id FROM trainings WHERE garmin_id = %s", (garmin_id,))
            if cur.fetchone():
                total_skipped += 1
                continue

            # Daten aufbereiten
            date_str = a.get("startTimeLocal", "")[:10]
            activity_type = map_activity_type(
                a.get("activityType", {}).get("typeKey", "")
            )
            distance_km = round(a.get("distance", 0) / 1000, 2) or None
            duration_min = round(a.get("duration", 0) / 60) or None
            avg_hr = a.get("averageHR")
            elevation = a.get("elevationGain")
            name = a.get("activityName", "")

            notes = name
            if elevation:
                notes += f" | +{round(elevation)}m"

            try:
                cur.execute("""
                    INSERT INTO trainings 
                        (date, type, duration_minutes, distance_km, 
                         heart_rate_avg, notes, garmin_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (
                    date_str,
                    activity_type,
                    duration_min,
                    distance_km if distance_km and distance_km > 0 else None,
                    int(avg_hr) if avg_hr else None,
                    notes,
                    garmin_id,
                ))
                total_imported += 1
                print(f"  ✅ {date_str} | {activity_type} | {name}")
            except Exception as e:
                print(f"  ❌ Fehler bei {name}: {e}")
                conn.rollback()
                continue

        conn.commit()
        
        # Wenn weniger als batch_size zurück — fertig
        if len(activities) < batch_size:
            break
            
        start += batch_size
        time.sleep(1)  # Garmin Rate Limit respektieren

    conn.close()
    print(f"\n=== FERTIG ===")
    print(f"Importiert: {total_imported}")
    print(f"Übersprungen (Duplikate): {total_skipped}")

if __name__ == "__main__":
    import_all_activities()
