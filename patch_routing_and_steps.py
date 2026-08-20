#!/usr/bin/env python3
"""
Fixes:
  1. session_routing.py — mehr Garmin-fähige Typen + Cycling als eigener Typ
  2. mcp_server.py — workout_steps in upsert_training_block SESSION-INSERT

Run from repo root: python patch_routing_and_steps.py
"""

# ─── PATCH 1: session_routing.py ─────────────────────────────────────────────
PATH1 = "coach/session_routing.py"
with open(PATH1, "r") as f:
    src1 = f.read()

OLD1 = '''GARMIN_RUNNING_TYPES = frozenset({
    "Easy Run", "Trail Run", "Recovery Run", "Long Run",
    "Tempo Session", "Interval Session", "Sprint Session", "Hill Session",
    "Race Day", "Race",
})

# 'Cross Training' selbst ist NICHT automatisch Rad — siehe Docstring oben.
GARMIN_ELIGIBLE_CROSS_TYPES = frozenset({"Cross Training"})'''

NEW1 = '''GARMIN_RUNNING_TYPES = frozenset({
    # Basis-Typen
    "Easy Run", "Trail Run", "Recovery Run", "Long Run",
    "Tempo Session", "Interval Session", "Sprint Session", "Hill Session",
    "Race Day", "Race",
    # Erweiterte Typen (ChatGPT-kompatibel)
    "Interval Run", "Interval Training",
    "Threshold Run", "Trail Threshold", "Uphill Threshold",
    "Hill Technique", "Hill Run",
    "Race Activation", "Activation Run",
    "Fartlek", "Strides",
})

# Explizite Rad-Typen (garmin_sport=cycling)
GARMIN_CYCLING_TYPES = frozenset({"Cycling", "Bike", "Road Bike", "MTB", "E-Bike"})

# 'Cross Training' selbst ist NICHT automatisch Rad — siehe Docstring oben.
GARMIN_ELIGIBLE_CROSS_TYPES = frozenset({"Cross Training"})'''

assert OLD1 in src1, "PATCH 1 nicht gefunden"
src1 = src1.replace(OLD1, NEW1, 1)

# Cycling-Routing einfügen — vor dem GARMIN_RUNNING_TYPES Check
OLD1B = '''    if session_type in GARMIN_RUNNING_TYPES:
        return {
            "sync_target": "garmin", "garmin_sport": "running", "garmin_push_required": True,
            "source": None, "reason": f"{session_type!r} ist eine Laufeinheit — Garmin-Push (running).",
        }

    if session_type in GARMIN_ELIGIBLE_CROSS_TYPES:'''

NEW1B = '''    if session_type in GARMIN_CYCLING_TYPES:
        return {
            "sync_target": "garmin", "garmin_sport": "cycling", "garmin_push_required": True,
            "source": None, "reason": f"{session_type!r} ist eine Radeinheit — Garmin-Push (cycling).",
        }

    if session_type in GARMIN_RUNNING_TYPES:
        return {
            "sync_target": "garmin", "garmin_sport": "running", "garmin_push_required": True,
            "source": None, "reason": f"{session_type!r} ist eine Laufeinheit — Garmin-Push (running).",
        }

    if session_type in GARMIN_ELIGIBLE_CROSS_TYPES:'''

assert OLD1B in src1, "PATCH 1B nicht gefunden"
src1 = src1.replace(OLD1B, NEW1B, 1)

with open(PATH1, "w") as f:
    f.write(src1)

print("✓ coach/session_routing.py gepatcht (Interval Run, Threshold Run, Cycling, etc.)")

# ─── PATCH 2: mcp_server.py — workout_steps in upsert_training_block ─────────
PATH2 = "coach/mcp_server.py"
with open(PATH2, "r") as f:
    src2 = f.read()

