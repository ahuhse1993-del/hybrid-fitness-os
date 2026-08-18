"""
tests/test_hevy_mcp_tools.py
Tests fuer die neuen Hevy-MCP-Tools (2026-08-17):
- get_hevy_workouts: gruppierte Workouts inkl. Exercises
- get_hevy_routines: cairn_routines-Ausgabe
- get_planned_workouts: neuer start_date-Parameter
- sync_hevy_completions: neuer Match-Algorithmus ueber training_plan.hevy_id
  (Datum + session_type statt des fruehereren matched_training_id-Ansatzes)

DB-Zugriffe laufen echt gegen die Dev-DB mit klar markierten Testzeilen
(externe IDs/hevy_id-Praefix 'test-hevy-'), die per Fixture aufgeraeumt werden.
Kein echter Hevy-/Garmin-API-Call in diesem File.
"""
import datetime
import os
import sys

import pytest
from psycopg2.extras import Json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from coach.mcp_server import (
    get_hevy_routines,
    get_hevy_workouts,
    get_planned_workouts,
    sync_hevy_completions,
)
from database.connection import get_connection

TEST_PREFIX = "test-hevy-"

# sync_hevy_completions() waehlt bei mehreren planned Strength/Core/Mobility-
# Sessions am selben Datum deterministisch die AELTESTE Zeile (ORDER BY id).
# date.today()-basierte Testdaten koennen daher eine ECHTE Produktionszeile
# treffen, sobald die Systemuhr auf ein Datum faellt, das mit einer echten
# geplanten Session zusammenfaellt (bereits zweimal live passiert und manuell
# repariert). Ein Datum weit in der Zukunft schliesst jede Kollision sicher aus.
_SAFE_TEST_DATE = datetime.date.today() + datetime.timedelta(days=3650)


@pytest.fixture
def db_conn():
    conn = get_connection()
    yield conn
    conn.close()


@pytest.fixture
def cleanup_rows(db_conn):
    yield
    db_conn.rollback()  # falls der Test selbst mit einer offenen Fehler-Transaktion endete
    cur = db_conn.cursor()
    cur.execute("DELETE FROM training_plan WHERE external_id LIKE %s", (f"{TEST_PREFIX}%",))
    cur.execute("DELETE FROM trainings WHERE hevy_id LIKE %s", (f"{TEST_PREFIX}%",))
    cur.execute("DELETE FROM cairn_routines WHERE title LIKE %s", (f"{TEST_PREFIX}%",))
    db_conn.commit()


def _insert_hevy_training(db_conn, hevy_id: str, session_date, title: str,
                           duration_minutes: int = 45) -> int:
    cur = db_conn.cursor()
    cur.execute(
        """INSERT INTO trainings (date, type, duration_minutes, distance_km, notes, hevy_id)
           VALUES (%s, 'WeightTraining', %s, 0, %s, %s) RETURNING id""",
        (session_date, duration_minutes, title, hevy_id),
    )
    tid = cur.fetchone()[0]
    db_conn.commit()
    return tid


