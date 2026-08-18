-- CAIRN Activity Stream — unified per-second data aligned by distance
-- Ersetzt die getrennte gps_tracks / hr_tracks Struktur für Charts
-- Idempotent — kann mehrfach ausgeführt werden

CREATE TABLE IF NOT EXISTS activity_stream (
    id              SERIAL PRIMARY KEY,
    training_id     INTEGER NOT NULL REFERENCES trainings(id) ON DELETE CASCADE,
    distance_m      FLOAT   NOT NULL,       -- kumulierte Distanz in Metern (gemeinsamer Schlüssel!)
    elapsed_s       FLOAT,                  -- kumulierte Zeit in Sekunden
    heart_rate      INTEGER,                -- Herzfrequenz (bpm)
    speed_ms        FLOAT,                  -- Geschwindigkeit (m/s)
    cadence         INTEGER,                -- Kadenz (spm für Laufen, rpm für Rad)
    power           INTEGER,                -- Leistung (Watt, nur Rad/Running-Power-Meter)
    elevation_m     FLOAT,                  -- Höhe üNN (Meter)
    lat             FLOAT,                  -- Breitengrad
    lon             FLOAT                   -- Längengrad
);

CREATE INDEX IF NOT EXISTS activity_stream_training_idx
    ON activity_stream (training_id, distance_m);
