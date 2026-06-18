import psycopg2
import os

def get_connection():
    database_url = os.getenv("DATABASE_URL")
    
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
        print("✅ Datenbankverbindung erfolgreich!")
        conn.close()
    except Exception as e:
        print(f"❌ Fehler: {e}")

if __name__ == "__main__":
    test_connection()