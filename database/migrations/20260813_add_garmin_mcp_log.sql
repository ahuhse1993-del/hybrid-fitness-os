-- Migration: 20260813_add_garmin_mcp_log
-- Idempotenz-Log für den CAIRN Garmin MCP Push Service.
-- Zweck: verhindert Doppel-Uploads, trackt jeden Garmin-Write auditierbar,
--        speichert niemals Credentials – nur Workout-IDs und Aktionstypen.

BEGIN;

CREATE TABLE IF NOT EXISTS garmin_mcp_log (
    id                  SERIAL PRIMARY KEY,

    -- Idempotenz-Anker: vom MCP-Caller gesetzt, z.B. plan_session_id oder SHA256(workout_def)
    external_id         TEXT        NOT NULL,

    -- Garmin-Rückgaben (NULL bis zur jeweiligen Aktion)
    garmin_workout_id   BIGINT,
    garmin_schedule_id  BIGINT,

    -- Workout-Metadaten (denormalisiert für Log-Lesbarkeit)
    workout_name        TEXT,
    scheduled_date      DATE,

    -- Aktionstyp: create | schedule | push | preview | delete | move
    action              TEXT        NOT NULL,

    -- Ergebnis: success | failed | preview_only
    status              TEXT        NOT NULL DEFAULT 'success',

    -- Fehlertext: nur Exception-Klasse + Message, niemals Credentials
    last_error          TEXT,

    -- Zeitstempel
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Flexibles JSONB: MCP-Tool-Name, ChatGPT-Request-ID, etc.
    metadata            JSONB,

    UNIQUE (external_id)
);

CREATE INDEX IF NOT EXISTS garmin_mcp_log_scheduled_date_idx
    ON garmin_mcp_log (scheduled_date);

CREATE INDEX IF NOT EXISTS garmin_mcp_log_workout_id_idx
    ON garmin_mcp_log (garmin_workout_id);

CREATE INDEX IF NOT EXISTS garmin_mcp_log_created_at_idx
    ON garmin_mcp_log (created_at DESC);

-- Trigger: updated_at automatisch aktualisieren
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS garmin_mcp_log_updated_at ON garmin_mcp_log;
CREATE TRIGGER garmin_mcp_log_updated_at
    BEFORE UPDATE ON garmin_mcp_log
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

COMMIT;
