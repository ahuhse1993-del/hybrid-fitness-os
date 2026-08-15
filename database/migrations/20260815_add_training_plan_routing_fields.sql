-- Migration: 20260815_add_training_plan_routing_fields
-- Trennt Garmin- (Lauf/Rad) von Hevy-Zustaendigkeit (Kraft) auf training_plan-Ebene.
-- Krafttraining darf niemals an Garmin gesendet werden — garmin_push_required=false
-- macht diese Regel als Datensatz explizit statt implizit ueber Code-Pfade.

BEGIN;

ALTER TABLE training_plan
    ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'planned',
    ADD COLUMN IF NOT EXISTS source TEXT,
    ADD COLUMN IF NOT EXISTS garmin_push_required BOOLEAN NOT NULL DEFAULT true,
    -- Stabiler Routine-Schluessel = cairn_routines.title (das einzige real
    -- vorhandene eindeutige Hevy-Routine-Merkmal in dieser Codebase — Hevy
    -- liefert hier keine dauerhaft gespeicherte numerische Routine-ID).
    ADD COLUMN IF NOT EXISTS hevy_routine_key TEXT,
    -- Idempotenter Link zur tatsaechlich absolvierten Aktivitaet (Hevy- oder
    -- Garmin-Sync), verhindert Doppel-Matching bei wiederholter Synchronisation.
    ADD COLUMN IF NOT EXISTS matched_training_id INTEGER REFERENCES trainings(id);

ALTER TABLE training_plan
    DROP CONSTRAINT IF EXISTS training_plan_status_check;
ALTER TABLE training_plan
    ADD CONSTRAINT training_plan_status_check
    CHECK (status IN ('planned', 'completed', 'skipped', 'moved'));

-- Backfill: bestehende Kraft-Sessions (aus generate_plan.py, session_type
-- 'Strength Training', sowie 'Krafttraining' — beide Schreibweisen kommen im
-- Code vor, siehe garmin_calendar_sync.py) korrekt als Hevy-Zustaendigkeit
-- markieren. Kein Loeschen/Ueberschreiben von Inhalten, nur die beiden neuen
-- Routing-Spalten fuer bereits vorhandene Zeilen korrigieren.
UPDATE training_plan
SET garmin_push_required = false,
    source = 'hevy'
WHERE session_type IN ('Strength Training', 'Krafttraining')
  AND (garmin_push_required IS DISTINCT FROM false OR source IS DISTINCT FROM 'hevy');

COMMIT;
