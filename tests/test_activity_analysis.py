"""
tests/test_activity_analysis.py
Tests fuer die CAIRN Activity-Analysis-Plattform (2026-08-18):
Athlete-Profil-Patch, Gear-System, native Laps vs. berechnete km_splits,
HF-Zonen (zeitgewichtet), Route, Datenqualitaet, persistente Coach-Analysen
(Versionierung/Idempotenz/Stale-Erkennung), Backfill fuer bestehende
Aktivitaeten ohne Detaildaten.

DB-Zugriffe laufen echt gegen die Dev-DB mit klar markierten Testzeilen
(notes-Praefix 'TEST-ACTAN-' fuer trainings, entsprechende Praefixe fuer
Gear/Profil), die per Fixture aufgeraeumt werden. Kein echter Garmin-API-Call.
"""
import datetime
import os
import sys

import pytest
from psycopg2.extras import Json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import coach.jobs.sync_completed_activities as sync_job
from coach.activity_data import (
    build_data_quality,
    build_hr_zones,
    build_native_laps,
    build_route,
    compute_km_splits,
)
from coach.mcp_server import (
    assign_activity_gear,
    create_athlete_gear,
    get_activity_analysis_data,
    get_activity_analysis_status,
    list_athlete_gear,
    remove_activity_gear,
    retire_athlete_gear,
    save_activity_coach_analysis,
    update_athlete_gear,
    update_athlete_profile,
)
from database.connection import get_connection

TEST_TAG = "TEST-ACTAN-"


@pytest.fixture
def db_conn():
    conn = get_connection()
    yield conn
    conn.close()


@pytest.fixture
def cleanup_rows(db_conn):
    training_ids: list[int] = []
    gear_ids: list[int] = []
    yield {"training_ids": training_ids, "gear_ids": gear_ids}
    db_conn.rollback()
    cur = db_conn.cursor()
    for tid in training_ids:
        cur.execute("DELETE FROM activity_analyses WHERE training_id = %s", (tid,))
        cur.execute("DELETE FROM activity_gear_usage WHERE training_id = %s", (tid,))
        cur.execute("DELETE FROM activity_stream WHERE training_id = %s", (tid,))
        cur.execute("DELETE FROM splits WHERE training_id = %s", (tid,))
        cur.execute("DELETE FROM trainings WHERE id = %s", (tid,))
    for gid in gear_ids:
        cur.execute("DELETE FROM activity_gear_usage WHERE gear_id = %s", (gid,))
        cur.execute("DELETE FROM athlete_gear WHERE id = %s", (gid,))
    db_conn.commit()


def _insert_training(db_conn, distance_km=10.0, duration_min=60, activity_type="Run",
                      date=None, garmin_id=None) -> int:
    cur = db_conn.cursor()
    cur.execute(
        """INSERT INTO trainings (date, type, duration_minutes, distance_km, notes, garmin_id)
           VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
        (date or datetime.date.today(), activity_type, duration_min, distance_km,
         f"{TEST_TAG}activity", garmin_id),
    )
    tid = cur.fetchone()[0]
    db_conn.commit()
    return tid


def _insert_stream(db_conn, training_id: int, points: list[dict]):
    cur = db_conn.cursor()
    rows = [
        (training_id, p.get("distance_m"), p.get("elapsed_s"), p.get("heart_rate"),
         p.get("speed_ms"), p.get("cadence"), p.get("elevation_m"), p.get("lat"), p.get("lon"))
        for p in points
    ]
    cur.executemany(
        """INSERT INTO activity_stream
               (training_id, distance_m, elapsed_s, heart_rate, speed_ms, cadence, elevation_m, lat, lon)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        rows,
    )
    db_conn.commit()


def _insert_split(db_conn, training_id: int, **fields) -> int:
    cur = db_conn.cursor()
    split_number = fields.pop("split_number", 1)
    cols = ["training_id", "split_number"] + list(fields.keys())
    vals = [training_id, split_number] + list(fields.values())
    placeholders = ", ".join(["%s"] * len(cols))
    cur.execute(f"INSERT INTO splits ({', '.join(cols)}) VALUES ({placeholders}) RETURNING id", vals)
    sid = cur.fetchone()[0]
    db_conn.commit()
    return sid


