import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

def fix_duplicates():
    conn = psycopg2.connect(
        dbname="hybridfitnessdb",
        user="lexshapes",
        host="localhost"
    )
    cursor = conn.cursor()
    
    print("🔄 Duplikate werden bereinigt...")
    
    cursor.execute("""
        SELECT date FROM trainings 
        WHERE type = 'WeightTraining' 
        GROUP BY date 
        HAVING COUNT(*) > 1
    """)
    dates = cursor.fetchall()
    
    fixed = 0
    for (date,) in dates:
        cursor.execute("""
            SELECT id, duration_minutes, heart_rate_avg, hevy_id, strava_id, notes
            FROM trainings 
            WHERE date = %s AND type = 'WeightTraining'
            ORDER BY hevy_id NULLS LAST
        """, (date,))
        entries = cursor.fetchall()
        
        hevy_entry = None
        strava_entry = None
        
        for entry in entries:
            if entry[3]:
                hevy_entry = entry
            elif entry[4]:
                strava_entry = entry
        
        if hevy_entry and strava_entry:
            cursor.execute("""
                UPDATE trainings 
                SET duration_minutes = COALESCE(duration_minutes, %s), 
                    heart_rate_avg = COALESCE(heart_rate_avg, %s)
                WHERE id = %s
            """, (strava_entry[1], strava_entry[2], hevy_entry[0]))
            
            cursor.execute("DELETE FROM trainings WHERE id = %s", (strava_entry[0],))
            fixed += 1
    
    conn.commit()
    conn.close()
    print(f"✅ {fixed} Duplikate bereinigt!")

if __name__ == "__main__":
    fix_duplicates()