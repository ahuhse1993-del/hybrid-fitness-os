"""
CAIRN Data Service
Abstraktionsschicht zwischen CAIRN und allen Datenquellen.
Auf Railway: Daten aus DB. Lokal: Daten von Garmin live.
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

def _get_garmin_client():
    from garminconnect import Garmin
    client = Garmin(os.getenv("GARMIN_EMAIL"), os.getenv("GARMIN_PASSWORD"))
    client.login()
    return client

def get_sleep(target_date: str = None) -> dict:
    if target_date is None:
        target_date = (date.today() - timedelta(days=1)).isoformat()
    try:
        client = _get_garmin_client()
        sleep = client.get_sleep_data(target_date)
        s = sleep.get("dailySleepDTO", {})
        return {
            "date": target_date,
            "duration_h": round(s.get("sleepTimeSeconds", 0) / 3600, 1),
            "score": s.get("sleepScores", {}).get("overall", {}).get("value"),
            "deep_h": round(s.get("deepSleepSeconds", 0) / 3600, 1),
            "rem_h": round(s.get("remSleepSeconds", 0) / 3600, 1),
        }
    except Exception as e:
        return {"date": target_date, "error": str(e)}

def get_hrv(target_date: str = None) -> dict:
    if target_date is None:
        target_date = (date.today() - timedelta(days=1)).isoformat()
    try:
        client = _get_garmin_client()
        hrv = client.get_hrv_data(target_date)
        summary = hrv.get("hrvSummary", {})
        return {
            "date": target_date,
            "hrv_last_night": summary.get("lastNight"),
            "hrv_5day_avg": summary.get("lastNight5MinHigh"),
            "status": summary.get("status"),
        }
    except Exception as e:
        return {"date": target_date, "error": str(e)}

def get_body_battery(target_date: str = None) -> dict:
    if target_date is None:
        target_date = (date.today() - timedelta(days=1)).isoformat()
    try:
        client = _get_garmin_client()
        bb = client.get_body_battery(target_date)
        if bb and len(bb) > 0:
            return {"date": target_date, "charged": bb[0].get("charged"), "drained": bb[0].get("drained")}
        return {"date": target_date, "charged": None, "drained": None}
    except Exception as e:
        return {"date": target_date, "error": str(e)}

def get_resting_hr(target_date: str = None) -> dict:
    if target_date is None:
        target_date = (date.today() - timedelta(days=1)).isoformat()
    try:
        client = _get_garmin_client()
        stats = client.get_stats(target_date)
        return {"date": target_date, "rhr": stats.get("restingHeartRate")}
    except Exception as e:
        return {"date": target_date, "error": str(e)}

def get_activities(limit: int = 7) -> list:
    """Holt Aktivitäten aus der DB — funktioniert auf Railway und lokal."""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT date, type, notes, duration_minutes, distance_km, heart_rate_avg
            FROM trainings
            ORDER BY date DESC
            LIMIT %s
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
    """
    Holt alle relevanten Daten für den Coach.
    Garmin-Health-Daten: live wenn möglich, sonst Fallback-Werte.
    Aktivitäten: immer aus DB.
    """
    if target_date is None:
        target_date = (date.today() - timedelta(days=1)).isoformat()

    # Aktivitäten immer aus DB
    activities = get_activities(7)

    # Garmin-Health-Daten versuchen — Fallback wenn nicht erreichbar
    sleep = get_sleep(target_date)
    hrv = get_hrv(target_date)
    bb = get_body_battery(target_date)
    rhr = get_resting_hr(target_date)

    # Wenn Garmin nicht erreichbar — letzte bekannte Werte aus DB holen
    if "error" in sleep:
        sleep = {"duration_h": "unbekannt", "score": None, "deep_h": None, "rem_h": None}
    if "error" in hrv:
        hrv = {"hrv_last_night": None, "hrv_5day_avg": None, "status": "unbekannt"}
    if "error" in bb:
        bb = {"charged": None, "drained": None}
    if "error" in rhr:
        rhr = {"rhr": None}

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