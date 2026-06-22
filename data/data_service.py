"""
CAIRN Data Service
Auf Railway: Aktivitäten aus DB, Garmin-Health wenn möglich.
"""

from datetime import date, timedelta
import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

def get_db():
    database_url = os.getenv("RAILWAY_DATABASE_URL") or os.getenv("DATABASE_URL")
    if database_url:
        return psycopg2.connect(database_url)
    return psycopg2.connect(dbname="hybridfitnessdb", user="lexshapes", host="localhost")

def get_activities(limit: int = 7) -> list:
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT date, type, notes, duration_minutes, distance_km, heart_rate_avg
            FROM trainings ORDER BY date DESC LIMIT %s
        """, (limit,))
        rows = cur.fetchall()
        conn.close()
        result = []
        for r in rows:
            result.append({
                "date": str(r[0]),
                "type": r[1],
                "name": r[2] or r[1],
                "duration_min": r[3],
                "distance_km": float(r[4]) if r[4] else 0,
                "avg_hr": r[5],
            })
        return result
    except Exception as e:
        return [{"error": str(e)}]

def get_daily_snapshot(target_date: str = None) -> dict:
    if target_date is None:
        target_date = (date.today() - timedelta(days=1)).isoformat()

    # Aktivitäten immer aus DB
    activities = get_activities(7)

    # Garmin-Health nur versuchen wenn lokal — auf Railway überspringen
    sleep = {"duration_h": None, "score": None, "deep_h": None, "rem_h": None}
    hrv = {"hrv_last_night": None, "hrv_5day_avg": None, "status": None}
    bb = {"charged": None, "drained": None}
    rhr = {"rhr": None}

    try:
        from garminconnect import Garmin
        client = Garmin(os.getenv("GARMIN_EMAIL"), os.getenv("GARMIN_PASSWORD"))
        client.login()

        s = client.get_sleep_data(target_date).get("dailySleepDTO", {})
        sleep = {
            "duration_h": round(s.get("sleepTimeSeconds", 0) / 3600, 1),
            "score": s.get("sleepScores", {}).get("overall", {}).get("value"),
            "deep_h": round(s.get("deepSleepSeconds", 0) / 3600, 1),
            "rem_h": round(s.get("remSleepSeconds", 0) / 3600, 1),
        }

        hrv_data = client.get_hrv_data(target_date).get("hrvSummary", {})
        hrv = {
            "hrv_last_night": hrv_data.get("lastNight"),
            "hrv_5day_avg": hrv_data.get("lastNight5MinHigh"),
            "status": hrv_data.get("status"),
        }

        bb_data = client.get_body_battery(target_date)
        if bb_data:
            bb = {"charged": bb_data[0].get("charged"), "drained": bb_data[0].get("drained")}

        stats = client.get_stats(target_date)
        rhr = {"rhr": stats.get("restingHeartRate")}

    except Exception:
        pass  # Garmin nicht erreichbar — Fallback-Werte bleiben

    return {
        "date": target_date,
        "sleep": sleep,
        "hrv": hrv,
        "body_battery": bb,
        "resting_hr": rhr,
        "recent_activities": activities,
    }

if __name__ == "__main__":
    import json
    print("=== CAIRN Data Service Test ===\n")
    snapshot = get_daily_snapshot()
    print(json.dumps(snapshot, indent=2, ensure_ascii=False))