"""
CAIRN Data Service
Abstraktionsschicht zwischen CAIRN und allen Datenquellen.
Dashboard und Coach fragen NUR hier an — nie direkt Garmin/Strava/Hevy.
"""

from garminconnect import Garmin
from datetime import date, timedelta
import os
from dotenv import load_dotenv

load_dotenv()

# ── Garmin Client (einmal initialisieren, Session cachen) ──
_garmin_client = None

def get_garmin_client():
    global _garmin_client
    if _garmin_client is None:
        _garmin_client = Garmin(
            os.getenv("GARMIN_EMAIL"),
            os.getenv("GARMIN_PASSWORD")
        )
        _garmin_client.login()
    return _garmin_client


# ── SCHLAF ──
def get_sleep(target_date: str = None) -> dict:
    """
    Gibt Schlafdaten zurück für ein Datum (default: gestern).
    Returns: {"duration_h": 6.7, "score": 78, "date": "2026-06-21"}
    """
    if target_date is None:
        target_date = (date.today() - timedelta(days=1)).isoformat()
    
    try:
        client = get_garmin_client()
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


# ── HRV ──
def get_hrv(target_date: str = None) -> dict:
    """
    Gibt HRV-Daten zurück.
    Returns: {"hrv_last_night": 52, "hrv_5day_avg": 49, "date": "..."}
    """
    if target_date is None:
        target_date = (date.today() - timedelta(days=1)).isoformat()
    
    try:
        client = get_garmin_client()
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


# ── BODY BATTERY ──
def get_body_battery(target_date: str = None) -> dict:
    """
    Gibt Body Battery zurück.
    Returns: {"charged": 61, "drained": 75, "date": "..."}
    """
    if target_date is None:
        target_date = (date.today() - timedelta(days=1)).isoformat()
    
    try:
        client = get_garmin_client()
        bb = client.get_body_battery(target_date)
        if bb and len(bb) > 0:
            return {
                "date": target_date,
                "charged": bb[0].get("charged"),
                "drained": bb[0].get("drained"),
            }
        return {"date": target_date, "charged": None, "drained": None}
    except Exception as e:
        return {"date": target_date, "error": str(e)}


# ── RUHEPULS ──
def get_resting_hr(target_date: str = None) -> dict:
    """
    Gibt Ruhepuls zurück.
    Returns: {"rhr": 48, "date": "..."}
    """
    if target_date is None:
        target_date = (date.today() - timedelta(days=1)).isoformat()
    
    try:
        client = get_garmin_client()
        # Alternativer Endpunkt für Ruhepuls
        stats = client.get_stats(target_date)
        return {
            "date": target_date,
            "rhr": stats.get("restingHeartRate"),
        }
    except Exception as e:
        return {"date": target_date, "error": str(e)}


# ── AKTIVITÄTEN ──
def get_activities(limit: int = 7) -> list:
    """
    Gibt die letzten N Aktivitäten zurück.
    """
    try:
        client = get_garmin_client()
        activities = client.get_activities(0, limit)
        result = []
        for a in activities:
            result.append({
                "name": a.get("activityName"),
                "date": a.get("startTimeLocal", "")[:10],
                "type": a.get("activityType", {}).get("typeKey"),
                "distance_km": round(a.get("distance", 0) / 1000, 1),
                "duration_min": round(a.get("duration", 0) / 60),
                "avg_hr": a.get("averageHR"),
                "elevation_m": a.get("elevationGain"),
                "garmin_id": a.get("activityId"),
            })
        return result
    except Exception as e:
        return [{"error": str(e)}]


# ── TAGES-SNAPSHOT (alles auf einmal für Coach) ──
def get_daily_snapshot(target_date: str = None) -> dict:
    """
    Holt alle relevanten Daten für einen Tag.
    Das ist der Haupt-Endpunkt den der AI Coach aufruft.
    """
    if target_date is None:
        target_date = (date.today() - timedelta(days=1)).isoformat()
    
    return {
        "date": target_date,
        "sleep": get_sleep(target_date),
        "hrv": get_hrv(target_date),
        "body_battery": get_body_battery(target_date),
        "resting_hr": get_resting_hr(target_date),
        "recent_activities": get_activities(7),
    }


# ── TEST ──
if __name__ == "__main__":
    import json
    print("=== CAIRN Data Service Test ===\n")
    snapshot = get_daily_snapshot()
    print(json.dumps(snapshot, indent=2, ensure_ascii=False))
