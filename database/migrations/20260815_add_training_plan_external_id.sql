-- Migration: 20260815_add_training_plan_external_id
-- Stabiler Idempotenz-Anker pro Session für den MCP upsert_training_block-Workflow.
-- Ohne external_id kann eine Session nur über (plan_id, week_date, day_of_week)
-- identifiziert werden, was bei Verschiebungen/Umbenennungen keine stabile
-- Update-statt-Duplikat-Garantie liefert.

BEGIN;

ALTER TABLE training_plan
    ADD COLUMN IF NOT EXISTS external_id TEXT;

-- Partieller Unique-Index: erlaubt NULL für alle bestehenden/legacy Zeilen
-- (die nie eine external_id gesetzt bekommen), erzwingt Eindeutigkeit nur,
-- sobald eine external_id tatsächlich gesetzt wird.
CREATE UNIQUE INDEX IF NOT EXISTS training_plan_external_id_key
    ON training_plan (external_id)
    WHERE external_id IS NOT NULL;

COMMIT;
