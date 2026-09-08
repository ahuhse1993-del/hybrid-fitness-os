import sys
sys.path.append('.')
from database.connection import get_connection
import requests
import os
import time
import psycopg2
from dotenv import load_dotenv

load_dotenv()

# Nur Cycling- und Running-Typen importieren — Krafttraining etc. läuft
# ausschließlich über Hevy/CAIRN (siehe coach/session_routing.py), Strava
# soll hier keine zweite Quelle dafür werden.
STRAVA_ALLOWED_TYPES = [
    'Ride', 'VirtualRide', 'MountainBikeRide', 'GravelRide', 'EBikeRide',
    'Run', 'TrailRun', 'VirtualRun'
]


def get_valid_access_token():
    """
    Bevorzugt den in strava_tokens persistierten, automatisch per
    Refresh-Token erneuerten Access Token. Faellt auf STRAVA_ACCESS_TOKEN
    (statisch, laeuft nach ca. 6h ab) zurueck, wenn keine DB konfiguriert
    ist oder noch kein Token in strava_tokens gespeichert wurde (siehe
    data/strava_auth.py, das den Refresh Token dort persistiert).
    """
    db_url = os.getenv("RAILWAY_DATABASE_URL") or os.getenv("DATABASE_URL")
    static_token = os.getenv("STRAVA_ACCESS_TOKEN")
    if not db_url:
        return static_token
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    cur.execute("SELECT access_token, refresh_token, expires_at FROM strava_tokens WHERE id=1")
    row = cur.fetchone()
    conn.close()
    if not row:
        return static_token
    access_token, refresh_token, expires_at = row
    if time.time() > expires_at - 300:
        resp = requests.post("https://www.strava.com/api/v3/oauth/token", data={
            "client_id": os.getenv("STRAVA_CLIENT_ID"),
            "client_secret": os.getenv("STRAVA_CLIENT_SECRET"),
            "grant_type": "refresh_token",
            "refresh_token": refresh_token
        })
        data = resp.json()
        if "access_token" not in data:
            print(f"❌ Strava Token-Refresh fehlgeschlagen: {data}")
            return access_token
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO strava_tokens (id, access_token, refresh_token, expires_at, updated_at)
            VALUES (1, %s, %s, %s, NOW())
            ON CONFLICT (id) DO UPDATE SET
                access_token=EXCLUDED.access_token,
                refresh_token=EXCLUDED.refresh_token,
                expires_at=EXCLUDED.expires_at,
                updated_at=NOW()
        """, [data["access_token"], data["refresh_token"], data["expires_at"]])
        conn.commit()
        conn.close()
        return data["access_token"]
    return access_token


def get_activity_splits(activity_id):
    try:
        access_token = get_valid_access_token()
        url = f"https://www.strava.com/api/v3/activities/{activity_id}"
        headers = {"Authorization": f"Bearer {access_token}"}
        response = requests.get(url, headers=headers)
        data = response.json()
        return data.get('splits_metric', [])
    except Exception as e:
        print(f"❌ Splits Fehler: {e}")
        return []


def get_all_strava_activities(days_back=30):
    all_activities = []
    page = 1
    after_ts = int(time.time()) - (days_back * 86400)
    while True:
        access_token = get_valid_access_token()
        url = "https://www.strava.com/api/v3/athlete/activities"
        headers = {"Authorization": f"Bearer {access_token}"}
        params = {"per_page": 10, "page": page, "after": after_ts}
        response = requests.get(url, headers=headers, params=params)
        activities = response.json()
        if not isinstance(activities, list):
            if response.status_code != 200:
                print(f"❌ Strava API Fehler ({response.status_code}): {activities}")
            break
        if not activities:
            break
        all_activities.extend(activities)
        print(f"✅ Seite {page}: {len(activities)} Aktivitäten geladen")
        page += 1
    print(f"✅ Total: {len(all_activities)} Aktivitäten geladen!")
    return all_activities


def sync_strava_to_db(days_back=30):
    print(f"🔄 Strava-Aktivitäten der letzten {days_back} Tage werden geladen...")
    activities = get_all_strava_activities(days_back=days_back)

    if not activities:
        print("❌ Keine Aktivitäten gefunden")
        return

    conn = get_connection()
    cursor = conn.cursor()

    inserted = 0
    skipped = 0
    ftp = int(os.getenv("CYCLING_FTP_W", "191"))

    for activity in activities:
        if not isinstance(activity, dict):
            continue
        try:
            # Garmin-Quelle überspringen — kommt bereits via Garmin-Sync rein
            external_id = activity.get('external_id', '') or ''
            if 'garmin' in external_id.lower():
                skipped += 1
                continue

            sport_type = activity.get('sport_type') or activity.get('type', '')
            if sport_type not in STRAVA_ALLOWED_TYPES:
                skipped += 1
                continue

            date = activity.get('start_date_local', '')[:10]
            name = activity.get('name', '')
            duration = round(activity.get('moving_time', 0) / 60)
            distance = round(activity.get('distance', 0) / 1000, 2)
            heart_rate = activity.get('average_heartrate')
            strava_id = str(activity.get('id', ''))

            cursor.execute("SELECT id FROM trainings WHERE strava_id = %s", (strava_id,))
            existing = cursor.fetchone()
            if existing:
                skipped += 1
                continue

            avg_power = activity.get('average_watts')
            norm_power = activity.get('weighted_average_watts')
            max_power = activity.get('max_watts')
            tss = None
            if norm_power and ftp:
                if_val = norm_power / ftp
                dur_h = activity.get('moving_time', 0) / 3600
                tss = round(if_val ** 2 * dur_h * 100, 1)

            cursor.execute(
                "INSERT INTO trainings (date, type, duration_minutes, distance_km, heart_rate_avg, notes, "
                "strava_id, avg_power, normalized_power_w, max_power_w, tss_estimate, source) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (date, sport_type, duration, distance, heart_rate, name, strava_id,
                 avg_power, norm_power, max_power, tss, 'strava')
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
    print(f"⏭️ {skipped} übersprungen (bereits vorhanden oder Garmin-Quelle)")


def inspect_strava_sources(days_back=30):
    """Read-only: zeigt pro Strava-Aktivität, ob sie beim Sync als
    Garmin-Duplikat übersprungen oder als Strava-only importiert würde —
    ohne zu schreiben. Zum Prüfen vor dem ersten produktiven Lauf."""
    activities = get_all_strava_activities(days_back=days_back)
    for a in activities:
        ext_id = a.get('external_id', '') or ''
        src = 'GARMIN' if 'garmin' in ext_id.lower() else 'STRAVA-ONLY'
        activity_type = a.get('sport_type') or a.get('type', '') or ''
        start_date = (a.get('start_date_local') or '')[:10]
        name = (a.get('name') or '')[:40]
        print(f"{src:12} | {activity_type:20} | {start_date} | {name}")


if __name__ == "__main__":
    if '--inspect' in sys.argv:
        inspect_strava_sources(days_back=int(os.getenv("STRAVA_SYNC_DAYS_BACK", "30")))
    else:
        sync_strava_to_db(days_back=int(os.getenv("STRAVA_SYNC_DAYS_BACK", "30")))
