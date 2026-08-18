-- CAIRN Gear-System: normalisierte Schuh-/Gear-Datensaetze + Nutzungsprotokoll.
-- Ersetzt NICHT athlete_profile.shoes (jsonb) -- das bleibt fuer bestehende
-- Leser unveraendert bestehen, wird aber von den neuen Gear-Tools nicht mehr
-- beschrieben. Additiv, rueckwaertskompatibel, keine bestehenden Tabellen
-- veraendert.

CREATE TABLE IF NOT EXISTS athlete_gear (
    id                   SERIAL PRIMARY KEY,
    athlete_id           INTEGER REFERENCES athlete_profile(id),
    gear_type            TEXT NOT NULL CHECK (gear_type IN (
                             'running_shoe', 'trail_shoe', 'hiking_shoe', 'bicycle',
                             'watch', 'heart_rate_sensor', 'vest', 'poles', 'other'
                         )),
    brand                TEXT,
    model                TEXT,
    nickname             TEXT,
    size                 TEXT,
    color                TEXT,
    primary_surface      TEXT,
    purchase_date        DATE,
    first_use_date       DATE,
    retired_date         DATE,
    active               BOOLEAN NOT NULL DEFAULT true,
    initial_distance_km  NUMERIC(8, 2) NOT NULL DEFAULT 0,
    target_distance_km   NUMERIC(8, 2),
    notes                TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Shoe-Typen, fuer die "max. ein Schuh pro Aktivitaet" gilt (application-level
-- in coach/mcp_server.py::assign_activity_gear durchgesetzt -- ein Schuhwechsel
-- entfernt automatisch die vorige Schuh-Zuordnung derselben Aktivitaet statt
-- einen zweiten Schuh gleichzeitig zuzulassen; andere Gear-Typen sind davon
-- nicht betroffen und duerfen beliebig zusaetzlich zugeordnet werden).

CREATE TABLE IF NOT EXISTS activity_gear_usage (
    id                 SERIAL PRIMARY KEY,
    training_id        INTEGER NOT NULL REFERENCES trainings(id) ON DELETE CASCADE,
    gear_id            INTEGER NOT NULL REFERENCES athlete_gear(id) ON DELETE CASCADE,
    distance_km        NUMERIC(6, 2) NOT NULL,
    assignment_source  TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (training_id, gear_id)
);

CREATE INDEX IF NOT EXISTS activity_gear_usage_gear_idx ON activity_gear_usage (gear_id);
