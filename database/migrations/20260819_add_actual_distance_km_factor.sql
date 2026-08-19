-- Planned vs. Actual Distance Tracking
-- ----------------------------------------
-- km_factor:               Umrechnungsschlüssel für Cross-Einheiten (z.B. 0.25 für Rennrad /4).
--                          NULL = kein Faktor (Laufen, Trail, Wandern), 1.0 wäre äquivalent zu NULL.
-- actual_distance_km:      Tatsächlich absolvierte km (roh aus Garmin, kein Faktor angewendet).
--                          Bei Rennrad: echte Rennrad-km (z.B. 80 km, nicht 20).
-- linked_garmin_activity_id: Garmin Activity-ID der abgeschlossenen Einheit.
--                          Wird vom Sync-Job automatisch gesetzt (über garmin_workout_id-Match)
--                          oder manuell via link_activity_to_session (Backdoor).

ALTER TABLE training_plan
    ADD COLUMN IF NOT EXISTS km_factor              NUMERIC(5, 4),
    ADD COLUMN IF NOT EXISTS actual_distance_km     NUMERIC(8, 3),
    ADD COLUMN IF NOT EXISTS linked_garmin_activity_id BIGINT;

-- Index für den Sync-Job (sucht training_plan über garmin_workout_id)
CREATE INDEX IF NOT EXISTS idx_training_plan_garmin_workout_id
    ON training_plan (garmin_workout_id)
    WHERE garmin_workout_id IS NOT NULL;

-- Index für Backdoor-Lookups über linked_garmin_activity_id
CREATE INDEX IF NOT EXISTS idx_training_plan_linked_activity
    ON training_plan (linked_garmin_activity_id)
    WHERE linked_garmin_activity_id IS NOT NULL;
