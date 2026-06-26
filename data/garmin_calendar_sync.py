"""
CAIRN Garmin Calendar Sync
Holt geplante Workouts aus Garmin Kalender → training_plan Tabelle
Europäische Woche: Montag = Tag 1 ... Sonntag = Tag 7
week_date = Montag der Woche
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from garminconnect import Garmin
from database.connection import get_connection
from datetime import date, timedelta

def map_title_to_session_type(title, sport_key):
    title_lower = title.lower()
    # Titel-basiertes Mapping hat Vorrang
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
    # Sport-Key als Fallback
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
        day_of_week = item_date.isoweekday()  # Mo=1...So=7

        title = item.get('title', '')
        session_type = map_title_to_session_type(title, item.get('sportTypeKey', 'running'))

        cur.execute("""
            INSERT INTO training_plan (week_date, day_of_week, session_type, session_zone, duration_min, distance_km, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            str(item_monday),
            day_of_week,
            session_type,
            '',
            0,
            0,
            title
        ))
        print(f"  ✅ {item_date} ({item_date.strftime('%A')}) | week_date: {item_monday} | Tag {day_of_week} | {session_type} | {title}")

    conn.commit()
    conn.close()
    print("✅ Garmin Kalender Sync abgeschlossen")

if __name__ == "__main__":
    sync_garmin_calendar()