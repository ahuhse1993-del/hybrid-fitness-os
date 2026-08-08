"""
CAIRN – Hevy CAIRN-Routinen Sync
Holt die Routinen aus dem CAIRN-Ordner (folder_id) von Hevy und schreibt sie
nach cairn_routines. generate_plan_internal() liest von dort statt live von Hevy.
Ausführen: python data/hevy_routines_sync.py
"""
import os
import requests
import psycopg2
from psycopg2.extras import Json
from dotenv import load_dotenv
load_dotenv()

HEVY_CAIRN_FOLDER_ID = 3380361


def get_db():
    database_url = os.getenv("DATABASE_URL") or os.getenv("RAILWAY_DATABASE_URL")
    return psycopg2.connect(database_url)


def sync_cairn_routines():
    api_key = os.getenv("HEVY_API_KEY")
    resp = requests.get(
        "https://api.hevyapp.com/v1/routines",
        headers={"api-key": api_key},
        params={"page": 1, "pageSize": 10},
        timeout=15
    )
    resp.raise_for_status()
    routines = resp.json().get('routines', [])

    cairn_routines = {}
    for r in routines:
        if r.get('folder_id') != HEVY_CAIRN_FOLDER_ID:
            continue
        title = (r.get('title') or '').strip()
        if not title or title in cairn_routines:
            continue  # Duplikat ignorieren
        cairn_routines[title] = [e.get('title', '') for e in r.get('exercises', []) if e.get('title')]

    if not cairn_routines:
        print("⚠️ Keine CAIRN-Routinen gefunden (folder_id passt nicht?)")
        return

    conn = get_db()
    cur = conn.cursor()
    for title, exercises in cairn_routines.items():
        cur.execute("""
            INSERT INTO cairn_routines (title, exercises, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (title) DO UPDATE SET exercises = EXCLUDED.exercises, updated_at = NOW()
        """, (title, Json(exercises)))
    conn.commit()
    conn.close()

    print(f"✅ {len(cairn_routines)} CAIRN-Routinen synchronisiert: {', '.join(cairn_routines.keys())}")


if __name__ == "__main__":
    sync_cairn_routines()
