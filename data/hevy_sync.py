import sys
sys.path.append('.')
from data.hevy_import import get_hevy_workouts
from database.connection import get_connection

def sync_hevy_to_db():
    print("🔄 Hevy Daten werden geladen...")
    workouts = get_hevy_workouts(limit=10)
    
    if not workouts:
        print("❌ Keine Workouts gefunden")
        return
    
    conn = get_connection()
    cursor = conn.cursor()
    
    inserted = 0
    skipped = 0
    merged = 0
    
    for workout in workouts:
        try:
            date = workout.get('start_time', '')[:10]
            name = workout.get('title', '')
            duration = round(workout.get('duration', 0) / 60)
            hevy_id = str(workout.get('id', ''))

            # Prüfen ob bereits vorhanden
            cursor.execute("SELECT id FROM trainings WHERE hevy_id = %s", (hevy_id,))
            if cursor.fetchone():
                skipped += 1
                continue

            # Prüfen ob Strava WeightTraining am gleichen Tag existiert
            cursor.execute("""
                SELECT id, duration_minutes, heart_rate_avg 
                FROM trainings 
                WHERE date = %s AND type = 'WeightTraining' AND strava_id IS NOT NULL AND hevy_id IS NULL
            """, (date,))
            strava_entry = cursor.fetchone()

            if strava_entry:
                # Hevy Daten auf Strava Eintrag mergen
                cursor.execute("""
                    UPDATE trainings 
                    SET notes = %s,
                        hevy_id = %s,
                        duration_minutes = COALESCE(duration_minutes, %s)
                    WHERE id = %s
                """, (name, hevy_id, duration, strava_entry[0]))
                merged += 1
            else:
                # Neuer Hevy Eintrag
                cursor.execute("""
                    INSERT INTO trainings (date, type, duration_minutes, distance_km, notes, hevy_id)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (date, 'WeightTraining', duration, 0, name, hevy_id))
                inserted += 1

        except Exception as e:
            print(f"⚠️ Fehler: {e}")
            continue
    
    conn.commit()
    conn.close()
    
    print(f"✅ {inserted} neue Workouts gespeichert")
    print(f"🔗 {merged} mit Strava gemergt")
    print(f"⏭️ {skipped} bereits vorhanden")

if __name__ == "__main__":
    sync_hevy_to_db()