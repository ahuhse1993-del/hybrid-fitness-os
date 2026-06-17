import requests
from dotenv import load_dotenv
import os

from pathlib import Path
load_dotenv(Path(__file__).parent.parent / '.env')

def get_hevy_workouts(limit=10):
    try:
        api_key = os.getenv("HEVY_API_KEY")
        url = "https://api.hevyapp.com/v1/workouts"
        headers = {"api-key": api_key}
        params = {"pageSize": limit}
        
        response = requests.get(url, headers=headers, params=params)
        data = response.json()
        print(f"API Antwort: {data}")
        
        print(f"✅ {len(data.get('workouts', []))} Workouts geladen!")
        return data.get('workouts', [])
    except Exception as e:
        print(f"❌ Fehler: {e}")
        return []

if __name__ == "__main__":
    workouts = get_hevy_workouts()
    for workout in workouts:
        print(f"{workout.get('start_time', '')[:10]} - {workout.get('title', '')} - {len(workout.get('exercises', []))} Übungen")