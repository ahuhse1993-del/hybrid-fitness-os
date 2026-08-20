-- One-time cleanup: alle Sessions und plan_weeks löschen, Pläne archivieren
-- Wird einmalig beim nächsten Deploy ausgeführt
DELETE FROM training_plan;
DELETE FROM plan_weeks;
UPDATE plans SET status = 'archived' WHERE status = 'active';
