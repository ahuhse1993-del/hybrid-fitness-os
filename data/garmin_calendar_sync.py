"""
CAIRN Garmin Calendar Sync
Holt geplante Workouts aus Garmin Kalender → training_plan Tabelle
Europäische Woche: Montag = Tag 1 ... Sonntag = Tag 7
week_date = Montag der Woche
Holt auch Workout-Details (Distanz, Dauer, Struktur) via workoutId
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from garminconnect import Garmin
from database.connection import get_connection
from datetime import date, timedelta
import time

def map_title_to_session_type(title, sport_key):
    title_lower = title.lower()
    if 'interval' in title_lower or 'intervall' in title_lower:
        return 'Intervalle'
    if 'hill' in title_lower or 'hügel' in title_lower or 'bergauf' in title_lower:
        return 'Hill Repeats'
    if 'tempo' in title_lower:
        return 'Tempo Run'
    if 'threshold' in title_lower or 'schwelle' in title_lower:
        return 'Threshold Run'
    if 'progression' in title_lower:
        return 'Progression Run'
    if 'long' in title_lower or 'langer' in title_lower:
        return 'Long Run'
    if 'recovery' in title_lower or 'regeneration' in title_lower:
        return 'Recovery Run'
    if 'race' in title_lower or 'wettkampf' in title_lower or 'rennen' in title_lower:
        return 'Race Pace Run'
    if 'trail' in title_lower:
        return 'Trail Run'
    if 'fartlek' in title_lower:
        return 'Tempo Run'
    if 'strength' in title_lower or 'kraft' in title_lower:
        return 'Krafttraining'
    if 'shake out' in title_lower or 'shakeout' in title_lower:
        return 'Recovery Run'
    mapping = {
        'running': 'Easy Run',
        'trail_running': 'Trail Run',
        'cycling': 'Ride',
        'strength_training': 'Krafttraining',
        'swimming': 'Swim',
        'walking': 'Walk',
        'hiking': 'Hike',
    }
    return mapping.get(sport_key, 'Easy Run')

def get_workout_details(client, workout_id):
    """Holt Distanz, Dauer und Struktur eines Workouts"""
    try:
        w = client.get_workout_by_id(workout_id)
        dist_m = w.get('estimatedDistanceInMeters') or 0
        dur_s = w.get('estimatedDurationInSecs') or 0
        dist_km = round(dist_m / 1000, 1) if dist_m else 0
        dur_min = round(dur_s / 60) if dur_s else 0

        # Workout-Struktur aus Steps extrahieren
        steps_text = []
        segments = w.get('workoutSegments', [])
        for seg in segments:
            steps = seg.get('workoutSteps', [])
            for step in steps:
                step_type = step.get('stepType', {}).get('stepTypeKey', '')
                desc = step.get('description', '') or ''
                end_cond = step.get('endCondition', {}).get('conditionTypeKey', '')
                end_val = step.get('endConditionValue')

                if end_cond == 'lap.button':
                    continue

                if end_val:
                    if end_cond == 'distance':
                        val_str = f"{round(end_val/1000, 1)}km"
                    elif end_cond == 'time':
                        mins = round(end_val / 60)
                        val_str = f"{mins} min"
                    else:
                        val_str = str(round(end_val))
                else:
                    val_str = ''

                if step_type == 'warmup':
                    steps_text.append(f"Warm-Up: {val_str} {desc}".strip())
                elif step_type == 'cooldown':
                    steps_text.append(f"Cool Down: {val_str} {desc}".strip())
                elif step_type == 'interval':
                    steps_text.append(f"{val_str} {desc}".strip())
                elif step_type == 'rest':
                    steps_text.append(f"Pause: {val_str}".strip())

        structure = ' · '.join([s for s in steps_text if s]) if steps_text else ''
        return dist_km, dur_min, structure
    except Exception as e:
        print(f"    ⚠️ Workout-Details Fehler: {e}")
        return 0, 0, ''

def sync_garmin_calendar():
    client = Garmin(os.getenv("GARMIN_EMAIL"), os.getenv("GARMIN_PASSWORD"))
    client.login()

    conn = get_connection()
    cur = conn.cursor()

    today = date.today()

    months = set()
    for i in range(5):
        d = today + timedelta(weeks=i)
        months.add((d.year, d.month))

    planned = []
    seen_ids = set()
    for year, month in sorted(months):
        data = client.get_scheduled_workouts(year, month)
        items = data.get('calendarItems', [])
        for item in items:
            if item.get('itemType') == 'workout':
                workout_id = item.get('workoutId') or item.get('id')
                if workout_id not in seen_ids:
                    seen_ids.add(workout_id)
                    planned.append(item)

    print(f"Gefundene geplante Workouts: {len(planned)}")

    monday = today - timedelta(days=today.weekday())
    cur.execute("DELETE FROM training_plan WHERE week_date >= %s", (monday,))

    for item in planned:
        item_date = date.fromisoformat(item['date'])
        if item_date < today:
            continue

        item_monday = item_date - timedelta(days=item_date.weekday())
        day_of_week = item_date.isoweekday()

        title = item.get('title', '')
        session_type = map_title_to_session_type(title, item.get('sportTypeKey', 'running'))

        # Workout-Details holen
        workout_id = item.get('workoutId')
        dist_km, dur_min, structure = 0, 0, ''
        if workout_id:
            dist_km, dur_min, structure = get_workout_details(client, workout_id)
            time.sleep(0.5)

        # Notes: Struktur wenn vorhanden, sonst Titel
        notes = structure if structure else title

        cur.execute("""
            INSERT INTO training_plan (week_date, day_of_week, session_type, session_zone, duration_min, distance_km, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            str(item_monday),
            day_of_week,
            session_type,
            '',
            dur_min,
            dist_km,
            notes
        ))
        print(f"  ✅ {item_date} | Tag {day_of_week} | {session_type} | {dist_km}km | {dur_min}min | {notes[:40]}")

    conn.commit()
    conn.close()
    print("✅ Garmin Kalender Sync abgeschlossen")

if __name__ == "__main__":
    sync_garmin_calendar()