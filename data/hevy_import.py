import requests
from dotenv import load_dotenv
import os

from pathlib import Path
load_dotenv(Path(__file__).parent.parent / '.env')

def get_hevy_workouts(limit=10):
    all_workouts = []
    page = 1
    
    while True:
        try:
            api_key = os.getenv("HEVY_API_KEY")
            url = "https://api.hevyapp.com/v1/workouts"
            headers = {"api-key": api_key}
            params = {"pageSize": 10, "page": page}
            
            response = requests.get(url, headers=headers, params=params)
            data = response.json()
            
            workouts = data.get('workouts', [])
            if not workouts:
                break
                
            all_workouts.extend(workouts)
            print(f"✅ Seite {page}: {len(workouts)} Workouts geladen")
            page += 1
            
        except Exception as e:
            print(f"❌ Fehler: {e}")
            break
    
    print(f"✅ Total: {len(all_workouts)} Workouts geladen!")
    return all_workouts

if __name__ == "__main__":
    workouts = get_hevy_workouts()
    for workout in workouts:
        print(f"{workout.get('start_time', '')[:10]} - {workout.get('title', '')} - {len(workout.get('exercises', []))} Übungen")