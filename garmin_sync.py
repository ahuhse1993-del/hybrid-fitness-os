from garminconnect import Garmin
import os, json
from datetime import date, timedelta
from dotenv import load_dotenv

load_dotenv()

client = Garmin(os.getenv("GARMIN_EMAIL"), os.getenv("GARMIN_PASSWORD"))
client.login()

today = date.today()
yesterday = today - timedelta(days=1)
d = yesterday.isoformat()

print(f"\n=== GARMIN SYNC {today} ===\n")

# 1. HRV
try:
    hrv = client.get_hrv_data(d)
    hrv_val = hrv.get("hrvSummary", {}).get("lastNight")
    print(f"HRV letzte Nacht:  {hrv_val} ms")
except Exception as e:
    print(f"HRV: Fehler — {e}")

# 2. Schlaf
try:
    sleep = client.get_sleep_data(d)
    s = sleep.get("dailySleepDTO", {})
    duration_h = round(s.get("sleepTimeSeconds", 0) / 3600, 1)
    score = s.get("sleepScores", {}).get("overall", {}).get("value")
    print(f"Schlaf:            {duration_h} h  |  Score: {score}")
except Exception as e:
    print(f"Schlaf: Fehler — {e}")

# 3. Body Battery
try:
    bb = client.get_body_battery(d)
    if bb and len(bb) > 0:
        charged = bb[0].get("charged")
        drained = bb[0].get("drained")
        print(f"Body Battery:      +{charged} / -{drained}")
except Exception as e:
    print(f"Body Battery: Fehler — {e}")

# 4. Ruhepuls
try:
    rhr = client.get_rhr_day(d)
    val = rhr.get("restingHeartRate")
    print(f"Ruhepuls:          {val} bpm")
except Exception as e:
    print(f"Ruhepuls: Fehler — {e}")

# 5. Aktivitäten letzte 7 Tage
try:
    activities = client.get_activities(0, 7)
    print(f"\nAktivitäten (letzte 7):")
    for a in activities:
        name = a.get("activityName")
        start = a.get("startTimeLocal", "")[:10]
        dist = round(a.get("distance", 0) / 1000, 1)
        print(f"  {start}  {name}  {dist} km")
except Exception as e:
    print(f"Aktivitäten: Fehler — {e}")

print("\n=== FERTIG ===")
