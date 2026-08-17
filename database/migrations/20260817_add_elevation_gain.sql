-- Migration: 20260817_add_elevation_gain
-- elevation_gain_m fuer geplante Sessions (training_plan) — CAIRN-intern,
-- kein Garmin-Feld. Bereits durch data/migrate_plan_v4.py angelegt; hier als
-- IF NOT EXISTS dokumentiert/idempotent nachvollzogen.

BEGIN;

ALTER TABLE training_plan ADD COLUMN IF NOT EXISTS elevation_gain_m INTEGER;

COMMIT;
