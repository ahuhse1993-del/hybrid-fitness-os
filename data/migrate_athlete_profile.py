"""
CAIRN – DB Migration: Athlete Profile + Coach Context
Ausführen: python data/migrate_athlete_profile.py
"""
import os, psycopg2
from dotenv import load_dotenv
load_dotenv()

def migrate():
    conn = psycopg2.connect(os.getenv("RAILWAY_DATABASE_URL") or os.getenv("DATABASE_URL"))
    cur = conn.cursor()

    print("Migration startet...")

    # Schicht 1 — Visuelles Profil, editierbar durch den Athleten. Ein Row pro Athlet.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS athlete_profile (
            id SERIAL PRIMARY KEY,
            name VARCHAR(100),
            age INTEGER,
            weight_kg FLOAT,
            hr_z1_min INTEGER, hr_z1_max INTEGER,
            hr_z2_min INTEGER, hr_z2_max INTEGER,
            hr_z3_min INTEGER, hr_z3_max INTEGER,
            hr_z4_min INTEGER, hr_z4_max INTEGER,
            hr_z5_min INTEGER, hr_z5_max INTEGER,
            pace_z1 VARCHAR(20), pace_z2 VARCHAR(20), pace_z3 VARCHAR(20),
            pace_z4 VARCHAR(20), pace_z5 VARCHAR(20),
            shoes JSONB DEFAULT '[]',
            cross_rennrad BOOLEAN DEFAULT FALSE,
            cross_schwimmen BOOLEAN DEFAULT FALSE,
            cross_wandern BOOLEAN DEFAULT FALSE,
            cross_ski BOOLEAN DEFAULT FALSE,
            long_term_goals TEXT,
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """)
    print("OK: Tabelle 'athlete_profile' erstellt")

    # Schicht 2 — Coach-Kontext, read-only für den Athleten. Mehrere Einträge über Zeit.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS coach_context (
            id SERIAL PRIMARY KEY,
            category VARCHAR(50),
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    print("OK: Tabelle 'coach_context' erstellt")

    conn.commit()
    conn.close()
    print("\nMigration abgeschlossen!")

if __name__ == "__main__":
    migrate()
