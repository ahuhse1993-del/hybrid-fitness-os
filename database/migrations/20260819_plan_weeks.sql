-- Migration: 20260819_plan_weeks
-- 1) Extra race-Felder auf plans (Höhe, Priorität, Zielzeit)
-- 2) plan_weeks-Tabelle: kanonische Phase + Last pro Woche
-- 3) week_id FK auf training_plan
-- Alle Statements sind idempotent (IF NOT EXISTS).

BEGIN;

ALTER TABLE plans
  ADD COLUMN IF NOT EXISTS race_elevation_m  INTEGER,
  ADD COLUMN IF NOT EXISTS race_priority     CHAR(1) DEFAULT 'A',
  ADD COLUMN IF NOT EXISTS target_time       VARCHAR(20),
  ADD COLUMN IF NOT EXISTS plan_start_date   DATE;

CREATE TABLE IF NOT EXISTS plan_weeks (
    id              SERIAL PRIMARY KEY,
    plan_id         INTEGER NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
    week_number     INTEGER NOT NULL,
    week_start      DATE    NOT NULL,
    phase           VARCHAR(20) NOT NULL DEFAULT 'base',
    is_deload       BOOLEAN NOT NULL DEFAULT FALSE,
    is_peak         BOOLEAN NOT NULL DEFAULT FALSE,
    target_run_km   NUMERIC(6,1),
    week_focus      VARCHAR(200),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (plan_id, week_number)
);

CREATE INDEX IF NOT EXISTS plan_weeks_plan_week_start
    ON plan_weeks (plan_id, week_start);

DROP TRIGGER IF EXISTS plan_weeks_updated_at ON plan_weeks;
CREATE TRIGGER plan_weeks_updated_at
    BEFORE UPDATE ON plan_weeks
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

ALTER TABLE training_plan
  ADD COLUMN IF NOT EXISTS week_id INTEGER REFERENCES plan_weeks(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS training_plan_week_id_idx
    ON training_plan (week_id);

ALTER TABLE milestones
  ADD COLUMN IF NOT EXISTS week_number INTEGER;

COMMIT;
