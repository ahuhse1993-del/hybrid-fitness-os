import psycopg2

def get_connection():
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