"""
CAIRN – DB Migration: Terrain & Höhenmeter pro Session
Neues Feld elevation_gain_m in training_plan für die Höhenmeter einzelner
Sessions (befüllt vom Coach basierend auf Terrain/GPX-Profil des Rennens).
Ausführen: python data/migrate_plan_v4.py
"""
import os, psycopg2
from dotenv import load_dotenv
load_dotenv()

def migrate():
    conn = psycopg2.connect(os.getenv("RAILWAY_DATABASE_URL") or os.getenv("DATABASE_URL"))
    cur = conn.cursor()
    print("Migration startet...")

    cur.execute("ALTER TABLE training_plan ADD COLUMN IF NOT EXISTS elevation_gain_m INTEGER")
    print("OK: elevation_gain_m")

    conn.commit()
    conn.close()
    print("\nMigration abgeschlossen!")

if __name__ == "__main__":
    migrate()
