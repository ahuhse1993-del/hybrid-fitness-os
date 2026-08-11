"""
CAIRN – DB Migration: Plan-Generierung auf GitHub Actions umstellen
Neue Felder für strukturierte Quality-Session-Details in training_plan,
neue Tabelle plan_jobs für den asynchronen Job-Status (ersetzt das
In-Memory-Dict in coach/api.py).
Ausführen: python data/migrate_plan_v3.py
"""
import os, psycopg2
from dotenv import load_dotenv
load_dotenv()

def migrate():
    conn = psycopg2.connect(os.getenv("RAILWAY_DATABASE_URL") or os.getenv("DATABASE_URL"))
    cur = conn.cursor()

    print("Migration startet...")

    # 1. training_plan erweitern — strukturierte Warm-Up/Hauptteil/Cool-Down Felder
    migrations = [
        "ALTER TABLE training_plan ADD COLUMN IF NOT EXISTS warmup_km DECIMAL",
        "ALTER TABLE training_plan ADD COLUMN IF NOT EXISTS warmup_min INTEGER",
        "ALTER TABLE training_plan ADD COLUMN IF NOT EXISTS main_sets INTEGER",
        "ALTER TABLE training_plan ADD COLUMN IF NOT EXISTS main_distance_m INTEGER",
        "ALTER TABLE training_plan ADD COLUMN IF NOT EXISTS main_pace VARCHAR(20)",
        "ALTER TABLE training_plan ADD COLUMN IF NOT EXISTS recovery_m INTEGER",
        "ALTER TABLE training_plan ADD COLUMN IF NOT EXISTS cooldown_km DECIMAL",
        "ALTER TABLE training_plan ADD COLUMN IF NOT EXISTS cooldown_min INTEGER",
        # garmin_workout_id existiert bereits als BIGINT (migrate_plan_v2) — auf VARCHAR(100) heben.
        # ADD ... IF NOT EXISTS ist ein No-Op falls die Spalte schon existiert, daher zusätzlich ALTER TYPE.
        "ALTER TABLE training_plan ADD COLUMN IF NOT EXISTS garmin_workout_id VARCHAR(100)",
        "ALTER TABLE training_plan ALTER COLUMN garmin_workout_id TYPE VARCHAR(100) USING garmin_workout_id::VARCHAR(100)",
    ]

    for sql in migrations:
        try:
            cur.execute(sql)
            print(f"OK: {sql[:70]}...")
        except Exception as e:
            print(f"Skip (existiert bereits / kein Effekt): {e}")
            conn.rollback()
            conn = psycopg2.connect(os.getenv("RAILWAY_DATABASE_URL") or os.getenv("DATABASE_URL"))
            cur = conn.cursor()

    # 2. Neue Tabelle: plan_jobs (Job-Status für die GitHub-Actions-basierte Plan-Generierung)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS plan_jobs (
            id VARCHAR(50) PRIMARY KEY,
            data JSONB,
            status VARCHAR(20) DEFAULT 'pending',
            error TEXT,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """)
    print("OK: Tabelle 'plan_jobs' erstellt")

    conn.commit()
    conn.close()
    print("\nMigration abgeschlossen!")

if __name__ == "__main__":
    migrate()
