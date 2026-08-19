-- Add phase column to training_plan if not exists
ALTER TABLE training_plan
    ADD COLUMN IF NOT EXISTS phase VARCHAR(20);
