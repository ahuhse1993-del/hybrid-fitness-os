-- Manuell gepflegter VO2max-Wert auf athlete_profile (Garmin-Aktivitaeten
-- fuellen trainings.vo2max_estimate aktuell nicht -- siehe coach/api.py::frontend_profile,
-- das den aktuelleren Wert aus trainings bevorzugt und hierauf nur zurueckfaellt).
ALTER TABLE athlete_profile ADD COLUMN IF NOT EXISTS vo2max NUMERIC(4, 1);
