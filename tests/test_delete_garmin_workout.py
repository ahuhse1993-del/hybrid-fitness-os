"""
tests/test_delete_garmin_workout.py
Tests fuer delete_garmin_workout() DB-Aufraeumung und bulk_delete_garmin_workouts()
(2026-08-15). Garmin-Client durchgehend gemockt (coach.mcp_server.garmin_client) —
kein echter Garmin-Call. DB-Zugriffe laufen echt gegen die Dev-DB mit klar
markierten Testzeilen (external_id-Praefix 'test-del-'), die pro Test aufgeraeumt
werden.
"""
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import coach.mcp_server as mcp_server
from coach.mcp_server import bulk_delete_garmin_workouts, delete_garmin_workout
from database.connection import get_connection

TEST_PREFIX = "test-del-"


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
    db_conn.commit()


def _insert_synced_row(db_conn, external_id: str, garmin_workout_id: str):
    cur = db_conn.cursor()
    cur.execute(
        """INSERT INTO training_plan
               (external_id, week_date, day_of_week, session_type,
                garmin_workout_id, sync_status, sync_error, content_hash, last_synced_at)
           VALUES (%s, '2026-10-05', 1, 'Easy Run', %s, 'synced', NULL, 'dummyhash', NOW())
           RETURNING id""",
        (external_id, garmin_workout_id),
    )
    tp_id = cur.fetchone()[0]
    db_conn.commit()
    return tp_id


class TestDeleteGarminWorkoutClearsDb:
    def test_clears_matching_training_plan_row(self, db_conn, cleanup_rows):
        ext_id = f"{TEST_PREFIX}1"
        tp_id = _insert_synced_row(db_conn, ext_id, "555001")

        client = MagicMock()
        with patch.object(mcp_server, "garmin_client", return_value=client):
            result = delete_garmin_workout(555001)

        assert result == {"deleted": True, "garmin_workout_id": 555001, "db_rows_cleared": 1}
        client.delete_workout.assert_called_once_with(555001)

        cur = db_conn.cursor()
        cur.execute(
            "SELECT garmin_workout_id, sync_status, content_hash, last_synced_at "
            "FROM training_plan WHERE id = %s", (tp_id,)
        )
        row = cur.fetchone()
        assert row == (None, None, None, None)

    def test_no_matching_row_returns_zero_cleared(self, db_conn, cleanup_rows):
        client = MagicMock()
        with patch.object(mcp_server, "garmin_client", return_value=client):
            result = delete_garmin_workout(999999999)
        assert result["db_rows_cleared"] == 0
        assert result["deleted"] is True

    def test_garmin_failure_raises_and_leaves_db_untouched(self, db_conn, cleanup_rows):
        ext_id = f"{TEST_PREFIX}2"
        tp_id = _insert_synced_row(db_conn, ext_id, "555002")

        client = MagicMock()
        client.delete_workout.side_effect = Exception("Garmin 500")
        with patch.object(mcp_server, "garmin_client", return_value=client):
            with pytest.raises(RuntimeError, match="Failed to delete"):
                delete_garmin_workout(555002)

        cur = db_conn.cursor()
        cur.execute("SELECT sync_status FROM training_plan WHERE id = %s", (tp_id,))
        assert cur.fetchone()[0] == "synced", "Bei Garmin-Fehler darf die DB-Zeile nicht angetastet werden"


class TestBulkDeleteGarminWorkouts:
    def test_single_login_for_whole_batch(self, db_conn, cleanup_rows):
        _insert_synced_row(db_conn, f"{TEST_PREFIX}bulk-1", "555010")
        _insert_synced_row(db_conn, f"{TEST_PREFIX}bulk-2", "555011")

        client = MagicMock()
        login_calls = {"n": 0}

        def fake_client():
            login_calls["n"] += 1
            return client

        with patch.object(mcp_server, "garmin_client", side_effect=fake_client):
            result = bulk_delete_garmin_workouts([555010, 555011])

        assert login_calls["n"] == 1, "Genau ein Garmin-Login fuer den ganzen Batch"
        assert result["deleted"] == [555010, 555011]
        assert result["failed"] == []
        assert result["db_rows_cleared"] == 2

    def test_one_failure_does_not_block_others(self, db_conn, cleanup_rows):
        _insert_synced_row(db_conn, f"{TEST_PREFIX}bulk-ok", "555020")
        _insert_synced_row(db_conn, f"{TEST_PREFIX}bulk-fail", "555021")

        client = MagicMock()

        def fake_delete(gw_id):
            if gw_id == 555021:
                raise Exception("simulierter Garmin-Fehler")

        client.delete_workout.side_effect = fake_delete

        with patch.object(mcp_server, "garmin_client", return_value=client):
            result = bulk_delete_garmin_workouts([555020, 555021])

        assert result["deleted"] == [555020]
        assert result["failed"] == [{"garmin_workout_id": 555021, "error": "Exception: simulierter Garmin-Fehler"}]
        assert result["db_rows_cleared"] == 1

        cur = db_conn.cursor()
        cur.execute("SELECT sync_status FROM training_plan WHERE garmin_workout_id = '555021'")
        assert cur.fetchone()[0] == "synced", "Fehlgeschlagene Loeschung darf die DB-Zeile nicht veraendern"

    def test_empty_list_returns_empty_summary(self):
        client = MagicMock()
        with patch.object(mcp_server, "garmin_client", return_value=client):
            result = bulk_delete_garmin_workouts([])
        assert result == {"deleted": [], "failed": [], "db_rows_cleared": 0}