def _make_distance_time_series(total_m: float, n_points: int, pace_s_per_km: float,
                                hr_base: int = 140) -> list[dict]:
    points = []
    for i in range(n_points):
        d = total_m * i / (n_points - 1)
        t = d / 1000 * pace_s_per_km
        points.append({"distance_m": d, "elapsed_s": t, "heart_rate": hr_base + (i % 5),
                        "speed_ms": 1000 / pace_s_per_km, "cadence": 170, "elevation_m": 400 + (i % 10)})
    return points


# ── 1+2: Partielles Profil-Update, null vs. nicht uebergeben ───────────────

class TestProfilePartialUpdate:
    def test_partial_update_leaves_other_fields_untouched(self, db_conn):
        cur = db_conn.cursor()
        cur.execute("SELECT weight_kg, age FROM athlete_profile ORDER BY id DESC LIMIT 1")
        before_weight, before_age = cur.fetchone()

        result = update_athlete_profile(patch={"height_cm": 176})
        assert result["updated"] is True

        cur.execute("SELECT weight_kg, age, height_cm FROM athlete_profile ORDER BY id DESC LIMIT 1")
        after_weight, after_age, after_height = cur.fetchone()
        assert after_weight == before_weight
        assert after_age == before_age
        assert float(after_height) == 176.0

        update_athlete_profile(patch={"height_cm": None})  # reset

    def test_null_clears_field_distinct_from_omitted(self, db_conn):
        update_athlete_profile(patch={"height_cm": 180})
        cur = db_conn.cursor()
        cur.execute("SELECT height_cm FROM athlete_profile ORDER BY id DESC LIMIT 1")
        assert float(cur.fetchone()[0]) == 180.0

        # Ein Update OHNE height_cm im patch darf es nicht anfassen.
        update_athlete_profile(patch={"weight_kg": 74.5})
        cur.execute("SELECT height_cm, weight_kg FROM athlete_profile ORDER BY id DESC LIMIT 1")
        height, weight = cur.fetchone()
        assert float(height) == 180.0
        assert float(weight) == 74.5

        # Explizites null LEERT es.
        update_athlete_profile(patch={"height_cm": None})
        cur.execute("SELECT height_cm FROM athlete_profile ORDER BY id DESC LIMIT 1")
        assert cur.fetchone()[0] is None


# ── 3: Ungueltige/ueberlappende HF-Zonen werden abgelehnt ──────────────────

class TestHrZoneValidation:
    def test_overlapping_zones_rejected(self):
        result = update_athlete_profile(patch={"hr_zones": {
            "z1": {"min": 0, "max": 144}, "z2": {"min": 140, "max": 152},
            "z3": {"min": 153, "max": 160}, "z4": {"min": 161, "max": 169}, "z5": {"min": 170, "max": 220},
        }})
        assert result["updated"] is False
        assert "überlappen" in result["errors"][0]

    def test_min_greater_than_max_rejected(self):
        result = update_athlete_profile(patch={"hr_zones": {
            "z1": {"min": 150, "max": 100}, "z2": {"min": 145, "max": 152},
            "z3": {"min": 153, "max": 160}, "z4": {"min": 161, "max": 169}, "z5": {"min": 170, "max": 220},
        }})
        assert result["updated"] is False
        assert any("größer" in e for e in result["errors"])

    def test_unknown_field_rejected(self):
        result = update_athlete_profile(patch={"not_a_real_field": 1})
        assert result["updated"] is False
        assert "Unbekannte Felder" in result["error"]


# ── 4: Gear erstellen, aktualisieren, stilllegen ────────────────────────────

class TestGearLifecycle:
    def test_create_update_retire(self, cleanup_rows):
        created = create_athlete_gear(gear_type="trail_shoe", nickname=f"{TEST_TAG}shoe1", target_distance_km=500)
        gid = created["gear"]["id"]
        cleanup_rows["gear_ids"].append(gid)
        assert created["gear"]["active"] is True

        updated = update_athlete_gear(gid, {"nickname": f"{TEST_TAG}shoe1-renamed"})
        assert updated["updated"] is True
        assert updated["gear"]["nickname"] == f"{TEST_TAG}shoe1-renamed"

        retired = retire_athlete_gear(gid)
        assert retired["retired"] is True
        assert retired["gear"]["active"] is False

        still_listed = [g for g in list_athlete_gear(active_only=False) if g["id"] == gid]
        assert still_listed, "Stillgelegtes Gear darf nicht geloescht werden (Historie erhalten)"


