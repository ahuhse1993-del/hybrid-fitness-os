ALTER TABLE athlete_profile ADD COLUMN IF NOT EXISTS cycling_ftp_w INTEGER;
ALTER TABLE athlete_profile ADD COLUMN IF NOT EXISTS cycling_ftp_wkg NUMERIC(4,2);
ALTER TABLE athlete_profile ADD COLUMN IF NOT EXISTS cycling_ftp_tested_at DATE;
ALTER TABLE athlete_profile ADD COLUMN IF NOT EXISTS cycling_ftp_test_type TEXT;
