-- Athlete-Profile-Erweiterung fuer update_athlete_profile / get_athlete_profile.
-- Rein additiv: alle bestehenden Spalten (hr_z1_min..hr_z5_max, pace_z1..pace_z5,
-- shoes, long_term_goals, ...) bleiben unveraendert und werden von keinem
-- bestehenden Aufrufer entfernt. Neue, reichere Strukturen (hr_zones jsonb,
-- pace_zones jsonb, long_term_goals_json jsonb) treten NEBEN die alten Felder,
-- nicht an ihre Stelle -- Rueckwaertskompatibilitaet.

ALTER TABLE athlete_profile ADD COLUMN IF NOT EXISTS height_cm NUMERIC(5, 1);
ALTER TABLE athlete_profile ADD COLUMN IF NOT EXISTS resting_hr INTEGER;
ALTER TABLE athlete_profile ADD COLUMN IF NOT EXISTS max_hr INTEGER;
ALTER TABLE athlete_profile ADD COLUMN IF NOT EXISTS lactate_threshold_hr INTEGER;
ALTER TABLE athlete_profile ADD COLUMN IF NOT EXISTS hr_zone_method TEXT;
ALTER TABLE athlete_profile ADD COLUMN IF NOT EXISTS hr_zones JSONB;
ALTER TABLE athlete_profile ADD COLUMN IF NOT EXISTS pace_zones JSONB;
ALTER TABLE athlete_profile ADD COLUMN IF NOT EXISTS preferred_surfaces JSONB;
ALTER TABLE athlete_profile ADD COLUMN IF NOT EXISTS preferred_sports JSONB;
ALTER TABLE athlete_profile ADD COLUMN IF NOT EXISTS training_preferences JSONB;
ALTER TABLE athlete_profile ADD COLUMN IF NOT EXISTS injury_notes JSONB;
ALTER TABLE athlete_profile ADD COLUMN IF NOT EXISTS long_term_goals_json JSONB;

-- Audit-Log fuer update_athlete_profile — nachvollziehbare Aenderungshistorie,
-- keine sensiblen Rohwerte ausserhalb der DB geloggt (kein print/logger.info
-- mit Profildaten im Anwendungscode, siehe coach/mcp_server.py).
CREATE TABLE IF NOT EXISTS athlete_profile_audit_log (
    id              SERIAL PRIMARY KEY,
    athlete_profile_id INTEGER REFERENCES athlete_profile(id),
    changed_fields  JSONB NOT NULL,
    old_values      JSONB,
    new_values      JSONB,
    source          TEXT,
    changed_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
