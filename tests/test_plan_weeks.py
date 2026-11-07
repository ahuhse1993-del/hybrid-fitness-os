"""
tests/test_plan_weeks.py
plan_weeks muss aus ALLEN Sessions eines Plans abgeleitet werden, mit
Wochennummer = Abstand zum Planstart. Regression (2026-11-07): ein Teil-Upsert
mit nur den Qualitätseinheiten nummerierte seine Wochen ab 1 durch und
überschrieb dadurch die echten Wochen 1–16 mit falschem Datum und falschen km.

Läuft gegen die Dev-DB, aber nur auf einem eigenen Wegwerf-Plan
(status='archived', wird nie zum aktiven Plan) — der echte Plan bleibt unberührt.
"""
import datetime
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from coach.mcp_server import _rebuild_plan_weeks
from database.connection import get_connection

TEST_PREFIX = "test-pw-"
START = datetime.date(2030, 1, 7)  # Montag, weit weg von echten Plänen


@pytest.fixture
def conn():
    c = get_connection()
    yield c
    c.close()


@pytest.fixture
def plan_id(conn):
    cur = conn.cursor()
    cur.execute("INSERT INTO plans (name, status) VALUES (%s, 'archived') RETURNING id",
                (f"{TEST_PREFIX}plan",))
    pid = cur.fetchone()[0]
    conn.commit()
    yield pid
    cur = conn.cursor()
    cur.execute("DELETE FROM training_plan WHERE plan_id = %s", (pid,))
    cur.execute("DELETE FROM plans WHERE id = %s", (pid,))
    conn.commit()


def _add_session(conn, pid, week_idx, dow, stype, km, zone="Z2"):
    wstart = START + datetime.timedelta(weeks=week_idx)
    cur = conn.cursor()
    cur.execute(
        # phase explizit NULL wie bei echten Plan-Sessions (Spalten-Default ist 'base').
        """INSERT INTO training_plan (plan_id, week_date, day_of_week, session_type, session_zone,
                                      distance_km, external_id, status, phase)
           VALUES (%s, %s, %s, %s, %s, %s, %s, 'planned', NULL)""",
        (pid, wstart, dow, stype, zone, km, f"{TEST_PREFIX}{pid}-{week_idx}-{dow}"),
    )


def _weeks(conn, pid):
    cur = conn.cursor()
    cur.execute("SELECT week_number, week_start, target_run_km FROM plan_weeks "
                "WHERE plan_id = %s ORDER BY week_number", (pid,))
    return [(n, s, float(k) if k is not None else None) for n, s, k in cur.fetchall()]


def test_partial_call_does_not_renumber_weeks(conn, plan_id):
    # 6 Wochen à Easy 5 + Quality 8 + Strength.
    for w in range(6):
        _add_session(conn, plan_id, w, 1, "Easy Run", 5)
        _add_session(conn, plan_id, w, 3, "Interval Run", 8, zone="interval")
        _add_session(conn, plan_id, w, 2, "Strength Training", 0, zone=None)
    cur = conn.cursor()
    all_starts = {START + datetime.timedelta(weeks=w) for w in range(6)}
    _rebuild_plan_weeks(cur, plan_id, all_starts, [])
    conn.commit()

    # Teil-Aufruf nur mit Woche 1 und 5 (Index 0 und 4).
    partial = {START, START + datetime.timedelta(weeks=4)}
    total = _rebuild_plan_weeks(cur, plan_id, partial, [])
    conn.commit()

    weeks = _weeks(conn, plan_id)
    assert total == 6
    assert [w[0] for w in weeks] == [1, 2, 3, 4, 5, 6]
    assert [w[1] for w in weeks] == [START + datetime.timedelta(weeks=i) for i in range(6)]
    assert all(w[2] == 13.0 for w in weeks), "km = alle Lauf-Sessions der Woche, ohne Kraft"


def test_repairs_wrongly_numbered_rows_and_fills_gaps(conn, plan_id):
    for w in range(5):
        _add_session(conn, plan_id, w, 1, "Easy Run", 10 + w)
    cur = conn.cursor()
    # Kaputter Zustand wie in Produktion: Woche 2 zeigt aufs Datum von Woche 4.
    cur.execute("INSERT INTO plan_weeks (plan_id, week_number, week_start, phase, is_deload, is_peak, target_run_km) "
                "VALUES (%s, 1, %s, 'tempo', false, false, 3), (%s, 2, %s, 'tempo', false, false, 4)",
                (plan_id, START, plan_id, START + datetime.timedelta(weeks=3)))
    _rebuild_plan_weeks(cur, plan_id, set(), [], keep_existing_meta=False)
    conn.commit()

    weeks = _weeks(conn, plan_id)
    assert [(w[0], w[1], w[2]) for w in weeks] == [
        (i + 1, START + datetime.timedelta(weeks=i), float(10 + i)) for i in range(5)
    ]
    cur.execute("SELECT DISTINCT phase FROM plan_weeks WHERE plan_id = %s", (plan_id,))
    assert cur.fetchall() == [("Z2",)]
    cur.execute("SELECT COUNT(*) FROM training_plan tp JOIN plan_weeks pw ON pw.id = tp.week_id "
                "WHERE tp.plan_id = %s AND pw.week_start = tp.week_date", (plan_id,))
    assert cur.fetchone()[0] == 5, "Sessions hängen an der passenden Woche"


def test_week_meta_uses_absolute_week_number(conn, plan_id):
    for w in range(4):
        _add_session(conn, plan_id, w, 1, "Easy Run", 6)
    cur = conn.cursor()
    _rebuild_plan_weeks(cur, plan_id, {START + datetime.timedelta(weeks=w) for w in range(4)}, [])
    # Teil-Aufruf nur für Woche 3, mit Meta für week_number 3.
    _rebuild_plan_weeks(cur, plan_id, {START + datetime.timedelta(weeks=2)},
                        [{"week_number": 3, "phase": "build", "week_focus": "Fokus W3"}])
    conn.commit()
    cur.execute("SELECT week_number, phase, week_focus FROM plan_weeks WHERE plan_id = %s "
                "ORDER BY week_number", (plan_id,))
    rows = cur.fetchall()
    assert rows[2] == (3, "build", "Fokus W3")
    assert rows[0][1] == "Z2"
