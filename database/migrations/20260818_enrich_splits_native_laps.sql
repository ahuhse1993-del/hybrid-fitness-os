-- splits = Garmin's native lapDTOs (client.get_activity_splits), bereits die
-- Quelle fuer "native_laps". Erweitert um Felder, die Garmin liefert, aber
-- data/garmin_import_splits.py bisher nicht mappte -- u.a. elevation_loss_m
-- (Ursache fuer den bekannten "519m hoch, 0m runter"-Fehler: Garmin liefert
-- elevationLoss durchaus, die alte _insert_splits() las es nur nie aus).
-- Additiv, rueckwaertskompatibel -- bestehende Spalten/Werte unberuehrt.

ALTER TABLE splits ADD COLUMN IF NOT EXISTS start_time TIMESTAMPTZ;
ALTER TABLE splits ADD COLUMN IF NOT EXISTS moving_duration_s INTEGER;
ALTER TABLE splits ADD COLUMN IF NOT EXISTS max_hr INTEGER;
ALTER TABLE splits ADD COLUMN IF NOT EXISTS avg_power INTEGER;
ALTER TABLE splits ADD COLUMN IF NOT EXISTS elevation_loss_m NUMERIC(6, 2);
ALTER TABLE splits ADD COLUMN IF NOT EXISTS lap_type TEXT;
