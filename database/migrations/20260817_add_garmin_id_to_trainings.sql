-- Garmin Activity-ID Spalte für trainings Tabelle
-- Idempotent — kann mehrfach ausgeführt werden
ALTER TABLE trainings ADD COLUMN IF NOT EXISTS garmin_id TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS trainings_garmin_id_idx
    ON trainings (garmin_id)
    WHERE garmin_id IS NOT NULL;
