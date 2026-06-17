import sys
sys.path.append('.')
from data.strava_import import get_strava_activities
from database.connection import get_connection
from datetime import datetime

def sync_strava_to_db():
    print("🔄 Strava Daten werden geladen...")
    activities = get_strava_activities(limit=30)
    
    if not activities or not isinstance(activities[0], dict):
        print("❌ Keine Aktivitäten gefunden")
        return
    
    conn = get_connection()
    cursor = conn.cursor()
    
    inserted = 0
    skipped = 0
    
    for activity in activities:
        try:
            # Datum formatieren
            date = activity.get('start_date_local', '')[:10]
            name = activity.get('name', '')
            sport_type = activity.get('sport_type', '')
            duration = round(activity.get('moving_time', 0) / 60)
            distance = round(activity.get('distance', 0) / 1000, 2)
            heart_rate = activity.get('average_heartrate')
            strava_id = str(activity.get('id', ''))

            # Prüfen ob bereits vorhanden
            cursor.execute("SELECT id FROM trainings WHERE strava_id = %s", (strava_id,))
            if cursor.fetchone():
                skipped += 1
                continue

            cursor.execute("""
                INSERT INTO trainings (date, type, duration_minutes, distance_km, heart_rate_avg, notes, strava_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (date, sport_type, duration, distance, heart_rate, name, strava_id))
            
            inserted += 1

        except Exception as e:
            print(f"⚠️ Fehler bei Aktivität: {e}")
            continue
    
    conn.commit()
    conn.close()
    
    print(f"✅ {inserted} neue Aktivitäten gespeichert")
    print(f"⏭️ {skipped} bereits vorhanden")

if __name__ == "__main__":
    sync_strava_to_db()