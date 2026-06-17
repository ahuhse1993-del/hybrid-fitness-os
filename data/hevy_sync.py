import sys
sys.path.append('.')
from data.hevy_import import get_hevy_workouts
from database.connection import get_connection

def sync_hevy_to_db():
    print("🔄 Hevy Daten werden geladen...")
    workouts = get_hevy_workouts(limit=10)
    print(f"Debug: {len(workouts)} Workouts erhalten")
    
    if not workouts:
        print("❌ Keine Workouts gefunden")
        return
    
    conn = get_connection()
    cursor = conn.cursor()
    
    inserted = 0
    skipped = 0
    
    for workout in workouts:
        try:
            print(f"Debug Workout: {workout}")
            date = workout.get('start_time', '')[:10]
            name = workout.get('title', '')
            duration = round(workout.get('duration', 0) / 60)
            hevy_id = str(workout.get('id', ''))

            cursor.execute("SELECT id FROM trainings WHERE hevy_id = %s", (hevy_id,))
            if cursor.fetchone():
                skipped += 1
                continue

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
    print(f"⏭️ {skipped} bereits vorhanden")

if __name__ == "__main__":
    sync_hevy_to_db()