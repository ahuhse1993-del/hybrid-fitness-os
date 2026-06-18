import psycopg2
import os
import sys
sys.path.append('.')
from dotenv import load_dotenv
from data.hevy_import import get_hevy_workouts
from datetime import datetime

load_dotenv()

def fix_hevy_duration():
    print("🔄 Hevy Dauern werden korrigiert...")
    
    workouts = get_hevy_workouts()
    
    conn = psycopg2.connect(os.getenv("RAILWAY_DATABASE_URL"))
    cursor = conn.cursor()
    
    fixed = 0
    for workout in workouts:
        hevy_id = str(workout.get('id', ''))
        start = workout.get('start_time', '')
        end = workout.get('end_time', '')
        
        if start and end:
            start_dt = datetime.fromisoformat(start)
            end_dt = datetime.fromisoformat(end)
            duration = round((end_dt - start_dt).total_seconds() / 60)
            
            cursor.execute("""
                UPDATE trainings 
                SET duration_minutes = %s
                WHERE hevy_id = %s
            """, (duration, hevy_id))
            fixed += 1
    
    conn.commit()
    conn.close()
    print(f"✅ {fixed} Dauern korrigiert!")

if __name__ == "__main__":
    fix_hevy_duration()