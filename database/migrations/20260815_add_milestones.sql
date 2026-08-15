-- Migration: 20260815_add_milestones
-- Milestone-Tabelle für den CAIRN Trainingsplan.
-- Inhalte werden vom Coach (ChatGPT via MCP) befüllt und aktualisiert.
-- Status-Logik: open → achieved | changed
-- ALS NÄCHSTES = erster offener Milestone in step_number-Reihenfolge

BEGIN;

CREATE TABLE IF NOT EXISTS milestones (
    id              SERIAL PRIMARY KEY,

    -- Zuordnung zum Plan
    plan_id         INTEGER REFERENCES plans(id) ON DELETE CASCADE,

    -- Reihenfolge innerhalb des Plans
    step_number     INTEGER NOT NULL,

    -- Anzeige
    title           VARCHAR(200)    NOT NULL,
    criterion       TEXT,           -- z.B. "20 km · 700-800 HM"
    target_date     DATE,           -- optionales Zieldatum

    -- Status: open | achieved | changed
    status          VARCHAR(20)     NOT NULL DEFAULT 'open',

    -- Wie der Status verifiziert wird (z.B. "Automatisch aus Aktivität", "Coach/Check-in bestätigt")
    evidence        TEXT,

    -- Freitext vom Coach
    notes           TEXT,

    -- Wann der Milestone erreicht wurde
    achieved_at     DATE,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS milestones_plan_id_idx
    ON milestones (plan_id, step_number);

-- updated_at Trigger (nutzt die bereits vorhandene set_updated_at Funktion)
DROP TRIGGER IF EXISTS milestones_updated_at ON milestones;
CREATE TRIGGER milestones_updated_at
    BEFORE UPDATE ON milestones
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

COMMIT;
