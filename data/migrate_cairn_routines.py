"""
CAIRN – DB Migration: cairn_routines
Lokaler Cache der Hevy CAIRN-Ordner-Routinen, gefüllt durch data/hevy_routines_sync.py.
Ausführen: python data/migrate_cairn_routines.py
"""
import os, psycopg2
from dotenv import load_dotenv
load_dotenv()

def migrate():
    conn = psycopg2.connect(os.getenv("RAILWAY_DATABASE_URL") or os.getenv("DATABASE_URL"))
    cur = conn.cursor()

    print("Migration startet...")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS cairn_routines (
            id SERIAL PRIMARY KEY,
            title VARCHAR(200) UNIQUE NOT NULL,
            exercises JSONB DEFAULT '[]',
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """)
    print("OK: Tabelle 'cairn_routines' erstellt")

    conn.commit()
    conn.close()
    print("\nMigration abgeschlossen!")

if __name__ == "__main__":
    migrate()
