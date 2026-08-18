-- activity_stream.moving_s — kumulierte Bewegungszeit (Garmin sumMovingDuration)
-- getrennt von elapsed_s (kumulierte Gesamtzeit inkl. Pausen/Auto-Pause).
-- Ermoeglicht Pausenerkennung: elapsed_s - moving_s waechst waehrend einer Pause,
-- bleibt konstant waehrend Bewegung. Additiv, rueckwaertskompatibel.

ALTER TABLE activity_stream ADD COLUMN IF NOT EXISTS moving_s FLOAT;
