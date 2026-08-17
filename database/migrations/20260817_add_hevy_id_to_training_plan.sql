-- Migration: 20260817_add_hevy_id_to_training_plan
-- sync_hevy_completions() verknuepft eine geplante Session mit ihrem
-- absolvierten Hevy-Workout ueber training_plan.hevy_id = trainings.hevy_id
-- (statt ueber das bisherige matched_training_id, das nie befuellt wurde,
-- weil die Spalte hevy_id auf training_plan schlicht fehlte).

BEGIN;

ALTER TABLE training_plan ADD COLUMN IF NOT EXISTS hevy_id TEXT;

COMMIT;
