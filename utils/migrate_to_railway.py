import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

def get_local_connection():
    return psycopg2.connect(
        dbname="hybridfitnessdb",
        user="lexshapes",
        host="localhost"
    )

def get_railway_connection():
    return psycopg2.connect(os.getenv("RAILWAY_DATABASE_URL"))

def migrate():
    print("🔄 Migration startet...")
    
    local = get_local_connection()
    railway = get_railway_connection()
    
    local_cursor = local.cursor()
    railway_cursor = railway.cursor()
    
    # Trainings migrieren
    print("📦 Migriere trainings...")
    local_cursor.execute("SELECT id, date, type, duration_minutes, distance_km, heart_rate_avg, notes, rating, strava_id, hevy_id FROM trainings")
    trainings = local_cursor.fetchall()
    
    inserted = 0
    for t in trainings:
        try:
            railway_cursor.execute("""
                INSERT INTO trainings (id, date, type, duration_minutes, distance_km, heart_rate_avg, notes, rating, strava_id, hevy_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
            """, t)
            inserted += 1
        except Exception as e:
            print(f"⚠️ {e}")
    
    railway.commit()
    print(f"✅ {inserted} Trainings migriert")
    
    # Splits migrieren
    print("📦 Migriere splits...")
    local_cursor.execute("SELECT id, training_id, split_number, distance_km, pace_seconds, heart_rate_avg, elevation_gain FROM splits")
    splits = local_cursor.fetchall()
    
    inserted = 0
    for s in splits:
        try:
            railway_cursor.execute("""
                INSERT INTO splits (id, training_id, split_number, distance_km, pace_seconds, heart_rate_avg, elevation_gain)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
            """, s)
            inserted += 1
        except Exception as e:
            print(f"⚠️ {e}")
    
    railway.commit()
    print(f"✅ {inserted} Splits migriert")
    
    # Feelings migrieren
    print("📦 Migriere feelings...")
    local_cursor.execute("SELECT id, date, energy_level, sleep_quality, stress_level, notes FROM feelings")
    feelings = local_cursor.fetchall()
    
    inserted = 0
    for f in feelings:
        try:
            railway_cursor.execute("""
                INSERT INTO feelings (id, date, energy_level, sleep_quality, stress_level, notes)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
            """, f)
            inserted += 1
        except Exception as e:
            print(f"⚠️ {e}")
    
    railway.commit()
    print(f"✅ {inserted} Feelings migriert")
    
    local.close()
    railway.close()
    print("🎉 Migration abgeschlossen!")

if __name__ == "__main__":
    migrate()