# ── 5+6+7: Idempotenz, Schuhwechsel, Distanzaenderung ──────────────────────

class TestActivityGearAssignment:
    def test_same_shoe_same_activity_not_double_counted(self, db_conn, cleanup_rows):
        tid = _insert_training(db_conn, distance_km=10.0)
        cleanup_rows["training_ids"].append(tid)
        gear = create_athlete_gear(gear_type="trail_shoe", nickname=f"{TEST_TAG}g1")
        gid = gear["gear"]["id"]
        cleanup_rows["gear_ids"].append(gid)

        first = assign_activity_gear(activity_id=tid, gear_id=gid)
        assert first["idempotent"] is False
        second = assign_activity_gear(activity_id=tid, gear_id=gid)
        assert second["idempotent"] is True
        assert second["distance_added_km"] == 0

        cur = db_conn.cursor()
        cur.execute("SELECT COUNT(*) FROM activity_gear_usage WHERE training_id=%s AND gear_id=%s", (tid, gid))
        assert cur.fetchone()[0] == 1

    def test_shoe_swap_moves_distance(self, db_conn, cleanup_rows):
        tid = _insert_training(db_conn, distance_km=8.0)
        cleanup_rows["training_ids"].append(tid)
        g1 = create_athlete_gear(gear_type="trail_shoe", nickname=f"{TEST_TAG}old")["gear"]["id"]
        g2 = create_athlete_gear(gear_type="trail_shoe", nickname=f"{TEST_TAG}new")["gear"]["id"]
        cleanup_rows["gear_ids"].extend([g1, g2])

        assign_activity_gear(activity_id=tid, gear_id=g1)
        result = assign_activity_gear(activity_id=tid, gear_id=g2)

        assert result["replaced_gear"] is not None
        assert result["replaced_gear"]["id"] == g1
        assert result["replaced_gear"]["new_total_distance_km"] == 0

        cur = db_conn.cursor()
        cur.execute("SELECT COUNT(*) FROM activity_gear_usage WHERE training_id=%s AND gear_id=%s", (tid, g1))
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT distance_km FROM activity_gear_usage WHERE training_id=%s AND gear_id=%s", (tid, g2))
        assert float(cur.fetchone()[0]) == 8.0

    def test_changed_activity_distance_updates_gear_km(self, db_conn, cleanup_rows):
        tid = _insert_training(db_conn, distance_km=5.0)
        cleanup_rows["training_ids"].append(tid)
        gid = create_athlete_gear(gear_type="running_shoe", nickname=f"{TEST_TAG}g2")["gear"]["id"]
        cleanup_rows["gear_ids"].append(gid)

        assign_activity_gear(activity_id=tid, gear_id=gid)
        db_conn.cursor().execute("UPDATE trainings SET distance_km = 12.0 WHERE id = %s", (tid,))
        db_conn.commit()
        result = assign_activity_gear(activity_id=tid, gear_id=gid)  # distance_km=None -> re-derive

        assert result["gear"]["new_total_distance_km"] == 12.0

    def test_remove_and_reassign(self, db_conn, cleanup_rows):
        tid = _insert_training(db_conn, distance_km=6.0)
        cleanup_rows["training_ids"].append(tid)
        gid = create_athlete_gear(gear_type="trail_shoe", nickname=f"{TEST_TAG}g3")["gear"]["id"]
        cleanup_rows["gear_ids"].append(gid)

        assign_activity_gear(activity_id=tid, gear_id=gid)
        removed = remove_activity_gear(activity_id=tid, gear_id=gid)
        assert removed["removed"] is True
        assert removed["gear"]["total_distance_km"] == 0


# ── 8: Native Garmin-Laps bleiben erhalten ──────────────────────────────────

class TestNativeLapsUnchanged:
    def test_native_laps_reflect_splits_table_verbatim(self, db_conn, cleanup_rows):
        tid = _insert_training(db_conn, distance_km=5.0)
        cleanup_rows["training_ids"].append(tid)
        _insert_split(db_conn, tid, split_number=1, distance_km=5.0, pace_seconds=300,
                       heart_rate_avg=150, elevation_gain=100, cadence_avg=170,
                       max_hr=160, elevation_loss_m=20, lap_type="interval")

        laps = build_native_laps(db_conn, tid)
        assert len(laps) == 1
        assert laps[0]["avg_hr"] == 150
        assert laps[0]["lap_type"] == "interval"
        assert laps[0]["elevation_loss_m"] == 20.0

        # Ein zweiter Aufruf darf dieselben, unveraenderten Werte liefern.
        laps2 = build_native_laps(db_conn, tid)
        assert laps == laps2


