CREATE TABLE IF NOT EXISTS strava_tokens (
    id            INTEGER PRIMARY KEY DEFAULT 1,
    access_token  TEXT NOT NULL,
    refresh_token TEXT NOT NULL,
    expires_at    BIGINT NOT NULL,
    athlete_id    BIGINT,
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);

-- avg_power_w bewusst NICHT hinzugefuegt: trainings.avg_power existiert
-- bereits und wird von Garmin-Sync (data/garmin_import_history.py,
-- data/garmin_sync_daily.py) befuellt -- Strava-Sync schreibt ab jetzt in
-- dieselbe Spalte, damit coach/activity_data.py::build_summary beide
-- Quellen einheitlich liest, statt eine zweite, nie gelesene Spalte
-- anzulegen.
-- normalized_power_w / max_power_w / tss_estimate / strava_id existieren
-- teils schon aus vorherigen Migrationen -- IF NOT EXISTS macht das hier
-- ungefaehrlich und die Datei bleibt fuer eine frische DB vollstaendig.
ALTER TABLE trainings ADD COLUMN IF NOT EXISTS normalized_power_w INTEGER;
ALTER TABLE trainings ADD COLUMN IF NOT EXISTS max_power_w INTEGER;
ALTER TABLE trainings ADD COLUMN IF NOT EXISTS tss_estimate NUMERIC(6,1);
ALTER TABLE trainings ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'garmin';
ALTER TABLE trainings ADD COLUMN IF NOT EXISTS strava_id TEXT;

-- Kein CREATE UNIQUE INDEX auf strava_id: die Spalte hat bereits die
-- UNIQUE-CONSTRAINT trainings_strava_id_key (siehe \d trainings), ein
-- zweiter funktional identischer Index waere nur totes Gewicht.
