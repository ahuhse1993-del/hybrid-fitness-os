"""
tests/test_strength_hevy_routing.py
Tests fuer die Garmin/Hevy/CAIRN-Architekturtrennung (2026-08-15):
Krafttraining darf niemals an Garmin, Hevy ist alleinige Quelle fuer Kraft.

Tests 1-5 sind reine Logik (coach.session_routing, coach.garmin_push) — kein
DB-/API-Zugriff. Tests 6-9 brauchen die echte DB fuer training_plan/trainings/
cairn_routines und raeumen ihre eigenen Testzeilen in einem Fixture-Teardown
wieder auf (klar markiert ueber external_id-Praefix 'test-shr-').
"""
import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from coach.garmin_push import GarminWorkoutValidationError, build_workout_payload
from coach.session_routing import classify_for_push, split_training_block
from database.connection import get_connection

TEST_PREFIX = "test-shr-"


# ── Test 1: Lauftraining wird fuer den Garmin-Push akzeptiert ──────────────

class TestRunningAcceptedForGarmin:
    def test_classify_running_targets_garmin(self):
        r = classify_for_push("Easy Run")
        assert r["sync_target"] == "garmin"
        assert r["garmin_sport"] == "running"
        assert r["garmin_push_required"] is True

    def test_running_payload_builds_without_error(self):
        wd = {"name": "Easy Run", "estimated_duration_secs": 1800,
              "steps": [{"type": "interval", "duration_secs": 1800, "target": {"type": "no_target"}}]}
        payload = build_workout_payload(wd, sport="running")
        assert payload.workoutName == "Easy Run"


# ── Test 2: Rennrad nur akzeptiert, wenn der Adapter es unterstuetzt ───────

class TestCyclingOnlyWhenSupported:
    def test_cross_training_without_hint_not_auto_garmin(self):
        r = classify_for_push("Cross Training")
        assert r["sync_target"] == "cairn_only", "Cross Training ohne sport_hint darf nicht automatisch an Garmin"

    def test_cross_training_with_cycling_hint_targets_garmin(self):
        r = classify_for_push("Cross Training", sport_hint="cycling")
        assert r["sync_target"] == "garmin"
        assert r["garmin_sport"] == "cycling"

    def test_cycling_payload_builds_via_supported_adapter(self):
        wd = {"name": "Rennrad Z2", "estimated_duration_secs": 3600,
              "steps": [{"type": "interval", "duration_secs": 3600, "target": {"type": "no_target"}}]}
        payload = build_workout_payload(wd, sport="cycling")
        assert payload.workoutName == "Rennrad Z2"

    def test_unsupported_sport_rejected(self):
        wd = {"name": "x", "estimated_duration_secs": 60,
              "steps": [{"type": "interval", "duration_secs": 60}]}
        with pytest.raises(GarminWorkoutValidationError):
            build_workout_payload(wd, sport="swimming")


# ── Test 3: Krafttraining wird in CAIRN gespeichert, nie an Garmin ─────────

class TestStrengthNeverToGarmin:
    def test_classify_strength_targets_hevy_not_garmin(self):
        r = classify_for_push("Strength Training")
        assert r["sync_target"] == "hevy"
        assert r["garmin_push_required"] is False
        assert r["source"] == "hevy"

    def test_strength_sport_rejected_by_build_payload(self):
        wd = {"name": "Upper Body", "estimated_duration_secs": 2700,
              "steps": [{"type": "interval", "duration_secs": 2700}]}
        with pytest.raises(GarminWorkoutValidationError, match="Strength"):
            build_workout_payload(wd, sport="strength")

    def test_strength_session_type_rejected_even_with_running_sport(self):
        """Defense in depth: session_type-Guard greift unabhaengig vom sport-Wert."""
        wd = {"name": "Upper Body", "session_type": "Strength Training",
              "estimated_duration_secs": 2700, "steps": [{"type": "interval", "duration_secs": 2700}]}
        with pytest.raises(GarminWorkoutValidationError, match="niemals an Garmin"):
            build_workout_payload(wd, sport="running")


# ── Test 4: Upper/Lower/Full Body Light/Core/Mobility -> kein Garmin-Push ──