# ── 9+10+11: km_splits Berechnung, partial-Flag, kein Erfinden ─────────────

class TestKmSplits:
    def test_computes_splits_from_full_distance_stream(self, db_conn, cleanup_rows):
        tid = _insert_training(db_conn, distance_km=3.0)
        cleanup_rows["training_ids"].append(tid)
        points = _make_distance_time_series(3000, 60, pace_s_per_km=300)
        _insert_stream(db_conn, tid, points)

        splits, reason = compute_km_splits(db_conn, tid)
        assert reason is None
        assert len(splits) == 3
        assert splits[0]["km"] == 1
        assert splits[0]["partial"] is False
        assert abs(splits[0]["pace_seconds_per_km"] - 300) < 5

    def test_partial_split_flagged(self, db_conn, cleanup_rows):
        tid = _insert_training(db_conn, distance_km=2.5)
        cleanup_rows["training_ids"].append(tid)
        points = _make_distance_time_series(2500, 50, pace_s_per_km=300)
        _insert_stream(db_conn, tid, points)

        splits, reason = compute_km_splits(db_conn, tid)
        assert len(splits) == 3
        assert splits[0]["partial"] is False
        assert splits[1]["partial"] is False
        assert splits[2]["partial"] is True
        assert splits[2]["distance_m"] < 1000

    def test_no_stream_returns_no_artificial_splits(self, db_conn, cleanup_rows):
        tid = _insert_training(db_conn, distance_km=15.0)
        cleanup_rows["training_ids"].append(tid)
        # Nur ein Gesamtsplit, kein Stream — wie bei Aktivitaet 461 vor dem Backfill.
        _insert_split(db_conn, tid, split_number=1, distance_km=15.0, pace_seconds=400)

        splits, reason = compute_km_splits(db_conn, tid)
        assert splits is None
        assert "nicht berechenbar" in reason


# ── 12: HF-Zonen zeitgewichtet ──────────────────────────────────────────────

class TestHrZonesTimeWeighted:
    def test_time_weighted_not_point_weighted(self, db_conn, cleanup_rows):
        tid = _insert_training(db_conn, distance_km=5.0, duration_min=20)
        cleanup_rows["training_ids"].append(tid)
        # Viele dicht getaktete Punkte in Zone 1 (kurze Zeitspanne), wenige,
        # aber zeitlich weit auseinanderliegende Punkte in Zone 5 (lange Zeitspanne).
        # Eine reine Punktanzahl-Zaehlung wuerde Zone 1 klar bevorzugen — die
        # zeitgewichtete Berechnung muss die tatsaechliche Dauer je Zone abbilden.
        points = []
        for i in range(50):
            points.append({"distance_m": i, "elapsed_s": i * 1.0, "heart_rate": 100})  # Zone1: 0-49s
        for i in range(3):
            points.append({"distance_m": 100 + i, "elapsed_s": 50 + i * 100, "heart_rate": 210})  # Zone5: 50-250s
        _insert_stream(db_conn, tid, points)

        cur = db_conn.cursor()
        cur.execute(
            "INSERT INTO athlete_profile (name, hr_zone_method, hr_zones) VALUES (%s, %s, %s) RETURNING id",
            (f"{TEST_TAG}profile", "test", Json({
                "z1": {"min": 0, "max": 120}, "z2": {"min": 121, "max": 150},
                "z3": {"min": 151, "max": 170}, "z4": {"min": 171, "max": 190}, "z5": {"min": 191, "max": 220},
            })),
        )
        temp_profile_id = cur.fetchone()[0]
        db_conn.commit()
        try:
            zones = build_hr_zones(db_conn, tid)
            z1 = next(z for z in zones["zones"] if z["zone"] == 1)
            z5 = next(z for z in zones["zones"] if z["zone"] == 5)
            # Zone 5 deckt ~200s ab, Zone 1 nur ~49s -> Zone 5 muss trotz
            # weniger Punkten den groesseren Zeitanteil haben.
            assert z5["duration_s"] > z1["duration_s"]
        finally:
            db_conn.cursor().execute("DELETE FROM athlete_profile WHERE id = %s", (temp_profile_id,))
            db_conn.commit()


