"""
tests/test_garmin_batch.py
Tests fuer coach/garmin_batch.py (2026-08-15): Advisory Lock, Strength-Guard
vor Garmin-Login, Content-Hash-basiertes unchanged/updated, isolierte
Fehlerbehandlung pro Session.

Garmin-Netzwerkaufrufe sind durchgehend gemockt (monkeypatch auf
coach.garmin_batch.push_workout/schedule_workout/garmin_client) — kein
echter Garmin-Call in diesem Testfile. DB-Zugriffe laufen echt gegen die
Test-/Dev-DB mit klar markierten Testzeilen (external_id-Praefix
'test-batch-'), die in einem Fixture-Teardown wieder entfernt werden.
"""
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import coach.garmin_batch as garmin_batch
from coach.garmin_batch import (
    acquire_lock, release_lock, run_batch, _push_single_session, _build_workout_steps,
)
from coach.sync_utils import compute_content_hash
from database.connection import get_connection

TEST_PREFIX = "test-batch-"


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


def _insert_session(db_conn, external_id, session_type="Easy Run", sport="running",
                     sync_target="garmin", garmin_workout_id=None, sync_status=None,
                     content_hash=None, duration_min=40, distance_km=8, session_zone="Z2"):
    cur = db_conn.cursor()
    cur.execute(
        """INSERT INTO training_plan
               (external_id, week_date, day_of_week, session_type, sport, sync_target,
                status, duration_min, distance_km, session_zone,
                garmin_workout_id, sync_status, content_hash)
           VALUES (%s, '2026-10-05', 1, %s, %s, %s, 'planned', %s, %s, %s, %s, %s, %s)
           RETURNING id""",
        (external_id, session_type, sport, sync_target, duration_min, distance_km,
         session_zone, garmin_workout_id, sync_status, content_hash),
    )
    tp_id = cur.fetchone()[0]
    db_conn.commit()
    return tp_id


# ── Test 1: Advisory Lock verhindert parallele Ausfuehrung ────────────────

class TestAdvisoryLock:
    def test_second_connection_cannot_acquire_held_lock(self, db_conn):
        conn2 = get_connection()
        try:
            assert acquire_lock(db_conn) is True
            assert acquire_lock(conn2) is False, "Zweite Verbindung darf den gehaltenen Lock nicht bekommen"
            release_lock(db_conn)
            assert acquire_lock(conn2) is True, "Nach Freigabe muss der Lock wieder verfuegbar sein"
        finally:
            release_lock(conn2)
            conn2.close()

    def test_run_batch_returns_error_when_lock_held(self, db_conn, monkeypatch):
        held_conn = get_connection()
        assert acquire_lock(held_conn) is True
        try:
            # garmin_client darf gar nicht erst aufgerufen werden, wenn der Lock blockiert
            called = {"n": 0}
            monkeypatch.setattr(garmin_batch, "garmin_client", lambda: called.__setitem__("n", called["n"] + 1))
            result = run_batch(session_ids=[999999999])
            assert "error" in result
            assert called["n"] == 0
        finally:
            release_lock(held_conn)
            held_conn.close()


# ── Test 2: Strength-Session wird vor Garmin-Login abgelehnt ──────────────

class TestStrengthBlockedBeforeLogin:
    def test_strength_session_rejected_without_garmin_call(self, db_conn, cleanup_rows, monkeypatch):
        ext_id = f"{TEST_PREFIX}strength-1"
        tp_id = _insert_session(db_conn, ext_id, session_type="Strength Training",
                                 sport=None, sync_target="hevy")

        login_called = {"n": 0}
        monkeypatch.setattr(garmin_batch, "garmin_client", lambda: login_called.__setitem__("n", login_called["n"] + 1))

        result = run_batch(session_ids=[tp_id])

        assert "error" in result
        assert result["blocked"][0]["id"] == tp_id
        assert login_called["n"] == 0, "Garmin-Login darf bei geblockten Session-Typen nie aufgerufen werden"


# ── Test 3: unveraenderter content_hash -> unchanged ───────────────────────