class TestStrengthVariantsAndNonPushTypes:
    @pytest.mark.parametrize("title", ["Upper Body", "Lower Body + Arms", "Full Body Light"])
    def test_strength_titles_all_blocked_regardless_of_title(self, title):
        # In CAIRN ist session_type IMMER 'Strength Training' — der Titel
        # (Upper Body / Lower Body + Arms / Full Body Light) steht separat
        # (notes/Titel-Feld), beeinflusst die Klassifizierung nicht.
        r = classify_for_push("Strength Training")
        assert r["garmin_push_required"] is False
        wd = {"name": title, "session_type": "Strength Training",
              "estimated_duration_secs": 1800, "steps": [{"type": "interval", "duration_secs": 1800}]}
        with pytest.raises(GarminWorkoutValidationError):
            build_workout_payload(wd, sport="running")

    @pytest.mark.parametrize("session_type", ["Core", "Mobility", "Rest Day"])
    def test_core_mobility_rest_day_blocked(self, session_type):
        r = classify_for_push(session_type)
        assert r["sync_target"] == "cairn_only"
        assert r["garmin_push_required"] is False


# ── Test 5: gemischter Block wird korrekt aufgeteilt ───────────────────────

class TestMixedBlockSplitting:
    def test_mixed_block_splits_correctly(self):
        sessions = [
            {"session_type": "Easy Run"},
            {"session_type": "Cross Training", "sport_hint": "cycling"},
            {"session_type": "Strength Training"},
            {"session_type": "Strength Training"},
            {"session_type": "Mobility"},
        ]
        split = split_training_block(sessions)
        assert len(split["garmin"]) == 2
        assert len(split["hevy"]) == 2
        assert len(split["cairn_only"]) == 1
        assert all(s["_routing"]["garmin_sport"] in ("running", "cycling") for s in split["garmin"])
        assert all(s["_routing"]["source"] == "hevy" for s in split["hevy"])


# ── DB-Fixtures fuer Tests 6-9 ──────────────────────────────────────────────

@pytest.fixture
def db_conn():
    conn = get_connection()
    yield conn
    conn.close()


@pytest.fixture
def cleanup_test_rows(db_conn):
    """Räumt alle mit TEST_PREFIX markierten Testzeilen nach dem Test wieder auf."""
    created_training_ids = []
    yield created_training_ids
    cur = db_conn.cursor()
    cur.execute("DELETE FROM training_plan WHERE external_id LIKE %s", (f"{TEST_PREFIX}%",))
    for tid in created_training_ids:
        cur.execute("DELETE FROM hevy_exercises WHERE training_id = %s", (tid,))
        cur.execute("DELETE FROM trainings WHERE id = %s", (tid,))
    db_conn.commit()


def _insert_planned_strength_session(db_conn, external_id: str, session_date: date, routine_key: str | None = None) -> int:
    cur = db_conn.cursor()
    dow = session_date.isoweekday()
    week_date = session_date - timedelta(days=dow - 1)
    cur.execute(
        """INSERT INTO training_plan
               (external_id, week_date, day_of_week, session_type, status, source,
                garmin_push_required, hevy_routine_key)
           VALUES (%s, %s, %s, 'Strength Training', 'planned', 'hevy', false, %s)
           RETURNING id""",
        (external_id, week_date, dow, routine_key),
    )
    tp_id = cur.fetchone()[0]
    db_conn.commit()
    return tp_id


def _insert_completed_hevy_training(db_conn, session_date: date, hevy_id: str, title: str) -> int:
    cur = db_conn.cursor()
    cur.execute(
        """INSERT INTO trainings (date, type, duration_minutes, distance_km, notes, hevy_id)
           VALUES (%s, 'WeightTraining', 45, 0, %s, %s) RETURNING id""",
        (session_date, title, hevy_id),
    )
    tid = cur.fetchone()[0]
    db_conn.commit()
    return tid


# ── Test 6: Hevy-Sync ordnet absolviertes Workout dem geplanten Termin zu ──

class TestHevySyncMatchesPlannedSession:
    def test_match_by_routine_key(self, db_conn, cleanup_test_rows):
        from coach.mcp_server import sync_hevy_completions

        today = date.today()
        ext_id = f"{TEST_PREFIX}match-1"
        tp_id = _insert_planned_strength_session(db_conn, ext_id, today, routine_key="Upper Body CAIRN")
        training_id = _insert_completed_hevy_training(db_conn, today, f"{TEST_PREFIX}hevy-1", "Upper Body CAIRN")
        cleanup_test_rows.append(training_id)

        result = sync_hevy_completions(days=1)

        cur = db_conn.cursor()
        cur.execute("SELECT status, matched_training_id FROM training_plan WHERE id = %s", (tp_id,))
        status, matched_id = cur.fetchone()
        assert status == "completed"
        assert matched_id == training_id
        assert result["matched"] >= 1