# ── 13: GPS-Route + Bounds ──────────────────────────────────────────────────

class TestRoute:
    def test_route_returns_coordinates_and_bounds(self, db_conn, cleanup_rows):
        tid = _insert_training(db_conn, distance_km=1.0)
        cleanup_rows["training_ids"].append(tid)
        points = [
            {"distance_m": 0, "lat": 47.40, "lon": 7.55},
            {"distance_m": 500, "lat": 47.41, "lon": 7.56},
            {"distance_m": 1000, "lat": 47.42, "lon": 7.57},
        ]
        _insert_stream(db_conn, tid, points)

        route = build_route(db_conn, tid)
        assert route["available"] is True
        assert len(route["coordinates"]) == 3
        assert route["coordinates"][0] == [7.55, 47.4]  # [lon, lat] Reihenfolge
        assert route["bounds"]["min_lat"] == 47.40
        assert route["bounds"]["max_lat"] == 47.42

    def test_no_gps_returns_unavailable_not_fake_route(self, db_conn, cleanup_rows):
        tid = _insert_training(db_conn, distance_km=1.0)
        cleanup_rows["training_ids"].append(tid)
        route = build_route(db_conn, tid)
        assert route["available"] is False
        assert route["coordinates"] is None


# ── 14: Fehlende Werte bleiben null ──────────────────────────────────────────

class TestMissingValuesStayNull:
    def test_activity_without_any_details_has_null_fields(self, db_conn, cleanup_rows):
        tid = _insert_training(db_conn, distance_km=None, duration_min=None)
        cleanup_rows["training_ids"].append(tid)
        result = get_activity_analysis_data(activity_id=tid, include_stream=False)
        s = result["summary"]
        assert s["max_hr"] is None
        assert s["avg_power"] is None
        assert s["normalized_power"] is None
        assert s["calories"] is None
        assert result["route"]["available"] is False


# ── 15+16: Backfill bestehender Aktivitaeten, keine Duplikate ─────────────

class TestBackfillExistingActivity:
    def test_find_existing_by_garmin_id(self, db_conn, cleanup_rows):
        tid = _insert_training(db_conn, garmin_id=f"{TEST_TAG}999999")
        cleanup_rows["training_ids"].append(tid)
        cur = db_conn.cursor()
        found = sync_job._find_existing_training_id(cur, f"{TEST_TAG}999999", "2026-01-01T10:00:00", "Run", 60)
        assert found == tid

    def test_backfill_adds_missing_native_laps(self, db_conn, cleanup_rows, monkeypatch):
        # garmin_id muss numerisch sein (int(garmin_id) in _backfill_missing_details,
        # wie bei jeder echten Garmin activityId) — kein TEST_TAG-Praefix hier.
        garmin_id = "9990001"
        tid = _insert_training(db_conn, distance_km=5.0, garmin_id=garmin_id)
        cleanup_rows["training_ids"].append(tid)
        cur = db_conn.cursor()

        called = {}
        def fake_import_splits(client, training_id, garmin_id, force=False):
            called["training_id"] = training_id
            _insert_split(db_conn, training_id, split_number=1, distance_km=5.0, pace_seconds=300)

        monkeypatch.setattr("data.garmin_import_splits.import_splits_for_activity", fake_import_splits)
        added = sync_job._backfill_missing_details(cur, client=object(), training_id=tid,
                                                     garmin_id=garmin_id, activity_type="WeightTraining")
        assert "native_laps" in added
        assert called["training_id"] == tid

    def test_backfill_skips_when_already_present(self, db_conn, cleanup_rows, monkeypatch):
        garmin_id = "9990002"
        tid = _insert_training(db_conn, distance_km=5.0, garmin_id=garmin_id)
        cleanup_rows["training_ids"].append(tid)
        _insert_split(db_conn, tid, split_number=1, distance_km=5.0, pace_seconds=300)
        cur = db_conn.cursor()

        called = {"n": 0}
        def fake_import_splits(*a, **k):
            called["n"] += 1
        monkeypatch.setattr("data.garmin_import_splits.import_splits_for_activity", fake_import_splits)

        added = sync_job._backfill_missing_details(cur, client=object(), training_id=tid,
                                                     garmin_id=garmin_id, activity_type="WeightTraining")
        assert "native_laps" not in added
        assert called["n"] == 0


