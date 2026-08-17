"""
tests/test_elevation_gain.py
Tests fuer training_plan.elevation_gain_m (2026-08-17): reines CAIRN-Feld,
niemals an Garmin gesendet. Deckt ab:
- upsert_planned_workout speichert elevation_gain_m korrekt
- get_planned_workouts gibt elevation_gain_m zurueck
- elevation_gain_m in HASH_FIELDS -> Aenderung markiert eine bereits
  gepushte Session als dirty
- der Garmin-Push-Pfad (workout_def) enthaelt elevation_gain_m nie

DB-Zugriffe laufen echt gegen die Dev-DB mit klar markierten Testzeilen
(external_id-Praefix 'test-elev-'), die per Fixture aufgeraeumt werden.
Garmin-Netzwerkaufrufe sind durchgehend gemockt.
"""
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import coach.garmin_batch as garmin_batch
from coach.garmin_batch import run_batch
from coach.mcp_server import get_planned_workouts, upsert_planned_workout, upsert_training_block
from coach.sync_utils import HASH_FIELDS, compute_content_hash
from database.connection import get_connection

TEST_PREFIX = "test-elev-"


@pytest.fixture
def db_conn():
    conn = get_connection()
    yield conn
    conn.close()


@pytest.fixture
def cleanup_rows(db_conn):
    yield
    cur = db_conn.cursor()
    cur.execute("DELETE FROM training_plan WHERE external_id LIKE %s", (f"{TEST_PREFIX}%",))
    cur.execute("DELETE FROM garmin_mcp_log WHERE external_id LIKE %s", (f"{TEST_PREFIX}%",))
    db_conn.commit()


# ── 1: upsert_planned_workout speichert elevation_gain_m ───────────────────

class TestUpsertPlannedWorkoutElevationGain:
    def test_elevation_gain_m_persisted(self, db_conn, cleanup_rows):
        ext_id = f"{TEST_PREFIX}upw-1"
        result = upsert_planned_workout(
            external_id=ext_id, date="2026-09-20", session_type="Long Run",
            sport="running", name="Long Trail", duration_min=150, distance_km=19,
            elevation_gain_m=1200,
        )
        assert result.get("error") is None

        cur = db_conn.cursor()
        cur.execute("SELECT elevation_gain_m FROM training_plan WHERE external_id = %s", (ext_id,))
        assert cur.fetchone()[0] == 1200

    def test_elevation_gain_m_optional_defaults_to_null(self, db_conn, cleanup_rows):
        ext_id = f"{TEST_PREFIX}upw-2"
        upsert_planned_workout(
            external_id=ext_id, date="2026-09-20", session_type="Easy Run", duration_min=40,
        )
        cur = db_conn.cursor()
        cur.execute("SELECT elevation_gain_m FROM training_plan WHERE external_id = %s", (ext_id,))
        assert cur.fetchone()[0] is None


# ── upsert_training_block: elevation_gain_m pro Session ────────────────────

class TestUpsertTrainingBlockElevationGain:
    def test_elevation_gain_m_persisted_per_session(self, db_conn, cleanup_rows):
        # Race-Metadaten identisch zum echten aktiven Plan mitgeben, damit
        # upsert_training_block dessen plans-Zeile nicht mit erfundenen
        # Testdaten ueberschreibt (nur die neue training_plan-Testzeile zaehlt).
        cur = db_conn.cursor()
        cur.execute(
            "SELECT race_name, race_date, race_distance_km, goal_type "
            "FROM plans WHERE status='active' ORDER BY created_at DESC LIMIT 1"
        )
        active = cur.fetchone()
        if not active:
            pytest.skip("Kein aktiver Plan in der DB — Test setzt einen aktiven Plan voraus")
        race_name, race_date, race_distance_km, goal_type = active

        ext_id = f"{TEST_PREFIX}block-1"
        plan = {
            "race": {"name": race_name, "race_date": str(race_date),
                     "race_distance_km": float(race_distance_km) if race_distance_km is not None else None,
                     "goal_type": goal_type},
            "sessions": [{
                "external_id": ext_id, "date": "2026-09-22", "session_type": "Hill Session",
                "distance_km": 12, "duration_min": 80, "elevation_gain_m": 850,
            }],
        }
        result = upsert_training_block(plan)
        assert "error" not in result

        cur.execute("SELECT elevation_gain_m FROM training_plan WHERE external_id = %s", (ext_id,))
        assert cur.fetchone()[0] == 850


# ── 2: get_planned_workouts gibt elevation_gain_m zurueck ──────────────────

class TestGetPlannedWorkoutsElevationGain:
    def test_returns_elevation_gain_m_field(self, db_conn, cleanup_rows):
        ext_id = f"{TEST_PREFIX}gpw-1"
        upsert_planned_workout(
            external_id=ext_id, date="2026-08-25", session_type="Hill Session",
            sport="running", duration_min=80, distance_km=12, elevation_gain_m=700,
        )
        workouts = get_planned_workouts(days=30)
        matching = [w for w in workouts if w.get("elevation_gain_m") == 700]
        assert matching, "elevation_gain_m muss im Rueckgabe-Dict der Session auftauchen"

    def test_null_elevation_gain_m_is_none_not_missing_key(self, db_conn, cleanup_rows):
        ext_id = f"{TEST_PREFIX}gpw-2"
        upsert_planned_workout(
            external_id=ext_id, date="2026-08-25", session_type="Easy Run", duration_min=40,
        )
        workouts = get_planned_workouts(days=30)
        session = next(w for w in workouts if w.get("id") is not None and _matches(db_conn, w["id"], ext_id))
        assert "elevation_gain_m" in session
        assert session["elevation_gain_m"] is None