OLD2 = '''                cur.execute(
                    """INSERT INTO training_plan
                           (external_id, week_date, day_of_week, session_type, session_zone,
                            phase, distance_km, duration_min, notes, plan_id,
                            status, source, garmin_push_required, hevy_routine_key,
                            sport, sync_target, name, target, elevation_gain_m, km_factor)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (external_id) DO UPDATE SET
                           week_date=EXCLUDED.week_date, day_of_week=EXCLUDED.day_of_week,
                           session_type=EXCLUDED.session_type, session_zone=EXCLUDED.session_zone,
                           phase=EXCLUDED.phase,
                           distance_km=EXCLUDED.distance_km, duration_min=EXCLUDED.duration_min,
                           notes=EXCLUDED.notes, plan_id=EXCLUDED.plan_id,
                           source=EXCLUDED.source, garmin_push_required=EXCLUDED.garmin_push_required,
                           hevy_routine_key=EXCLUDED.hevy_routine_key,
                           sport=EXCLUDED.sport, sync_target=EXCLUDED.sync_target,
                           name=EXCLUDED.name, target=EXCLUDED.target,
                           elevation_gain_m=EXCLUDED.elevation_gain_m,
                           km_factor=EXCLUDED.km_factor, updated_at=now()
                       RETURNING id, (xmax = 0) AS inserted""",
                    (
                        s["external_id"], week_date, day_of_week, s["session_type"],
                        s.get("session_zone"), s.get("phase"),
                        s.get("distance_km"), s.get("duration_min"),
                        s.get("notes"), plan_id,
                        s.get("status", "planned"), routing["source"],
                        routing["garmin_push_required"], hevy_routine_key,
                        resolved_sport, routing["sync_target"], s.get("name"),
                        json.dumps(s["target"]) if s.get("target") is not None else None,
                        s.get("elevation_gain_m"), s.get("km_factor"),
                    ),
                )'''

NEW2 = '''                # workout_steps: akzeptiert "workout_steps" oder "structure" als Schlüssel
                raw_steps = s.get("workout_steps") or s.get("structure")
                steps_json = json.dumps(raw_steps) if raw_steps is not None else None

                cur.execute(
                    """INSERT INTO training_plan
                           (external_id, week_date, day_of_week, session_type, session_zone,
                            phase, distance_km, duration_min, notes, plan_id,
                            status, source, garmin_push_required, hevy_routine_key,
                            sport, sync_target, name, target, elevation_gain_m, km_factor,
                            workout_steps)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (external_id) DO UPDATE SET
                           week_date=EXCLUDED.week_date, day_of_week=EXCLUDED.day_of_week,
                           session_type=EXCLUDED.session_type, session_zone=EXCLUDED.session_zone,
                           phase=EXCLUDED.phase,
                           distance_km=EXCLUDED.distance_km, duration_min=EXCLUDED.duration_min,
                           notes=EXCLUDED.notes, plan_id=EXCLUDED.plan_id,
                           source=EXCLUDED.source, garmin_push_required=EXCLUDED.garmin_push_required,
                           hevy_routine_key=EXCLUDED.hevy_routine_key,
                           sport=EXCLUDED.sport, sync_target=EXCLUDED.sync_target,
                           name=EXCLUDED.name, target=EXCLUDED.target,
                           elevation_gain_m=EXCLUDED.elevation_gain_m,
                           km_factor=EXCLUDED.km_factor,
                           workout_steps=EXCLUDED.workout_steps,
                           updated_at=now()
                       RETURNING id, (xmax = 0) AS inserted""",
                    (
                        s["external_id"], week_date, day_of_week, s["session_type"],
                        s.get("session_zone"), s.get("phase"),
                        s.get("distance_km"), s.get("duration_min"),
                        s.get("notes"), plan_id,
                        s.get("status", "planned"), routing["source"],
                        routing["garmin_push_required"], hevy_routine_key,
                        resolved_sport, routing["sync_target"], s.get("name"),
                        json.dumps(s["target"]) if s.get("target") is not None else None,
                        s.get("elevation_gain_m"), s.get("km_factor"),
                        steps_json,
                    ),
                )'''

assert OLD2 in src2, "PATCH 2 nicht gefunden — mcp_server.py Version prüfen"
src2 = src2.replace(OLD2, NEW2, 1)

with open(PATH2, "w") as f:
    f.write(src2)

print("✓ coach/mcp_server.py gepatcht (workout_steps in upsert_training_block)")
print()
print("Nächster Schritt:")
print("  git add coach/session_routing.py coach/mcp_server.py")
print("  git commit -m 'fix: Interval Run routing, Cycling-Typ, workout_steps in block import'")
print("  git push")
