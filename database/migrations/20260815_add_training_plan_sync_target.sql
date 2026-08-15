-- Migration: 20260815_add_training_plan_sync_target
-- Fuegt die restlichen Felder aus dem upsert_planned_workout-Vertrag hinzu:
-- name (eigenstaendiger Titel, getrennt von notes), sport (running/cycling/
-- strength, unabhaengig vom groberen session_type), target (JSONB, HF-/Pace-
-- Zielstruktur einer Session — reichhaltiger als das bestehende session_zone-
-- Textfeld), sync_target (expliziter Zielsystem-Wert: garmin|hevy|cairn_only,
-- der einzige gueltige Wortschatz laut coach/session_routing.py).
--
-- structure (Workout-Schritte) wird NICHT neu angelegt — dafuer existiert
-- bereits workout_steps JSONB (aus migrate_plan_v2.py), keine Duplikat-Spalte.

BEGIN;

ALTER TABLE training_plan
    ADD COLUMN IF NOT EXISTS name TEXT,
    ADD COLUMN IF NOT EXISTS sport TEXT,
    ADD COLUMN IF NOT EXISTS target JSONB,
    ADD COLUMN IF NOT EXISTS sync_target TEXT;

ALTER TABLE training_plan
    DROP CONSTRAINT IF EXISTS training_plan_sync_target_check;
ALTER TABLE training_plan
    ADD CONSTRAINT training_plan_sync_target_check
    CHECK (sync_target IS NULL OR sync_target IN ('garmin', 'hevy', 'cairn_only'));

-- Backfill: sync_target aus den bereits vorhandenen Spalten source/
-- garmin_push_required ableiten, fuer alle Zeilen, die diese schon gesetzt
-- haben (aus der vorherigen Migration). Nichts geloescht, nur ergaenzt.
UPDATE training_plan
SET sync_target = CASE
    WHEN source = 'hevy' THEN 'hevy'
    WHEN garmin_push_required = true THEN 'garmin'
    ELSE 'cairn_only'
END
WHERE sync_target IS NULL;

COMMIT;
