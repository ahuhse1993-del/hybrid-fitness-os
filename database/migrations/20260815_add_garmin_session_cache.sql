-- Migration: 20260815_add_garmin_session_cache
-- Cached Garmin-Session-Token (aus Garmin().dumps() — DI-Token/Refresh-Token,
-- NIEMALS Email/Passwort), damit garmin_client() nicht bei jedem Aufruf neu
-- einloggen muss. Spart den teuren Login-Call und reduziert Rate-Limiting.

BEGIN;

CREATE TABLE IF NOT EXISTS garmin_session_cache (
    id SERIAL PRIMARY KEY,
    token TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMIT;
