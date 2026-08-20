#!/usr/bin/env python3
"""
Patch: Adds `name` field to both /api/frontend/week and /api/frontend/week/all
so the frontend can display ChatGPT-assigned session names instead of session_type.

Run from repo root: python patch_api_name.py
"""

PATH = "coach/api.py"
with open(PATH, "r") as f:
    src = f.read()

# ─── PATCH 1: frontend_week — SELECT ─────────────────────────────────────────
OLD1 = '''            SELECT id,
                   (week_date + (day_of_week - 1) * INTERVAL '1 day')::date AS session_date,
                   session_type, session_zone, distance_km, duration_min,
                   notes, phase, garmin_workout_id, workout_steps
            FROM training_plan
            WHERE week_date >= %s AND week_date <= %s
            ORDER BY week_date, day_of_week'''

NEW1 = '''            SELECT id,
                   (week_date + (day_of_week - 1) * INTERVAL '1 day')::date AS session_date,
                   session_type, session_zone, distance_km, duration_min,
                   notes, phase, garmin_workout_id, workout_steps, name
            FROM training_plan
            WHERE week_date >= %s AND week_date <= %s
            ORDER BY week_date, day_of_week'''

assert OLD1 in src, "PATCH 1 (frontend_week SELECT) nicht gefunden"
src = src.replace(OLD1, NEW1, 1)

# ─── PATCH 2: frontend_week — session_obj ────────────────────────────────────
OLD2 = '''            sessions.append({
                "id": r[0],
                "date": d_str,
                "day_label": DAY_DE[session_date.weekday()],
                "day_number": session_date.day,
                "month_label": MONTH_DE[session_date.month],
                "is_today": session_date == today,
                "is_past": session_date < today,
                "session_type": session_type,'''

NEW2 = '''            sessions.append({
                "id": r[0],
                "date": d_str,
                "day_label": DAY_DE[session_date.weekday()],
                "day_number": session_date.day,
                "month_label": MONTH_DE[session_date.month],
                "is_today": session_date == today,
                "is_past": session_date < today,
                "name": r[10] or "",
                "session_type": session_type,'''

assert OLD2 in src, "PATCH 2 (frontend_week session_obj) nicht gefunden"
src = src.replace(OLD2, NEW2, 1)

# ─── PATCH 3: frontend_week_all — SELECT ─────────────────────────────────────
OLD3 = '''            SELECT id,
                   (week_date + (day_of_week - 1) * INTERVAL '1 day')::date AS session_date,
                   week_date,
                   session_type, session_zone, distance_km, duration_min,
                   notes, phase, garmin_workout_id, workout_steps,
                   sync_status, elevation_gain_m, sport,
                   km_factor, actual_distance_km, linked_garmin_activity_id
            FROM training_plan
            ORDER BY week_date, day_of_week'''

NEW3 = '''            SELECT id,
                   (week_date + (day_of_week - 1) * INTERVAL '1 day')::date AS session_date,
                   week_date,
                   session_type, session_zone, distance_km, duration_min,
                   notes, phase, garmin_workout_id, workout_steps,
                   sync_status, elevation_gain_m, sport,
                   km_factor, actual_distance_km, linked_garmin_activity_id, name
            FROM training_plan
            ORDER BY week_date, day_of_week'''

assert OLD3 in src, "PATCH 3 (frontend_week_all SELECT) nicht gefunden"
src = src.replace(OLD3, NEW3, 1)

# ─── PATCH 4: frontend_week_all — session_obj ────────────────────────────────
OLD4 = '''            session_obj = {
                "id": r[0],
                "date": d_str,
                "day_number": session_date.day,
                "day_short": DAY_SHORT[session_date.weekday()],
                "month_short": MONTH_SHORT[session_date.month],
                "is_today": session_date == today,
                "is_past": session_date < today,
                "session_type": session_type,'''

NEW4 = '''            session_obj = {
                "id": r[0],
                "date": d_str,
                "day_number": session_date.day,
                "day_short": DAY_SHORT[session_date.weekday()],
                "month_short": MONTH_SHORT[session_date.month],
                "is_today": session_date == today,
                "is_past": session_date < today,
                "name": r[17] or "",
                "session_type": session_type,'''

assert OLD4 in src, "PATCH 4 (frontend_week_all session_obj) nicht gefunden"
src = src.replace(OLD4, NEW4, 1)

with open(PATH, "w") as f:
    f.write(src)

print("✓ coach/api.py gepatcht — 'name' in /api/frontend/week und /api/frontend/week/all")
print()
print("Nächster Schritt:")
print("  git add coach/api.py files/cairn_app_v6.html")
print("  git commit -m 'fix: session name in API + icon mapping + step visualization for new types'")
print("  git push")