class TestUnchangedSkipped:
    def test_matching_hash_skips_push(self, db_conn):
        session = {
            "id": 1, "session_date": "2026-10-05", "session_type": "Easy Run",
            "session_zone": "Z2", "distance_km": 8, "duration_min": 40,
            "notes": None, "workout_steps": None, "sport": "running",
            "garmin_workout_id": "123456", "external_id": f"{TEST_PREFIX}unchanged-1",
        }
        session["content_hash"] = compute_content_hash(session)
        session["sync_status"] = "synced"

        client = MagicMock()
        result = _push_single_session(client, db_conn, session)

        assert result["action"] == "unchanged"
        client.upload_running_workout.assert_not_called()


# ── Test 4: geaenderter content_hash -> updated (delete + recreate) ───────

class TestChangedContentRecreated:
    def test_changed_hash_deletes_and_recreates(self, db_conn, cleanup_rows, monkeypatch):
        ext_id = f"{TEST_PREFIX}changed-1"
        old_session = {
            "session_date": "2026-10-05", "session_type": "Easy Run",
            "session_zone": "Z2", "distance_km": 8, "duration_min": 40,
            "notes": None, "workout_steps": None,
        }
        old_hash = compute_content_hash(old_session)
        tp_id = _insert_session(db_conn, ext_id, garmin_workout_id="555555",
                                 sync_status="synced", content_hash=old_hash,
                                 duration_min=40)

        # garmin_mcp_log-Eintrag fuer die "alte" erfolgreiche Version anlegen,
        # damit _push_single_session sie findet (gleiches Datum -> delete+recreate).
        cur = db_conn.cursor()
        cur.execute(
            """INSERT INTO garmin_mcp_log
                   (external_id, action, status, garmin_workout_id, garmin_schedule_id,
                    workout_name, scheduled_date, metadata)
               VALUES (%s, 'push', 'success', 555555, 111111, 'old', '2026-10-05', '{}')""",
            (ext_id,),
        )
        db_conn.commit()

        client = MagicMock()
        client.delete_workout.return_value = None

        monkeypatch.setattr(
            garmin_batch, "push_workout",
            lambda workout_def, date_str, sport="running": {
                "garmin_workout_id": 777777, "garmin_schedule_id": 222222,
                "workout_name": workout_def["name"], "scheduled_date": date_str,
            },
        )

        session = {
            "id": tp_id, "session_date": "2026-10-05", "session_type": "Easy Run",
            "session_zone": "Z2", "distance_km": 8,
            "duration_min": 90,  # geaendert! -> neuer Hash
            "notes": None, "workout_steps": None, "sport": "running",
            "garmin_workout_id": "555555", "external_id": ext_id,
            "content_hash": old_hash, "sync_status": "synced",
        }
        result = _push_single_session(client, db_conn, session)

        assert result["action"] == "updated"
        client.delete_workout.assert_called_once_with("555555")

        cur.execute("SELECT sync_status, garmin_workout_id FROM training_plan WHERE id = %s", (tp_id,))
        sync_status, new_garmin_id = cur.fetchone()
        assert sync_status == "synced"
        assert new_garmin_id == "777777"


# ── Test 5: Fehlschlag einer Session blockiert nicht die anderen ─────────

class TestIsolatedFailures:
    def test_one_failure_does_not_block_others(self, db_conn, cleanup_rows, monkeypatch):
        ext_ok = f"{TEST_PREFIX}iso-ok"
        ext_fail = f"{TEST_PREFIX}iso-fail"
        tp_ok = _insert_session(db_conn, ext_ok, duration_min=30)
        tp_fail = _insert_session(db_conn, ext_fail, duration_min=30)

        monkeypatch.setattr(garmin_batch, "garmin_client", lambda: MagicMock())

        def fake_push(client, conn, session):
            if session["external_id"] == ext_fail:
                raise RuntimeError("simulierter Garmin-Fehler")
            return {"session_id": session["id"], "action": "created", "garmin_workout_id": 42}

        monkeypatch.setattr(garmin_batch, "_push_single_session", fake_push)

        result = run_batch(session_ids=[tp_ok, tp_fail])

        assert tp_fail in [f["session_id"] for f in result["failed"]]
        assert 42 in result["created"]

        cur = db_conn.cursor()
        cur.execute("SELECT sync_status, sync_error FROM training_plan WHERE id = %s", (tp_fail,))
        sync_status, sync_error = cur.fetchone()
        assert sync_status == "failed"
        assert "simulierter Garmin-Fehler" in sync_error


