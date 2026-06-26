"""
CAIRN Data Service
Aktivitäten aus DB — kein Live-Garmin-Call auf Railway.
Garmin Health wird via sync_garmin_health() lokal gesyncт und in DB gespeichert.
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
        return [{
            "date": str(r[0]),
            "type": r[1],
            "name": r[2] or r[1],
            "duration_min": r[3],
            "distance_km": float(r[4]) if r[4] else 0,
            "avg_hr": r[5],
        } for r in rows]
    except Exception as e:
        return [{"error": str(e)}]

def get_daily_snapshot(target_date: str = None) -> dict:
    if target_date is None:
        target_date = (date.today() - timedelta(days=1)).isoformat()

    sleep = {"duration_h": None, "score": None, "deep_h": None, "rem_h": None}
    hrv = {"hrv_last_night": None, "hrv_5day_avg": None, "status": None}
    bb = {"charged": None, "drained": None}
    rhr = {"rhr": None}

    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT sleep_duration_h, sleep_score, sleep_deep_h, sleep_rem_h,
                   hrv_last_night, hrv_5day_avg, hrv_status,
                   body_battery_charged, body_battery_drained, resting_hr
            FROM daily_logs WHERE date = %s
        """, (target_date,))
        row = cur.fetchone()
        conn.close()
        if row:
            sleep = {"duration_h": row[0], "score": row[1], "deep_h": row[2], "rem_h": row[3]}
            hrv = {"hrv_last_night": row[4], "hrv_5day_avg": row[5], "status": row[6]}
            bb = {"charged": row[7], "drained": row[8]}
            rhr = {"rhr": row[9]}
    except Exception:
        pass

    return {
        "date": target_date,
        "sleep": sleep,
        "hrv": hrv,
        "body_battery": bb,
        "resting_hr": rhr,
        "recent_activities": get_activities(7),
    }

def sync_garmin_health(target_date: str = None):
    """
    Lokal ausführen — holt Garmin Health und schreibt in DB.
    Auf Railway nicht aufrufen.
    """
    if target_date is None:
        target_date = (date.today() - timedelta(days=1)).isoformat()
    try:
        from garminconnect import Garmin
        client = Garmin(os.getenv("GARMIN_EMAIL"), os.getenv("GARMIN_PASSWORD"))
        client.login()

        s = client.get_sleep_data(target_date).get("dailySleepDTO", {})
        hrv_data = client.get_hrv_data(target_date).get("hrvSummary", {})
        bb_data = client.get_body_battery(target_date)
        stats = client.get_stats(target_date)

        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO daily_logs (date, sleep_duration_h, sleep_score, sleep_deep_h, sleep_rem_h,
                hrv_last_night, hrv_5day_avg, hrv_status,
                body_battery_charged, body_battery_drained, resting_hr)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (date) DO UPDATE SET
                sleep_duration_h=EXCLUDED.sleep_duration_h,
                sleep_score=EXCLUDED.sleep_score,
                sleep_deep_h=EXCLUDED.sleep_deep_h,
                sleep_rem_h=EXCLUDED.sleep_rem_h,
                hrv_last_night=EXCLUDED.hrv_last_night,
                hrv_5day_avg=EXCLUDED.hrv_5day_avg,
                hrv_status=EXCLUDED.hrv_status,
                body_battery_charged=EXCLUDED.body_battery_charged,
                body_battery_drained=EXCLUDED.body_battery_drained,
                resting_hr=EXCLUDED.resting_hr
        """, (
            target_date,
            round(s.get("sleepTimeSeconds", 0) / 3600, 1),
            s.get("sleepScores", {}).get("overall", {}).get("value"),
            round(s.get("deepSleepSeconds", 0) / 3600, 1),
            round(s.get("remSleepSeconds", 0) / 3600, 1),
            hrv_data.get("lastNightAvg"),      # Fix: war lastNight
            hrv_data.get("weeklyAvg"),          # Fix: war lastNight5MinHigh
            hrv_data.get("status"),
            bb_data[0].get("charged") if bb_data else None,
            bb_data[0].get("drained") if bb_data else None,
            stats.get("restingHeartRate"),
        ))
        conn.commit()
        conn.close()
        print(f"✅ Garmin Health sync für {target_date}")
    except Exception as e:
        print(f"❌ Fehler: {e}")

if __name__ == "__main__":
    import json
    print("=== CAIRN Data Service Test ===\n")
    snapshot = get_daily_snapshot()
    print(json.dumps(snapshot, indent=2, ensure_ascii=False))