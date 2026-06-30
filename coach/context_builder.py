"""
CAIRN Context Builder
Pulls all relevant athlete data from the DB for a given coaching task.
Knowledge (philosophy) + Context (data) = Coaching.
"""

import os
import psycopg2
from datetime import date, timedelta, datetime


WEEKDAYS_DE = ['Montag', 'Dienstag', 'Mittwoch', 'Donnerstag', 'Freitag', 'Samstag', 'Sonntag']


def get_db():
    database_url = os.getenv("RAILWAY_DATABASE_URL") or os.getenv("DATABASE_URL")
    return psycopg2.connect(database_url)


def get_today():
    try:
        import pytz
        zurich = pytz.timezone('Europe/Zurich')
        return datetime.now(zurich).date()
    except Exception:
        return (datetime.utcnow() + timedelta(hours=2)).date()


def weekday_name(d) -> str:
    """Gibt den deutschen Wochentagsnamen für ein date-Objekt zurück."""
    return WEEKDAYS_DE[d.weekday()]


def build_morning_brief_context() -> dict:
    """
    Builds the full context for the morning brief.
    Returns a dict with all relevant data.
    """
    today = get_today()
    conn = get_db()
    cur = conn.cursor()

    # ── Recovery / Health ──
    cur.execute("""
        SELECT date, hrv_last_night, sleep_duration_h, resting_hr,
               body_battery_charged, feel, notes, athlete_text
        FROM daily_logs
        WHERE date >= %s
        ORDER BY date DESC
        LIMIT 3
    """, (today - timedelta(days=3),))
    health_rows = cur.fetchall()

    today_health = None
    recent_health = []
    for row in health_rows:
        entry = {
            "date": str(row[0]),
            "weekday": weekday_name(row[0]),
            "hrv": row[1],
            "sleep_h": float(row[2]) if row[2] else None,
            "rhr": row[3],
            "body_battery": row[4],
            "feel": row[5],
            "notes": row[6],
            "athlete_text": row[7],
        }
        if row[0] == today:
            today_health = entry
        else:
            recent_health.append(entry)

    # ── Planned workout today ──
    monday = today - timedelta(days=today.weekday())
    day_of_week = today.isoweekday()  # Mo=1...So=7

    cur.execute("""
        SELECT session_type, session_zone, duration_min, distance_km, notes
        FROM training_plan
        WHERE week_date = %s AND day_of_week = %s
        LIMIT 1
    """, (monday, day_of_week))
    plan_row = cur.fetchone()

    planned_workout = None
    if plan_row:
        planned_workout = {
            "session_type": plan_row[0],
            "session_zone": plan_row[1],
            "duration_min": plan_row[2],
            "distance_km": float(plan_row[3]) if plan_row[3] else None,
            "notes": plan_row[4],
        }

    # ── Recent trainings (last 7 days) ──
    cur.execute("""
        SELECT date, type, notes, duration_minutes, distance_km, heart_rate_avg
        FROM trainings
        WHERE date >= %s AND date <= %s
        ORDER BY date DESC
        LIMIT 7
    """, (today - timedelta(days=7), today))
    training_rows = cur.fetchall()

    recent_trainings = []
    for row in training_rows:
        recent_trainings.append({
            "date": str(row[0]),
            "weekday": weekday_name(row[0]),
            "type": row[1],
            "name": row[2] or row[1],
            "duration_min": row[3],
            "distance_km": float(row[4]) if row[4] else 0,
            "avg_hr": row[5],
        })

    # ── Week summary ──
    cur.execute("""
        SELECT COALESCE(SUM(distance_km), 0), COUNT(*)
        FROM trainings
        WHERE date >= %s AND date <= %s
        AND type NOT IN ('WeightTraining', 'Strength')
    """, (monday, today))
    week_row = cur.fetchone()
    week_km = round(float(week_row[0]), 1) if week_row[0] else 0
    week_sessions = int(week_row[1]) if week_row[1] else 0

    conn.close()

    return {
        "today": str(today),
        "today_weekday": weekday_name(today),
        "athlete": {
            "name": "Alexander",
            "type": "Hybrid athlete — Trail Running + Strength Training",
        },
        "today_health": today_health,
        "recent_health": recent_health,
        "planned_workout": planned_workout,
        "recent_trainings": recent_trainings,
        "week_summary": {
            "km_done": week_km,
            "sessions_done": week_sessions,
        },
    }


def format_context_for_prompt(context: dict) -> str:
    """
    Formats the context dict into a clean text block for the prompt.
    """
    lines = []
    lines.append(f"## Athlete\nName: {context['athlete']['name']}\nType: {context['athlete']['type']}")
    lines.append(f"## Date\n{context['today']} ({context.get('today_weekday', '')})")

    # Health today
    h = context.get('today_health')
    if h:
        lines.append("## Today's Health Data")
        if h.get('hrv'): lines.append(f"HRV: {h['hrv']} ms")
        if h.get('sleep_h'): lines.append(f"Sleep: {h['sleep_h']} h")
        if h.get('rhr'): lines.append(f"Resting HR: {h['rhr']} bpm")
        if h.get('body_battery'): lines.append(f"Body Battery: {h['body_battery']}")
        if h.get('feel'): lines.append(f"Athlete feel (1-10): {h['feel']}")
        if h.get('notes'): lines.append(f"Athlete notes: {h['notes']}")
        if h.get('athlete_text'): lines.append(f"Athlete message: {h['athlete_text']}")
    else:
        lines.append("## Today's Health Data\nNot yet available.")

    # Recent health
    if context.get('recent_health'):
        lines.append("## Recent Health (last 3 days)")
        for rh in context['recent_health']:
            parts = [f"{rh['date']} ({rh.get('weekday','')})"]
            if rh.get('hrv'): parts.append(f"HRV {rh['hrv']} ms")
            if rh.get('sleep_h'): parts.append(f"Sleep {rh['sleep_h']} h")
            if rh.get('rhr'): parts.append(f"RHR {rh['rhr']} bpm")
            lines.append("  " + " | ".join(parts))

    # Planned workout
    pw = context.get('planned_workout')
    if pw:
        lines.append("## Planned Workout Today")
        lines.append(f"Type: {pw['session_type']}")
        if pw.get('session_zone'): lines.append(f"Zone: {pw['session_zone']}")
        if pw.get('distance_km'): lines.append(f"Distance: {pw['distance_km']} km")
        if pw.get('duration_min'): lines.append(f"Duration: {pw['duration_min']} min")
        if pw.get('notes'): lines.append(f"Notes: {pw['notes']}")
    else:
        lines.append("## Planned Workout Today\nRest Day or no plan found.")

    # Recent trainings
    if context.get('recent_trainings'):
        lines.append("## Recent Trainings (last 7 days) — use the weekday name shown, do not calculate it yourself")
        for t in context['recent_trainings']:
            parts = [f"{t['date']} ({t.get('weekday','')})", t['type']]
            if t.get('distance_km'): parts.append(f"{t['distance_km']} km")
            if t.get('duration_min'): parts.append(f"{t['duration_min']} min")
            if t.get('avg_hr'): parts.append(f"HF {t['avg_hr']} bpm")
            lines.append("  " + " | ".join(str(p) for p in parts))

    # Week summary
    ws = context.get('week_summary', {})
    lines.append(f"## Week Summary\nKm done: {ws.get('km_done', 0)} | Sessions: {ws.get('sessions_done', 0)}")

    return "\n".join(lines)