"""
CAIRN – DB Migration: training_plan erweitern für Plan-Generator + Garmin Push
Ausführen: python data/migrate_plan_v2.py
"""
import os, psycopg2
from dotenv import load_dotenv
load_dotenv()

def migrate():
    conn = psycopg2.connect(os.getenv("RAILWAY_DATABASE_URL") or os.getenv("DATABASE_URL"))
    cur = conn.cursor()

    print("Migration startet...")

    # 1. training_plan erweitern
    migrations = [
        # Phase des Trainingsplans
        "ALTER TABLE training_plan ADD COLUMN IF NOT EXISTS phase VARCHAR(20) DEFAULT 'base'",
        # Garmin Workout ID nach Push
        "ALTER TABLE training_plan ADD COLUMN IF NOT EXISTS garmin_workout_id BIGINT",
        # Strukturierte Workout Steps als JSON
        "ALTER TABLE training_plan ADD COLUMN IF NOT EXISTS workout_steps JSONB",
        # Plan-Zugehörigkeit (mehrere Pläne möglich)
        "ALTER TABLE training_plan ADD COLUMN IF NOT EXISTS plan_id INTEGER DEFAULT 1",
        # Wochennummer innerhalb des Plans
        "ALTER TABLE training_plan ADD COLUMN IF NOT EXISTS plan_week INTEGER",
    ]

    for sql in migrations:
        try:
            cur.execute(sql)
            print(f"OK: {sql[:60]}...")
        except Exception as e:
            print(f"Skip (existiert bereits): {e}")
            conn.rollback()
            conn = psycopg2.connect(os.getenv("RAILWAY_DATABASE_URL") or os.getenv("DATABASE_URL"))
            cur = conn.cursor()

    # 2. Neue Tabelle: plans (Metadaten pro Plan)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS plans (
            id SERIAL PRIMARY KEY,
            name VARCHAR(200),
            goal_type VARCHAR(50),        -- race / fitness / maintain
            race_name VARCHAR(200),
            race_date DATE,
            race_distance_km FLOAT,
            total_weeks INTEGER,
            days_per_week INTEGER,
            long_run_day INTEGER,         -- 1=Mo...7=So
            quality_sessions INTEGER,
            strength_sessions INTEGER,
            current_week INTEGER DEFAULT 1,
            status VARCHAR(20) DEFAULT 'active',  -- active / completed / archived
            created_at TIMESTAMP DEFAULT NOW(),
            notes TEXT
        )
    """)
    print("OK: Tabelle 'plans' erstellt")

    conn.commit()
    conn.close()
    print("\nMigration abgeschlossen!")

if __name__ == "__main__":
    migrate()