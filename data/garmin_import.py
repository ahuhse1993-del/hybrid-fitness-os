from garminconnect import Garmin
from dotenv import load_dotenv
import os

load_dotenv()

def get_mfa():
    return input("Garmin MFA Code: ")

def get_garmin_activities(limit=10):
    try:
        email = os.getenv("GARMIN_EMAIL")
        password = os.getenv("GARMIN_PASSWORD")
        client = Garmin(email, password, prompt_mfa=get_mfa)
        client.login()
        activities = client.get_activities(0, limit)
        print(f"✅ {len(activities)} Aktivitäten geladen!")
        return activities
    except Exception as e:
        print(f"❌ Fehler: {e}")
        return []

if __name__ == "__main__":
    activities = get_garmin_activities()
    for activity in activities:
        print(f"{activity['startTimeLocal']} - {activity['activityName']} - {activity['distance']}m")