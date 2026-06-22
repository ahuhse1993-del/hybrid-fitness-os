import psycopg2
import os
from dotenv import load_dotenv

load_dotenv("/Users/lexshapes/hybrid-fitness-os/.env")

def get_connection():
    # Railway hat Priorität
    database_url = os.getenv("RAILWAY_DATABASE_URL") or os.getenv("DATABASE_URL")
    
    if database_url:
        conn = psycopg2.connect(database_url)
    else:
        conn = psycopg2.connect(
            dbname="hybridfitnessdb",
            user="lexshapes",
            host="localhost"
        )
    return conn

def test_connection():
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM trainings")
        count = cur.fetchone()[0]
        print(f"✅ Verbindung erfolgreich — {count} Trainings")
        conn.close()
    except Exception as e:
        print(f"❌ Fehler: {e}")

if __name__ == "__main__":
    test_connection()
