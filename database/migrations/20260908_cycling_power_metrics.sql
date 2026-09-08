-- avg_power_w bewusst NICHT hinzugefuegt: trainings.avg_power existiert
-- bereits (INTEGER, siehe coach/activity_data.py::build_summary) und wird
-- von get_activity_analysis_data befuellt. Ein zweites avg_power_w waere
-- ein doppelter, nie befuellter Datensatz.
ALTER TABLE trainings ADD COLUMN IF NOT EXISTS normalized_power_w INTEGER;
ALTER TABLE trainings ADD COLUMN IF NOT EXISTS max_power_w INTEGER;
ALTER TABLE trainings ADD COLUMN IF NOT EXISTS tss_estimate NUMERIC(6,1);
ALTER TABLE trainings ADD COLUMN IF NOT EXISTS intensity_factor NUMERIC(4,3);
