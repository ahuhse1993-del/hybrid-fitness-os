"""
CAIRN – DB Migration: Workout-Vorschläge auf plans
Ausführen: python data/migrate_workout_suggestions.py
"""
import os, psycopg2
from dotenv import load_dotenv
load_dotenv()

def migrate():
    conn = psycopg2.connect(os.getenv("RAILWAY_DATABASE_URL") or os.getenv("DATABASE_URL"))
    cur = conn.cursor()

    print("Migration startet...")

    # Vorschläge des Coaches zu den geplanten Strength-Training-Sessions.
    # Liste von Objekten: {id, workout_name, change, reason, status, comment, created_at, responded_at}
    cur.execute("ALTER TABLE plans ADD COLUMN IF NOT EXISTS workout_suggestions JSONB DEFAULT '[]'")
    print("OK: Spalte 'workout_suggestions' auf 'plans' erstellt")

    conn.commit()
    conn.close()
    print("\nMigration abgeschlossen!")

if __name__ == "__main__":
    migrate()
