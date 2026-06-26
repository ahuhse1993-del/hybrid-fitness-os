"""
CAIRN Garmin Calendar Sync
Holt geplante Workouts aus Garmin Kalender → training_plan Tabelle
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from garminconnect import Garmin
from database.connection import get_connection
from datetime import date, timedelta

def map_sport_to_session_type(sport_key):
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

    # Monate für die nächsten 5 Wochen
    months = set()
    for i in range(5):
        d = today + timedelta(weeks=i)
        months.add((d.year, d.month))

    # Geplante Workouts holen — Duplikate via workout_id verhindern
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

    # Bestehende zukünftige Einträge löschen
    monday = today - timedelta(days=today.weekday())
    sunday = monday - timedelta(days=1)
    cur.execute("DELETE FROM training_plan WHERE week_date >= %s", (sunday,))

    for item in planned:
        item_date = date.fromisoformat(item['date'])
        if item_date < today:
            continue

        # Sonntag der Woche als week_date (Tag 0 = Sonntag)
        item_monday = item_date - timedelta(days=item_date.weekday())
        item_sunday = item_monday - timedelta(days=1)
        day_of_week = (item_date - item_sunday).days - 1

        session_type = map_sport_to_session_type(item.get('sportTypeKey', 'running'))
        title = item.get('title', '')

        cur.execute("""
            INSERT INTO training_plan (week_date, day_of_week, session_type, session_zone, duration_min, distance_km, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            str(item_sunday),
            day_of_week,
            session_type,
            '',
            0,
            0,
            title
        ))
        print(f"  ✅ {item_date} | Tag {day_of_week} | {session_type} | {title}")

    conn.commit()
    conn.close()
    print("✅ Garmin Kalender Sync abgeschlossen")

if __name__ == "__main__":
    sync_garmin_calendar()