def _insert_hevy_exercise(db_conn, training_id: int, index: int, name: str,
                           sets: int = 3, reps: str = "10, 10, 10", weight: str = "20, 20, 20"):
    cur = db_conn.cursor()
    cur.execute(
        """INSERT INTO hevy_exercises (training_id, exercise_index, exercise_name, sets, reps_per_set, weight_kg_per_set)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (training_id, index, name, sets, reps, weight),
    )
    db_conn.commit()


def _insert_planned_session(db_conn, external_id: str, session_date, session_type: str = "Strength Training"):
    cur = db_conn.cursor()
    dow = session_date.isoweekday()
    week_date = session_date - datetime.timedelta(days=dow - 1)
    cur.execute(
        """INSERT INTO training_plan (external_id, week_date, day_of_week, session_type, status)
           VALUES (%s, %s, %s, %s, 'planned') RETURNING id""",
        (external_id, week_date, dow, session_type),
    )
    tp_id = cur.fetchone()[0]
    db_conn.commit()
    return tp_id


# ── 1: get_hevy_workouts gibt korrekt gruppierte Workouts zurueck ──────────

class TestGetHevyWorkouts:
    def test_groups_exercises_under_their_workout(self, db_conn, cleanup_rows):
        hevy_id = f"{TEST_PREFIX}w1"
        session_date = datetime.date.today() - datetime.timedelta(days=5)
        tid = _insert_hevy_training(db_conn, hevy_id, session_date, "Upper Body CAIRN", duration_minutes=52)
        _insert_hevy_exercise(db_conn, tid, 1, "Bench Press")
        _insert_hevy_exercise(db_conn, tid, 2, "Pull Up")

        workouts = get_hevy_workouts(days=30)
        match = next(w for w in workouts if w["hevy_id"] == hevy_id)

        assert match["title"] == "Upper Body CAIRN"
        assert match["duration_minutes"] == 52
        assert match["date"] == session_date.isoformat()
        assert [e["exercise_name"] for e in match["exercises"]] == ["Bench Press", "Pull Up"]

    def test_workout_without_exercises_returns_empty_list(self, db_conn, cleanup_rows):
        hevy_id = f"{TEST_PREFIX}w2"
        session_date = datetime.date.today() - datetime.timedelta(days=3)
        _insert_hevy_training(db_conn, hevy_id, session_date, "Empty Session")

        workouts = get_hevy_workouts(days=30)
        match = next(w for w in workouts if w["hevy_id"] == hevy_id)
        assert match["exercises"] == []

    def test_respects_days_window(self, db_conn, cleanup_rows):
        hevy_id = f"{TEST_PREFIX}w3"
        old_date = datetime.date.today() - datetime.timedelta(days=90)
        _insert_hevy_training(db_conn, hevy_id, old_date, "Too Old")

        workouts = get_hevy_workouts(days=30)
        assert not any(w["hevy_id"] == hevy_id for w in workouts)


# ── 2: get_hevy_routines gibt Routinen aus cairn_routines zurueck ──────────

class TestGetHevyRoutines:
    def test_returns_routine_with_exercises(self, db_conn, cleanup_rows):
        title = f"{TEST_PREFIX}Routine A"
        cur = db_conn.cursor()
        cur.execute(
            "INSERT INTO cairn_routines (title, exercises) VALUES (%s, %s)",
            (title, Json(["Squat", "Deadlift"])),
        )
        db_conn.commit()

        routines = get_hevy_routines()
        match = next(r for r in routines if r["title"] == title)
        assert match["exercises"] == ["Squat", "Deadlift"]


# ── 3+4: get_planned_workouts start_date-Parameter ──────────────────────────

class TestGetPlannedWorkoutsStartDate:
    def test_start_date_shows_sessions_from_yesterday(self, db_conn, cleanup_rows):
        yesterday = datetime.date.today() - datetime.timedelta(days=1)
        ext_id = f"{TEST_PREFIX}yesterday"
        _insert_planned_session(db_conn, ext_id, yesterday, session_type="Easy Run")

        workouts = get_planned_workouts(days=1, start_date=yesterday.isoformat())
        matching = [w for w in workouts if w["session_date"] == yesterday.isoformat()]
        assert matching, "Session von gestern muss bei start_date=gestern erscheinen"

    def test_without_start_date_defaults_to_today(self, db_conn, cleanup_rows):
        yesterday = datetime.date.today() - datetime.timedelta(days=1)
        ext_id = f"{TEST_PREFIX}should-not-show"
        _insert_planned_session(db_conn, ext_id, yesterday, session_type="Easy Run")

        workouts = get_planned_workouts(days=1)
        matching = [w for w in workouts if w["session_date"] == yesterday.isoformat()]
        assert not matching, "Ohne start_date darf eine Session von gestern nicht auftauchen"

    def test_invalid_start_date_returns_error(self):
        result = get_planned_workouts(start_date="not-a-date")
        assert result == [{"error": "start_date 'not-a-date' ist kein gueltiges YYYY-MM-DD"}]


# ── 5+6: sync_hevy_completions neuer Match-Algorithmus ─────────────────────

class TestSyncHevyCompletionsMatching:
    def test_matches_by_date_and_session_type(self, db_conn, cleanup_rows):
        session_date = _SAFE_TEST_DATE
        hevy_id = f"{TEST_PREFIX}match1"
        _insert_hevy_training(db_conn, hevy_id, session_date, "Leg Day")
        ext_id = f"{TEST_PREFIX}planned1"
        tp_id = _insert_planned_session(db_conn, ext_id, session_date, session_type="Strength Training")

        result = sync_hevy_completions(days=30)

        assert result["matched"] >= 1
        assert not any(u["hevy_id"] == hevy_id for u in result["unmatched_hevy"])

        cur = db_conn.cursor()
        cur.execute("SELECT status, hevy_id FROM training_plan WHERE id = %s", (tp_id,))
        status, linked_hevy_id = cur.fetchone()
        assert status == "completed"
        assert linked_hevy_id == hevy_id

    def test_core_and_mobility_session_types_also_match(self, db_conn, cleanup_rows):
        session_date = _SAFE_TEST_DATE + datetime.timedelta(days=1)
        hevy_id = f"{TEST_PREFIX}match2"
        _insert_hevy_training(db_conn, hevy_id, session_date, "Core Session")
        ext_id = f"{TEST_PREFIX}planned2"
        tp_id = _insert_planned_session(db_conn, ext_id, session_date, session_type="Core")

        sync_hevy_completions(days=30)

        cur = db_conn.cursor()
        cur.execute("SELECT status, hevy_id FROM training_plan WHERE id = %s", (tp_id,))
        status, linked_hevy_id = cur.fetchone()
        assert status == "completed"
        assert linked_hevy_id == hevy_id

    def test_no_matching_date_stays_unmatched(self, db_conn, cleanup_rows):
        hevy_id = f"{TEST_PREFIX}nomatch1"
        session_date = _SAFE_TEST_DATE + datetime.timedelta(days=2)
        _insert_hevy_training(db_conn, hevy_id, session_date, "Solo Workout")

        result = sync_hevy_completions(days=30)

        assert any(u["hevy_id"] == hevy_id for u in result["unmatched_hevy"])

    def test_repeated_call_does_not_rematch_or_duplicate(self, db_conn, cleanup_rows):
        session_date = _SAFE_TEST_DATE + datetime.timedelta(days=3)
        hevy_id = f"{TEST_PREFIX}idem1"
        _insert_hevy_training(db_conn, hevy_id, session_date, "Idem Session")
        ext_id = f"{TEST_PREFIX}planned-idem"
        tp_id = _insert_planned_session(db_conn, ext_id, session_date, session_type="Strength Training")

        first = sync_hevy_completions(days=30)
        second = sync_hevy_completions(days=30)

        assert not any(u["hevy_id"] == hevy_id for u in second["unmatched_hevy"]), \
            "Ein bereits verlinktes Hevy-Workout darf beim zweiten Aufruf nicht erneut auftauchen"

        cur = db_conn.cursor()
        cur.execute("SELECT COUNT(*) FROM training_plan WHERE hevy_id = %s", (hevy_id,))
        assert cur.fetchone()[0] == 1, "Kein doppeltes Linking bei wiederholtem Aufruf"
