-- Rad-HR-Zonen und Power-Zonen fuer athlete_profile.
-- hr_zones (Running, flach z1..z5) existiert bereits seit
-- 20260818_athlete_profile_enrichment.sql und wird bewusst NICHT angefasst --
-- coach/mcp_server.py::_validate_hr_zones erwartet exakt dieses flache Format,
-- jede Struktur-Aenderung wuerde zukuenftige update_athlete_profile-Aufrufe
-- fuer hr_zones brechen. Rad-Zonen leben deshalb in einer eigenen Spalte.
ALTER TABLE athlete_profile ADD COLUMN IF NOT EXISTS hr_zones_cycling JSONB;
ALTER TABLE athlete_profile ADD COLUMN IF NOT EXISTS power_zones JSONB;