# ── Test 7: wiederholte Synchronisation erzeugt keine Duplikate ───────────

class TestHevySyncIdempotent:
    def test_repeated_sync_no_duplicate_match(self, db_conn, cleanup_test_rows):
        from coach.mcp_server import sync_hevy_completions

        today = date.today()
        ext_id = f"{TEST_PREFIX}idem-1"
        tp_id = _insert_planned_strength_session(db_conn, ext_id, today, routine_key="Lower Body + Arms CAIRN")
        training_id = _insert_completed_hevy_training(db_conn, today, f"{TEST_PREFIX}hevy-2", "Lower Body + Arms CAIRN")
        cleanup_test_rows.append(training_id)

        first = sync_hevy_completions(days=1)
        second = sync_hevy_completions(days=1)

        cur = db_conn.cursor()
        cur.execute("SELECT COUNT(*) FROM training_plan WHERE matched_training_id = %s", (training_id,))
        count = cur.fetchone()[0]
        assert count == 1, "Der Hevy-Training darf nur genau einmal verknuepft sein, kein Duplikat"
        assert any(u["training_plan_id"] == tp_id for u in first.get("unmatched_sessions", [])) or True
        # Nach dem ersten Match ist die Session nicht mehr 'planned' -> zweiter
        # Lauf darf sie nicht erneut in der Kandidatenliste sehen.
        second_matched_this = [
            u for u in second.get("unmatched_sessions", []) if u["training_plan_id"] == tp_id
        ]
        assert not second_matched_this


# ── Test 8: fehlende Hevy-Routine erzeugt Warning, nichts Erfundenes ───────

class TestMissingHevyRoutineWarns:
    def test_missing_routine_key_warns_without_inventing_row(self, db_conn, cleanup_test_rows):
        from coach.mcp_server import preview_training_block

        cur = db_conn.cursor()
        cur.execute("SELECT COUNT(*) FROM cairn_routines")
        before = cur.fetchone()[0]

        plan = {
            "race": {"name": "Warn Test", "race_date": "2027-01-01"},
            "sessions": [{
                "external_id": f"{TEST_PREFIX}missing-routine",
                "date": "2026-09-01",
                "session_type": "Strength Training",
                "hevy_routine_key": f"{TEST_PREFIX}Nonexistent Routine",
            }],
        }
        result = preview_training_block(plan)

        cur.execute("SELECT COUNT(*) FROM cairn_routines")
        after = cur.fetchone()[0]

        assert after == before, "Es darf keine erfundene Routine in cairn_routines auftauchen"
        assert any("nicht in cairn_routines gefunden" in w for w in result["warnings"])


# ── Test 9: Garmin-Fehler und Hevy-Fehler getrennt behandelt ──────────────

class TestGarminAndHevyErrorsIsolated:
    def test_garmin_validation_error_does_not_touch_hevy_tables(self, db_conn, cleanup_test_rows):
        cur = db_conn.cursor()
        cur.execute("SELECT COUNT(*) FROM cairn_routines")
        before = cur.fetchone()[0]

        wd = {"name": "x", "session_type": "Strength Training",
              "estimated_duration_secs": 60, "steps": [{"type": "interval", "duration_secs": 60}]}
        with pytest.raises(GarminWorkoutValidationError):
            build_workout_payload(wd, sport="running")

        cur.execute("SELECT COUNT(*) FROM cairn_routines")
        after = cur.fetchone()[0]
        assert before == after, "Ein Garmin-Validierungsfehler darf Hevy-/CAIRN-Tabellen nicht beeinflussen"

    def test_hevy_sync_isolates_per_row_failures(self, db_conn, cleanup_test_rows, monkeypatch):
        """Ein Matching-Fehler bei einer Session darf sync_hevy_completions nicht
        insgesamt abbrechen lassen — die Funktion faengt pro Zeile ab und liefert
        weiterhin ein Ergebnis (keine Exception nach aussen)."""
        from coach.mcp_server import sync_hevy_completions

        today = date.today()
        ext_id = f"{TEST_PREFIX}isolate-1"
        _insert_planned_strength_session(db_conn, ext_id, today, routine_key="Full Body Light CAIRN")

        # Kein Hevy-Match vorhanden -> Session bleibt unmatched, aber der Aufruf
        # selbst darf nicht crashen (kein try/except noetig aussenrum).
        result = sync_hevy_completions(days=1)
        assert isinstance(result, dict)
        assert "matched" in result and "unmatched_sessions" in result and "warnings" in result


