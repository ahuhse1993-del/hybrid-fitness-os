import sys
sys.path.append('.')
from data.hevy_import import get_hevy_workouts
from database.connection import get_connection

def sync_hevy_to_db():
    print("🔄 Hevy Daten werden geladen...")
    workouts = get_hevy_workouts()

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
            duration = round(workout.get('duration', 0) / 60) if workout.get('duration') else None
            if not duration:
                try:
                    from datetime import datetime
                    start = datetime.fromisoformat(workout.get('start_time','').replace('Z','+00:00'))
                    end = datetime.fromisoformat(workout.get('end_time','').replace('Z','+00:00'))
                    duration = round((end - start).total_seconds() / 60)
                except Exception:
                    duration = None
            hevy_id = str(workout.get('id', ''))
            exercises = workout.get('exercises', [])

            # Bereits vorhanden?
            cursor.execute("SELECT id FROM trainings WHERE hevy_id = %s", (hevy_id,))
            existing = cursor.fetchone()
            if existing:
                training_id = existing[0]
                skipped += 1
            else:
                # Strava WeightTraining am gleichen Tag?
                cursor.execute("""
                    SELECT id FROM trainings
                    WHERE date = %s AND type = 'WeightTraining'
                    AND strava_id IS NOT NULL AND hevy_id IS NULL
                """, (date,))
                strava_entry = cursor.fetchone()

                if strava_entry:
                    training_id = strava_entry[0]
                    cursor.execute("""
                        UPDATE trainings
                        SET notes = %s, hevy_id = %s,
                            duration_minutes = COALESCE(duration_minutes, %s)
                        WHERE id = %s
                    """, (name, hevy_id, duration, training_id))
                    merged += 1
                else:
                    cursor.execute("""
                        INSERT INTO trainings (date, type, duration_minutes, distance_km, notes, hevy_id)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        RETURNING id
                    """, (date, 'WeightTraining', duration, 0, name, hevy_id))
                    training_id = cursor.fetchone()[0]
                    inserted += 1

            # Übungen speichern (löschen + neu schreiben)
            cursor.execute("DELETE FROM hevy_exercises WHERE training_id = %s", (training_id,))
            for ex in exercises:
                ex_name = ex.get('title', '')
                ex_index = ex.get('index', 0)
                sets = ex.get('sets', [])
                num_sets = len(sets)

                # Reps und Gewicht pro Satz
                reps_list = []
                weight_list = []
                for s in sets:
                    r = s.get('reps')
                    w = s.get('weight_kg')
                    dur = s.get('duration_seconds')
                    if r is not None:
                        reps_list.append(str(r))
                    elif dur is not None:
                        reps_list.append(f"{dur}s")
                    if w is not None:
                        weight_list.append(str(w))

                reps_str = ', '.join(reps_list) if reps_list else None
                weight_str = ', '.join(weight_list) if weight_list else None

                cursor.execute("""
                    INSERT INTO hevy_exercises
                        (training_id, exercise_index, exercise_name, sets, reps_per_set, weight_kg_per_set, notes)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (training_id, ex_index, ex_name, num_sets, reps_str, weight_str, ex.get('notes') or None))

        except Exception as e:
            print(f"⚠️ Fehler bei {name}: {e}")
            continue

    conn.commit()
    conn.close()

    print(f"✅ {inserted} neue Workouts gespeichert")
    print(f"🔗 {merged} mit Strava gemergt")
    print(f"⏭️ {skipped} bereits vorhanden")

if __name__ == "__main__":
    sync_hevy_to_db()