def _matches(db_conn, tp_id, ext_id) -> bool:
    cur = db_conn.cursor()
    cur.execute("SELECT external_id FROM training_plan WHERE id = %s", (tp_id,))
    row = cur.fetchone()
    return row is not None and row[0] == ext_id


# ── 3: elevation_gain_m in HASH_FIELDS -> Aenderung macht Session dirty ────

class TestElevationGainInHashFields:
    def test_elevation_gain_m_is_a_hash_field(self):
        assert "elevation_gain_m" in HASH_FIELDS

    def test_changing_elevation_gain_m_changes_hash(self):
        base = {"date": "2026-09-20", "session_type": "Hill Session", "distance_km": 12,
                "duration_min": 80, "notes": None, "workout_steps": None, "session_zone": None,
                "name": "Hill Threshold", "target": None}
        hash_a = compute_content_hash({**base, "elevation_gain_m": 700})
        hash_b = compute_content_hash({**base, "elevation_gain_m": 900})
        assert hash_a != hash_b

    def test_changed_elevation_gain_m_marks_synced_session_dirty(self, db_conn, cleanup_rows):
        ext_id = f"{TEST_PREFIX}dirty-1"
        result = upsert_planned_workout(
            external_id=ext_id, date="2026-09-20", session_type="Hill Session",
            sport="running", duration_min=80, distance_km=12, elevation_gain_m=700,
        )
        row_id = result["id"]

        # Session als bereits erfolgreich gepusht simulieren (garmin_workout_id +
        # content_hash gesetzt) -- genau der Zustand, den upsert_planned_workout
        # vor einer Aenderung prueft, um dirty-marking auszuloesen.
        cur = db_conn.cursor()
        cur.execute("SELECT content_hash FROM training_plan WHERE id = %s", (row_id,))
        synced_hash = cur.fetchone()[0]
        if synced_hash is None:
            synced_hash = compute_content_hash({
                "date": "2026-09-20", "session_type": "Hill Session", "distance_km": 12,
                "duration_min": 80, "notes": None, "workout_steps": None, "session_zone": None,
                "name": None, "target": None, "elevation_gain_m": 700,
            })
        cur.execute(
            "UPDATE training_plan SET garmin_workout_id = '123456', sync_status = 'synced', "
            "content_hash = %s WHERE id = %s",
            (synced_hash, row_id),
        )
        db_conn.commit()

        # Nur elevation_gain_m aendern -- alles andere bleibt gleich.
        upsert_planned_workout(
            external_id=ext_id, date="2026-09-20", session_type="Hill Session",
            sport="running", duration_min=80, distance_km=12, elevation_gain_m=900,
        )

        cur.execute("SELECT sync_status, elevation_gain_m FROM training_plan WHERE id = %s", (row_id,))
        sync_status, elevation_gain_m = cur.fetchone()
        assert elevation_gain_m == 900
        assert sync_status == "dirty", "Eine geaenderte Hoehenmeter-Angabe muss die Session als dirty markieren"


# ── 4: Garmin-Push-Pfad enthaelt elevation_gain_m nicht ────────────────────

class TestElevationGainNeverReachesGarmin:
    def test_workout_def_never_contains_elevation_gain_m(self, db_conn, cleanup_rows, monkeypatch):
        ext_id = f"{TEST_PREFIX}nogarmin-1"
        tp_id = upsert_planned_workout(
            external_id=ext_id, date="2026-10-05", session_type="Easy Run",
            sport="running", duration_min=45, distance_km=7, elevation_gain_m=300,
        )["id"]

        captured_workout_defs = []

        def fake_push_workout(workout_def, date_str, sport="running"):
            captured_workout_defs.append(workout_def)
            return {"garmin_workout_id": 111111, "garmin_schedule_id": 222222,
                    "workout_name": workout_def["name"], "scheduled_date": date_str}

        monkeypatch.setattr(garmin_batch, "garmin_client", lambda: MagicMock())
        monkeypatch.setattr(garmin_batch, "push_workout", fake_push_workout)

        run_batch(session_ids=[tp_id])

        assert captured_workout_defs, "push_workout haette aufgerufen werden muessen"
        for wd in captured_workout_defs:
            assert "elevation_gain_m" not in wd
            for step in wd.get("steps", []):
                assert "elevation_gain_m" not in step

    def test_session_dict_carries_elevation_gain_m_but_workout_def_does_not(self, db_conn, cleanup_rows):
        # _build_workout_steps() erhaelt das volle Session-Dict (inkl.
        # elevation_gain_m aus der erweiterten SELECT-Liste), darf es aber
        # nie in einen Step uebernehmen.
        from coach.garmin_batch import _build_workout_steps
        session = {"workout_steps": None, "distance_km": 10, "duration_min": 60,
                   "sport": "running", "elevation_gain_m": 500}
        steps = _build_workout_steps(session)
        for step in steps:
            assert "elevation_gain_m" not in step
