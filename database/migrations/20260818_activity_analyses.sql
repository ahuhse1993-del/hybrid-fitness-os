-- Dauerhaft gespeicherte, versionierte Coach-Analysen (ChatGPT-Interpretation).
-- Objektive Aktivitaetswerte (trainings/splits/activity_stream) werden hier
-- NICHT dupliziert -- nur die subjektive Interpretation + ein Hash der
-- zugrunde liegenden Quelldaten, um Veraltung (stale) zu erkennen.
-- Additiv, neue Tabelle, keine bestehenden Tabellen veraendert.

CREATE TABLE IF NOT EXISTS activity_analyses (
    id                       SERIAL PRIMARY KEY,
    training_id              INTEGER NOT NULL REFERENCES trainings(id) ON DELETE CASCADE,
    analysis_schema_version  INTEGER NOT NULL DEFAULT 1,
    version                  INTEGER NOT NULL DEFAULT 1,
    source_data_hash         TEXT NOT NULL,
    status                   TEXT NOT NULL DEFAULT 'fresh',
    verdict                  TEXT,
    goal_achievement         TEXT,
    summary                  TEXT,
    positive_findings_json   JSONB,
    limitations_json         JSONB,
    recovery_context         TEXT,
    coach_recommendation     TEXT,
    data_quality_note        TEXT,
    generated_by             TEXT,
    generated_at             TIMESTAMPTZ,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS activity_analyses_training_idx ON activity_analyses (training_id, version DESC);
