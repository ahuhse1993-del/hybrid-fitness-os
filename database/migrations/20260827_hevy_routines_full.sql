-- Vollständige Hevy-Routine-Struktur (bisher nur title+exercise-Namen in
-- cairn_routines, siehe data/hevy_routines_sync.py). Additiv, rueckwaerts-
-- kompatibel -- cairn_routines und die bestehenden Hevy-Tools/-Skripte
-- bleiben unveraendert bestehen und werden von diesen Tabellen nicht gelesen.

-- Eine Zeile pro Hevy-Routine (id = Hevy routine.id, ein UUID-String).
CREATE TABLE IF NOT EXISTS hevy_routines (
    id SERIAL PRIMARY KEY,
    hevy_routine_id TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    folder_id TEXT,
    notes TEXT,
    status TEXT NOT NULL DEFAULT 'active',  -- active | archived
    hevy_updated_at TIMESTAMPTZ,
    last_synced_at TIMESTAMPTZ,
    raw_json JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Übungen pro Routine (Reihenfolge + Template-ID). exercise_template_id ist
-- KEIN UUID -- Hevy liefert dafuer kurze alphanumerische Codes (z.B. "EAC7D9C5").
CREATE TABLE IF NOT EXISTS hevy_routine_exercises (
    id SERIAL PRIMARY KEY,
    routine_id INTEGER NOT NULL REFERENCES hevy_routines(id) ON DELETE CASCADE,
    exercise_template_id TEXT NOT NULL,
    exercise_name TEXT,
    order_index INTEGER NOT NULL,
    superset_id INTEGER,
    rest_seconds INTEGER,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Satz-Vorgaben pro Übung.
CREATE TABLE IF NOT EXISTS hevy_routine_sets (
    id SERIAL PRIMARY KEY,
    routine_exercise_id INTEGER NOT NULL REFERENCES hevy_routine_exercises(id) ON DELETE CASCADE,
    set_index INTEGER NOT NULL,
    set_type TEXT DEFAULT 'normal',  -- normal | warmup | failure | dropset
    weight_kg NUMERIC(6,2),
    reps INTEGER,
    duration_seconds INTEGER,
    distance_meters NUMERIC(8,2),
    rpe NUMERIC(3,1),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Lokaler Cache der Hevy Exercise Templates (id = Hevy exercise_templates.id,
-- z.B. "EAC7D9C5" -- ebenfalls kein UUID).
CREATE TABLE IF NOT EXISTS hevy_exercise_templates (
    id SERIAL PRIMARY KEY,
    exercise_template_id TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    exercise_type TEXT,
    primary_muscle_group TEXT,
    secondary_muscle_groups JSONB,
    equipment TEXT,
    is_custom BOOLEAN DEFAULT FALSE,
    last_synced_at TIMESTAMPTZ,
    raw_json JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_hevy_routines_status ON hevy_routines(status);
CREATE INDEX IF NOT EXISTS idx_hevy_routine_exercises_routine ON hevy_routine_exercises(routine_id);
CREATE INDEX IF NOT EXISTS idx_hevy_exercise_templates_title ON hevy_exercise_templates(title);
