-- Migration: 20260815_add_sync_fields
-- Fuer die Garmin-Batch-Push-Engine: Sync-Status pro Session, Fehlertext,
-- Content-Hash (erkennt Aenderungen -> dirty) und Zeitpunkt der letzten
-- erfolgreichen Synchronisation.

BEGIN;

ALTER TABLE training_plan
    ADD COLUMN IF NOT EXISTS sync_status     VARCHAR(20) DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS sync_error      TEXT,
    ADD COLUMN IF NOT EXISTS content_hash    VARCHAR(64),
    ADD COLUMN IF NOT EXISTS last_synced_at  TIMESTAMPTZ;

-- sync_status Werte: NULL (nie gepusht), 'synced', 'dirty', 'failed', 'pending'
-- content_hash: SHA256 der Push-relevanten Felder

ALTER TABLE training_plan
    DROP CONSTRAINT IF EXISTS training_plan_sync_status_check;
ALTER TABLE training_plan
    ADD CONSTRAINT training_plan_sync_status_check
    CHECK (sync_status IS NULL OR sync_status IN ('synced', 'dirty', 'failed', 'pending'));

CREATE INDEX IF NOT EXISTS training_plan_sync_idx
    ON training_plan (sync_target, status, sync_status)
    WHERE sync_target = 'garmin';

COMMIT;
