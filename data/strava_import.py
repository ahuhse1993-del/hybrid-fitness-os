import requests
from dotenv import load_dotenv
import os

load_dotenv()

def get_strava_activities(limit=10):
    try:
        access_token = os.getenv("STRAVA_ACCESS_TOKEN")
        url = "https://www.strava.com/api/v3/athlete/activities"
        headers = {"Authorization": f"Bearer {access_token}"}
        params = {"per_page": limit}
        
        response = requests.get(url, headers=headers, params=params)
        activities = response.json()
        
        print(f"✅ {len(activities)} Aktivitäten geladen!")
        return activities
    except Exception as e:
        print(f"❌ Fehler: {e}")
        return []
if __name__ == "__main__":
    activities = get_strava_activities()
    print(activities)