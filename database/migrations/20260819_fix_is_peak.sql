-- Fix: add is_peak column to plan_weeks (missed in initial migration)
ALTER TABLE plan_weeks
    ADD COLUMN IF NOT EXISTS is_peak BOOLEAN NOT NULL DEFAULT FALSE;