# ── Zusatztest: leere workout_steps -> sinnvoller Fallback (kein Erfinden) ─

class TestWorkoutStepsFallback:
    def test_empty_workout_steps_falls_back_to_single_step(self):
        # 2026-08-16: der Fallback baut kein echtes hr_zone-Target mehr aus
        # session_zone -- No-Target-Policy gilt auch hier, Zone bleibt Freitext.
        steps = _build_workout_steps({"duration_min": 50, "session_zone": "Z3"})
        assert len(steps) == 1
        assert steps[0]["duration_secs"] == 3000
        assert steps[0]["target"] == {"type": "no_target"}

    def test_real_workout_steps_are_used_unchanged(self):
        real_steps = [{"type": "warmup", "duration_secs": 300}]
        steps = _build_workout_steps({"duration_min": 50, "workout_steps": real_steps})
        assert steps == real_steps


# ── Test 14: Wiederholtes Pushen erzeugt keine Duplikate ───────────────────

class TestRepeatedPushIsIdempotent:
    def test_second_push_of_unchanged_session_triggers_no_new_garmin_call(self, db_conn, cleanup_rows, monkeypatch):
        ext_id = f"{TEST_PREFIX}idem-1"
        tp_id = _insert_session(db_conn, ext_id, session_type="Easy Run", distance_km=10, duration_min=60)

        monkeypatch.setattr(garmin_batch, "garmin_client", lambda: MagicMock())
        push_calls = {"n": 0}

        def fake_push(workout_def, date_str, sport="running"):
            push_calls["n"] += 1
            return {"garmin_workout_id": 888888, "garmin_schedule_id": 333333,
                    "workout_name": workout_def["name"], "scheduled_date": date_str}

        monkeypatch.setattr(garmin_batch, "push_workout", fake_push)

        result1 = run_batch(session_ids=[tp_id])
        assert push_calls["n"] == 1
        assert 888888 in result1["created"]

        result2 = run_batch(session_ids=[tp_id])
        assert push_calls["n"] == 1, "Zweiter Push eines unveraenderten Workouts darf keinen neuen Garmin-Call ausloesen"
        assert tp_id in result2["unchanged"]


# ── Test 15: Geaendertes Workout wird als Update erkannt ────────────────────

class TestNameChangeDetectedAsUpdate:
    def test_changing_name_only_triggers_update_not_unchanged(self, db_conn, cleanup_rows, monkeypatch):
        # Vor der Erweiterung von HASH_FIELDS um "name" waere eine reine
        # Namensaenderung fuer den Content-Hash unsichtbar gewesen und haette
        # faelschlich "unchanged" ergeben.
        ext_id = f"{TEST_PREFIX}namechg-1"
        tp_id = _insert_session(db_conn, ext_id, session_type="Easy Run", distance_km=10, duration_min=60)

        client_holder: dict = {}
        monkeypatch.setattr(garmin_batch, "garmin_client", lambda: client_holder.setdefault("client", MagicMock()))
        push_calls = {"n": 0}

        def fake_push(workout_def, date_str, sport="running"):
            push_calls["n"] += 1
            return {"garmin_workout_id": 900001, "garmin_schedule_id": 400001,
                    "workout_name": workout_def["name"], "scheduled_date": date_str}

        monkeypatch.setattr(garmin_batch, "push_workout", fake_push)

        result1 = run_batch(session_ids=[tp_id])
        assert push_calls["n"] == 1

        cur = db_conn.cursor()
        cur.execute("UPDATE training_plan SET name = 'Renamed Session' WHERE id = %s", (tp_id,))
        db_conn.commit()

        result2 = run_batch(session_ids=[tp_id])
        assert push_calls["n"] == 2, "Namensaenderung muss einen erneuten Push ausloesen"
        assert tp_id not in result2["unchanged"]
        client_holder["client"].delete_workout.assert_called_once_with("900001")
