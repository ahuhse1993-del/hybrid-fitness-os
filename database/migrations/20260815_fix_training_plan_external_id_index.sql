-- Migration: 20260815_fix_training_plan_external_id_index
-- BUGFIX: der bisherige partielle Unique-Index (WHERE external_id IS NOT NULL,
-- aus 20260815_add_training_plan_external_id.sql) wird von Postgres NICHT durch
-- ein einfaches "ON CONFLICT (external_id)" inferiert — das schlaegt live mit
-- "InvalidColumnReference: there is no unique or exclusion constraint matching
-- the ON CONFLICT specification" fehl (in upsert_planned_workout entdeckt;
-- betrifft auch upsert_training_block, dessen INSERT bislang nie real gegen
-- eine echte Zeile getestet wurde).
--
-- Der partielle Index war ohnehin unnoetig: ein normaler UNIQUE-Constraint in
-- Postgres behandelt mehrere NULL-Werte bereits als nicht-konfliktend, alte
-- Zeilen ohne external_id bleiben also auch ohne WHERE-Klausel unproblematisch.

BEGIN;

DROP INDEX IF EXISTS training_plan_external_id_key;

ALTER TABLE training_plan
    DROP CONSTRAINT IF EXISTS training_plan_external_id_key;
ALTER TABLE training_plan
    ADD CONSTRAINT training_plan_external_id_key UNIQUE (external_id);

COMMIT;
