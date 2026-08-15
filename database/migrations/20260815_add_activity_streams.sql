-- Migration: 20260815_add_activity_streams
-- Erweitert trainings um Garmin-Kennzahlen (Elevation, HF, Trainingsbelastung,
-- Effekte, VO2max, Power) und legt activity_streams für downgesampelte
-- Zeitreihen-Daten pro Aktivität an.

BEGIN;

ALTER TABLE trainings
    ADD COLUMN IF NOT EXISTS elevation_gain_m    FLOAT,
    ADD COLUMN IF NOT EXISTS elevation_loss_m    FLOAT,
    ADD COLUMN IF NOT EXISTS max_hr              INTEGER,
    ADD COLUMN IF NOT EXISTS avg_cadence         INTEGER,
    ADD COLUMN IF NOT EXISTS training_load       FLOAT,
    ADD COLUMN IF NOT EXISTS aerobic_effect      FLOAT,
    ADD COLUMN IF NOT EXISTS anaerobic_effect    FLOAT,
    ADD COLUMN IF NOT EXISTS vo2max_estimate     FLOAT,
    ADD COLUMN IF NOT EXISTS avg_power           INTEGER;

CREATE TABLE IF NOT EXISTS activity_streams (
    id              SERIAL PRIMARY KEY,
    training_id     INTEGER NOT NULL REFERENCES trainings(id) ON DELETE CASCADE,
    point_index     INTEGER NOT NULL,
    timestamp_ms    BIGINT,
    elapsed_s       INTEGER,
    heart_rate      SMALLINT,
    elevation_m     FLOAT,
    speed_ms        FLOAT,
    cadence         SMALLINT,
    power_w         SMALLINT,
    lat             DOUBLE PRECISION,
    lon             DOUBLE PRECISION,
    UNIQUE (training_id, point_index)
);

CREATE INDEX IF NOT EXISTS activity_streams_training_id_idx
    ON activity_streams (training_id);

COMMIT;