# ── 17: Datenqualitaet meldet fehlende Kategorien ───────────────────────────

class TestDataQuality:
    def test_reports_missing_categories(self):
        dq = build_data_quality(
            native_laps=[], km_splits=None, stream_available=False,
            hr_stream_available=False, pace_stream_available=False,
            elevation_stream_available=False, gps_route_available=False,
            power_available=False, hr_zones_available=False,
            summary={"distance_km": 10, "duration_min": 60, "avg_hr": None, "max_hr": None},
            hr_stream_point_count=0,
        )
        assert dq["native_laps_available"] is False
        assert dq["gps_route_available"] is False
        assert any("GPS" in w for w in dq["warnings"])

    def test_ascent_without_descent_flagged(self):
        dq = build_data_quality(
            native_laps=[{"lap_number": 1}], km_splits=None, stream_available=True,
            hr_stream_available=True, pace_stream_available=True, elevation_stream_available=True,
            gps_route_available=True, power_available=False, hr_zones_available=True,
            summary={"distance_km": 15.17, "duration_min": 109, "elevation_gain_m": 519, "elevation_loss_m": 0,
                     "avg_hr": 141, "max_hr": 150},
            hr_stream_point_count=1000,
        )
        assert any("Aufstieg" in w and "Abstieg" in w for w in dq["warnings"])


# ── 18+19: Coach-Analyse Versionierung, Idempotenz, Stale ──────────────────

class TestCoachAnalysisPersistence:
    def test_versioned_and_idempotent_save(self, db_conn, cleanup_rows):
        tid = _insert_training(db_conn, distance_km=10.0, duration_min=50)
        cleanup_rows["training_ids"].append(tid)
        current = get_activity_analysis_data(activity_id=tid, include_stream=False)
        h = current["source_data_hash"]

        analysis = {"verdict": "V1", "summary": "S1", "positive_findings": [], "limitations": []}
        save1 = save_activity_coach_analysis(activity_id=tid, source_data_hash=h, analysis=analysis)
        assert save1["saved"] is True and save1["version"] == 1

        save2 = save_activity_coach_analysis(activity_id=tid, source_data_hash=h, analysis=analysis)
        assert save2["idempotent"] is True
        assert save2["version"] == 1

        analysis_v2 = {**analysis, "verdict": "V2 revised"}
        save3 = save_activity_coach_analysis(activity_id=tid, source_data_hash=h, analysis=analysis_v2)
        assert save3["idempotent"] is False
        assert save3["version"] == 2

        cur = db_conn.cursor()
        cur.execute("SELECT COUNT(*) FROM activity_analyses WHERE training_id=%s", (tid,))
        assert cur.fetchone()[0] == 2, "Alte Version darf nicht geloescht werden"

    def test_changed_source_data_marks_stale(self, db_conn, cleanup_rows):
        tid = _insert_training(db_conn, distance_km=10.0, duration_min=50)
        cleanup_rows["training_ids"].append(tid)
        current = get_activity_analysis_data(activity_id=tid, include_stream=False)
        save_activity_coach_analysis(
            activity_id=tid, source_data_hash=current["source_data_hash"],
            analysis={"verdict": "V1", "summary": "S1"},
        )
        status1 = get_activity_analysis_status(tid)
        assert status1["status"] == "fresh"

        db_conn.cursor().execute("UPDATE trainings SET distance_km = 99.9 WHERE id = %s", (tid,))
        db_conn.commit()

        status2 = get_activity_analysis_status(tid)
        assert status2["status"] == "stale"

    def test_hash_mismatch_rejects_save(self, db_conn, cleanup_rows):
        tid = _insert_training(db_conn, distance_km=10.0)
        cleanup_rows["training_ids"].append(tid)
        result = save_activity_coach_analysis(
            activity_id=tid, source_data_hash="wrong-hash-value",
            analysis={"verdict": "V1"},
        )
        assert "error" in result


# ── 21: Frontend und MCP nutzen denselben Gear-Datensatz ───────────────────

class TestFrontendSharesGearLogic:
    def test_flask_route_calls_same_assign_function(self):
        import coach.api as api_module
        import inspect
        src = inspect.getsource(api_module.assign_gear_for_frontend)
        assert "assign_activity_gear" in src, \
            "Das Flask-Gear-Endpoint muss dieselbe assign_activity_gear-Funktion wie das MCP-Tool nutzen"