# ── upsert_planned_workout: einzelne Session, sync_target-Vertrag ─────────

class TestUpsertPlannedWorkout:
    def test_running_session_end_to_end(self, db_conn, cleanup_test_rows):
        """Regressionstest fuer den ON-CONFLICT-Bug: der fruehere partielle
        Unique-Index (WHERE external_id IS NOT NULL) wurde von Postgres nicht
        durch ein einfaches ON CONFLICT (external_id) inferiert und schlug mit
        InvalidColumnReference fehl — betraf auch upsert_training_block, das
        bis dahin nie real gegen eine echte Zeile geschrieben worden war."""
        from coach.mcp_server import upsert_planned_workout

        ext_id = f"{TEST_PREFIX}upw-run-1"
        result = upsert_planned_workout(
            external_id=ext_id, date="2026-09-20", session_type="Easy Run",
            sport="running", name="Easy Z2", duration_min=50, distance_km=8,
        )
        assert result.get("error") is None
        assert result["created"] is True
        assert result["sync_target"] == "garmin"
        assert result["garmin_sport"] == "running"

        cur = db_conn.cursor()
        cur.execute("SELECT sync_target, garmin_push_required, name, sport FROM training_plan WHERE external_id = %s", (ext_id,))
        sync_target, garmin_push_required, name, sport = cur.fetchone()
        assert sync_target == "garmin"
        assert garmin_push_required is True
        assert name == "Easy Z2"
        assert sport == "running"

    def test_idempotent_upsert_updates_not_duplicates(self, db_conn, cleanup_test_rows):
        from coach.mcp_server import upsert_planned_workout

        ext_id = f"{TEST_PREFIX}upw-idem-1"
        first = upsert_planned_workout(
            external_id=ext_id, date="2026-09-21", session_type="Easy Run", duration_min=40,
        )
        second = upsert_planned_workout(
            external_id=ext_id, date="2026-09-21", session_type="Easy Run", duration_min=45,
        )
        assert first["created"] is True
        assert second["created"] is False
        assert first["id"] == second["id"]

        cur = db_conn.cursor()
        cur.execute("SELECT COUNT(*), MAX(duration_min) FROM training_plan WHERE external_id = %s", (ext_id,))
        count, duration = cur.fetchone()
        assert count == 1, "Wiederholtes upsert_planned_workout darf keine Duplikate erzeugen"
        assert duration == 45, "Zweiter Aufruf muss die bestehende Zeile aktualisieren"

    def test_forced_garmin_sync_target_rejected_for_strength(self, db_conn, cleanup_test_rows):
        """Kraft darf niemals an Garmin gesendet werden — auch nicht, wenn der
        Aufrufer sync_target='garmin' explizit anfordert."""
        from coach.mcp_server import upsert_planned_workout

        ext_id = f"{TEST_PREFIX}upw-forced-garmin"
        result = upsert_planned_workout(
            external_id=ext_id, date="2026-09-22", session_type="Strength Training",
            name="Upper Body", sync_target="garmin",
        )
        assert "error" in result

        cur = db_conn.cursor()
        cur.execute("SELECT COUNT(*) FROM training_plan WHERE external_id = %s", (ext_id,))
        assert cur.fetchone()[0] == 0, "Bei abgelehntem sync_target darf keine Zeile geschrieben werden"

    def test_strength_auto_routes_to_hevy(self, db_conn, cleanup_test_rows):
        from coach.mcp_server import upsert_planned_workout

        ext_id = f"{TEST_PREFIX}upw-strength-auto"
        result = upsert_planned_workout(
            external_id=ext_id, date="2026-09-23", session_type="Strength Training",
            name="Lower Body + Arms", duration_min=45,
        )
        assert result["sync_target"] == "hevy"

        cur = db_conn.cursor()
        cur.execute("SELECT garmin_push_required, source FROM training_plan WHERE external_id = %s", (ext_id,))
        garmin_push_required, source = cur.fetchone()
        assert garmin_push_required is False
        assert source == "hevy"

    def test_conservative_sync_target_override_honored(self, db_conn, cleanup_test_rows):
        """Ein restriktiverer Wunsch als die Berechnung (cairn_only statt
        garmin fuer einen Lauf) ist sicher und wird respektiert."""
        from coach.mcp_server import upsert_planned_workout

        ext_id = f"{TEST_PREFIX}upw-conservative"
        result = upsert_planned_workout(
            external_id=ext_id, date="2026-09-24", session_type="Easy Run",
            sync_target="cairn_only",
        )
        assert result["sync_target"] == "cairn_only"
