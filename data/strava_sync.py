import sys
sys.path.append('.')
from database.connection import get_connection
import requests
import os
from dotenv import load_dotenv

load_dotenv()

def get_activity_splits(activity_id):
    try:
        access_token = os.getenv("STRAVA_ACCESS_TOKEN")
        url = f"https://www.strava.com/api/v3/activities/{activity_id}"
        headers = {"Authorization": f"Bearer {access_token}"}
        response = requests.get(url, headers=headers)
        data = response.json()
        return data.get('splits_metric', [])
    except Exception as e:
        print(f"❌ Splits Fehler: {e}")
        return []

def get_all_strava_activities():
    all_activities = []
    page = 1
    while True:
        access_token = os.getenv("STRAVA_ACCESS_TOKEN")
        url = "https://www.strava.com/api/v3/athlete/activities"
        headers = {"Authorization": f"Bearer {access_token}"}
        params = {"per_page": 10, "page": page}
        response = requests.get(url, headers=headers, params=params)
        activities = response.json()
        if not activities or not isinstance(activities, list):
            break
        all_activities.extend(activities)
        print(f"✅ Seite {page}: {len(activities)} Aktivitäten geladen")
        page += 1
    print(f"✅ Total: {len(all_activities)} Aktivitäten geladen!")
    return all_activities

def sync_strava_to_db():
    print("🔄 Alle Strava Daten werden geladen...")
    activities = get_all_strava_activities()

    if not activities:
        print("❌ Keine Aktivitäten gefunden")
        return

    conn = get_connection()
    cursor = conn.cursor()

    inserted = 0
    skipped = 0

    for activity in activities:
        if not isinstance(activity, dict):
            continue
        try:
            date = activity.get('start_date_local', '')[:10]
            name = activity.get('name', '')
            sport_type = activity.get('sport_type', '')
            duration = round(activity.get('moving_time', 0) / 60)
            distance = round(activity.get('distance', 0) / 1000, 2)
            heart_rate = activity.get('average_heartrate')
            strava_id = str(activity.get('id', ''))

            cursor.execute("SELECT id FROM trainings WHERE strava_id = %s", (strava_id,))
            existing = cursor.fetchone()
            if existing:
                skipped += 1
                continue

            cursor.execute(
                "INSERT INTO trainings (date, type, duration_minutes, distance_km, heart_rate_avg, notes, strava_id) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (date, sport_type, duration, distance, heart_rate, name, strava_id)
            )

            training_id = cursor.fetchone()[0]
            inserted += 1

            splits = get_activity_splits(activity.get('id'))
            for i, split in enumerate(splits):
                pace = split.get('average_speed', 0)
                pace_seconds = round(1000 / pace) if pace > 0 else 0
                cursor.execute(
                    "INSERT INTO splits (training_id, split_number, distance_km, pace_seconds, heart_rate_avg, elevation_gain) VALUES (%s, %s, %s, %s, %s, %s)",
                    (training_id, i+1, round(split.get('distance', 0)/1000, 2), pace_seconds, split.get('average_heartrate'), split.get('elevation_difference', 0))
                )

        except Exception as e:
            print(f"⚠️ Fehler: {e}")
            continue

    conn.commit()
    conn.close()

    print(f"✅ {inserted} neue Aktivitäten gespeichert")
    print(f"⏭️ {skipped} bereits vorhanden")

if __name__ == "__main__":
    sync_strava_to_db()