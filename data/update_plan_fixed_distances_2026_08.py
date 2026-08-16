"""
CAIRN — Einmaliges Update-Skript (2026-08-16)
Ersetzt Distanzbereiche im aktiven Trainingsblock durch feste Laufdistanzen,
setzt den Einheitsnamen (training_plan.name, bisher bei allen Sessions leer)
und hinterlegt echte workout_steps fuer sechs strukturierte Einheiten.
Idempotent ueber external_id (UPDATE ... WHERE external_id = %s), erfindet
keine neuen Sessions.

Allgemeine Rundungsregel fuer zukuenftige Plaene (Referenz, hier nur
dokumentiert — nicht in data/generate_plan.py verdrahtet):
    fixed_distance_km = math.ceil((minimum_km + maximum_km) / 2)

Beruehrt NUR die lokale CAIRN-DB (training_plan). Kein Garmin-Zugriff, kein
push_sessions_to_garmin-Aufruf — das produktive Re-Sync passiert separat.

Wenn eine Session bereits zu Garmin gepusht war (garmin_workout_id gesetzt,
sync_status='synced') UND sich der Inhalt durch dieses Update tatsaechlich
aendert, wird sync_status='dirty' gesetzt und content_hash neu berechnet —
exakt dieselbe Dirty-Marking-Logik wie in upsert_planned_workout/
upsert_training_block (coach/mcp_server.py), damit push_sessions_to_garmin
diese Sessions beim naechsten Lauf automatisch erneut aufnimmt.

Ausfuehren: python data/update_plan_fixed_distances_2026_08.py
"""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from database.connection import get_connection
from coach.sync_utils import compute_content_hash

# ── Fixe Laufdistanzen (Name, Distanz in km) ────────────────────────────────
# Notizen werden separat unten gepflegt — Bereichsangaben ("Geplanter
# Bereich X-Y km") entfernt, konkrete Distanz eingesetzt.
RUN_UPDATES: dict[str, dict] = {
    "tjb31_2026_20260816_easy_trail": {
        "name": "Easy Trail", "distance_km": 13,
        "notes": "Einheit: Easy Trail. Laufen. 13 km durchgehend locker, Distanz nach Beinen.",
    },
    "tjb31_2026_20260818_easy_trail": {
        "name": "Easy Trail", "distance_km": 8,
        "notes": "Einheit: Easy Trail. Laufen. 8 km durchgehend locker.",
    },
    "tjb31_2026_20260819_hill_threshold": {
        "name": "Hill Threshold", "distance_km": 12,
        "notes": (
            "Einheit: Hill Threshold. Laufen. 12 km: 2 km WU + 4x20 s Strides/40 s locker "
            "+ 5x(4 min Uphill RPE 7-8 / locker bergab) + 2 km CD."
        ),
    },
    "tjb31_2026_20260823_long_trail": {
        "name": "Long Trail", "distance_km": 19,
        "notes": "Einheit: Long Trail. Laufen. 19 km durchgehend locker.",
    },
    "tjb31_2026_20260826_threshold_3x2k": {
        "name": "Threshold 3×2 km", "distance_km": 11,
        "notes": (
            "Einheit: Threshold 3×2 km. Laufen. 11 km: 2 km WU + 4x20 s Strides/40 s locker "
            "+ 3x2 km @ 4:25-4:30 min/km (3 min Trab dazwischen, keine Trabpause nach dem "
            "letzten Intervall) + 2 km CD."
        ),
    },
    "tjb31_2026_20260827_easy_trail": {
        "name": "Easy Trail", "distance_km": 9,
        "notes": "Einheit: Easy Trail. Laufen. 9 km durchgehend locker.",
    },
    "tjb31_2026_20260830_long_trail": {
        "name": "Long Trail", "distance_km": 23,
        "notes": "Einheit: Long Trail. Laufen. 23 km durchgehend locker.",
    },
    "tjb31_2026_20260902_race_specific_uphill": {
        "name": "Race Specific Uphill", "distance_km": 13,
        "notes": (
            "Einheit: Race Specific Uphill. Laufen. 13 km: 2 km WU "
            "+ 4x(5 min Uphill RPE 7-8 / locker bergab bis erholt) + 2 km CD."
        ),
    },
    "tjb31_2026_20260903_easy_trail": {
        "name": "Easy Trail", "distance_km": 8,
        "notes": "Einheit: Easy Trail. Laufen. 8 km durchgehend locker.",
    },
    "tjb31_2026_20260906_peak_long_trail": {
        "name": "Peak Long Trail", "distance_km": 25,
        "notes": "Einheit: Peak Long Trail. Laufen. 25 km technischer Longrun; Race Fueling und Equipment testen.",
    },
    "tjb31_2026_20260909_easy_run": {
        "name": "Easy Run", "distance_km": 7,
        "notes": "Einheit: Easy Run. Laufen. 7 km durchgehend locker.",
    },
    "tjb31_2026_20260913_recovery_run": {
        "name": "Recovery Run", "distance_km": 7,
        "notes": "Einheit: Recovery Run. Laufen. 7 km, optional nur bei guten Beinen.",
    },
    "tjb31_2026_20260916_trail_sharpening": {
        "name": "Trail Sharpening", "distance_km": 9,
        "notes": (
            "Einheit: Trail Sharpening. Laufen. 9 km: 2 km WU "
            "+ 5x(2 min Uphill RPE 7 / 2 min locker) + 2 km CD."
        ),
    },
    "tjb31_2026_20260917_easy_run": {
        "name": "Easy Run", "distance_km": 7,
        "notes": "Einheit: Easy Run. Laufen. 7 km durchgehend locker.",
    },
    "tjb31_2026_20260920_short_long_trail": {
        "name": "Short Long Trail", "distance_km": 13,
        "notes": "Einheit: Short Long Trail. Laufen. 13 km durchgehend locker.",
    },
    "tjb31_2026_20260922_easy_run": {
        "name": "Easy Run", "distance_km": 6,
        "notes": "Einheit: Easy Run. Laufen. 6 km durchgehend locker.",
    },
    "tjb31_2026_20260923_race_primer": {
        "name": "Race Primer", "distance_km": 6,
        "notes": (
            "Einheit: Race Primer. Laufen. 6 km: 2 km WU "
            "+ 4x(30 s flott nach Gefuehl / 90 s locker) + 2 km CD."
        ),
    },
    "tjb31_2026_20260925_shakeout": {
        "name": "Pre-Race Shakeout", "distance_km": 4,
        "notes": (
            "Einheit: Pre-Race Shakeout. Laufen. 4 km: 2 km locker "
            "+ 3x(15 s Stride / 45 s locker) + 1 km CD."
        ),
    },
    "tjb31_2026_20260926_race": {
        "name": "Trail du Jura Bernois", "distance_km": 31,
        "notes": "Einheit: Trail du Jura Bernois. Laufen. 31 km - Race.",
    },
    "tjb31_2026_20261001_recovery_run": {
        "name": "Recovery Run", "distance_km": 6,
        "notes": "Einheit: Recovery Run. Laufen. 6 km, nur bei guter Recovery.",
    },
    "tjb31_2026_20261004_easy_trail": {
        "name": "Easy Trail", "distance_km": 9,
        "notes": "Einheit: Easy Trail. Laufen. 9 km durchgehend locker.",
    },
}

# ── Feste Rennrad-Dauern (Punkt 8) ──────────────────────────────────────────
# 60-75 min -> 70, 75-90 min -> 85, 45-60 min -> 55. Nur Sessions, deren
# Notizen tatsaechlich einen dieser Bereiche nennen — sonst unveraendert.
BIKE_UPDATES: dict[str, dict] = {
    "tjb31_2026_20260821_bike_endurance": {
        "duration_min": 70,
        "notes": "Einheit: Endurance. Rennrad. Durchgehend locker; 70 min.",
    },
    "tjb31_2026_20260901_bike_endurance": {
        "duration_min": 85,
        "notes": "Einheit: Endurance. Rennrad. Gleichmässig; 85 min.",
    },
    "tjb31_2026_20260929_recovery_ride": {
        "duration_min": 55,
        "notes": "Einheit: Recovery Ride. Rennrad. Optional, sehr locker; 55 min.",
    },
}

_NO_TARGET = {"type": "no_target"}


def _stride_block(iterations: int, stride_secs: int, recover_secs: int) -> dict:
    return {
        "type": "repeat", "iterations": iterations,
        "steps": [
            {"type": "interval", "duration_secs": stride_secs, "target": _NO_TARGET, "description": "Stride"},
            {"type": "recovery", "duration_secs": recover_secs, "target": _NO_TARGET, "description": "locker"},
        ],
    }


# ── Strukturierte workout_steps (Punkt 6) ───────────────────────────────────
STRUCTURED_STEPS: dict[str, list] = {
    "tjb31_2026_20260819_hill_threshold": [
        {"type": "warmup", "duration_meters": 2000, "target": _NO_TARGET},
        _stride_block(4, 20, 40),
        {
            "type": "repeat", "iterations": 5,
            "steps": [
                {"type": "interval", "duration_secs": 240, "target": _NO_TARGET, "description": "RPE 7-8"},
                {"type": "recovery", "lap_button": True, "target": _NO_TARGET, "description": "locker bergab"},
            ],
        },
        {"type": "cooldown", "duration_meters": 2000, "target": _NO_TARGET},
    ],
    "tjb31_2026_20260826_threshold_3x2k": [
        {"type": "warmup", "duration_meters": 2000, "target": _NO_TARGET},
        _stride_block(4, 20, 40),
        {
            "type": "interval", "duration_meters": 2000, "enforce_garmin_target": True,
            "target": {"type": "pace_zone", "slow_pace_sec_per_km": 270, "fast_pace_sec_per_km": 265},
            "description": "4:25-4:30 min/km",
        },
        {"type": "recovery", "duration_secs": 180, "target": _NO_TARGET, "description": "Trab"},
        {
            "type": "interval", "duration_meters": 2000, "enforce_garmin_target": True,
            "target": {"type": "pace_zone", "slow_pace_sec_per_km": 270, "fast_pace_sec_per_km": 265},
            "description": "4:25-4:30 min/km",
        },
        {"type": "recovery", "duration_secs": 180, "target": _NO_TARGET, "description": "Trab"},
        {
            "type": "interval", "duration_meters": 2000, "enforce_garmin_target": True,
            "target": {"type": "pace_zone", "slow_pace_sec_per_km": 270, "fast_pace_sec_per_km": 265},
            "description": "4:25-4:30 min/km",
        },
        {"type": "cooldown", "duration_meters": 2000, "target": _NO_TARGET},
    ],
    "tjb31_2026_20260902_race_specific_uphill": [
        {"type": "warmup", "duration_meters": 2000, "target": _NO_TARGET},
        {
            "type": "repeat", "iterations": 4,
            "steps": [
                {"type": "interval", "duration_secs": 300, "target": _NO_TARGET, "description": "RPE 7-8"},
                {"type": "recovery", "lap_button": True, "target": _NO_TARGET, "description": "locker bergab bis erholt"},
            ],
        },
        {"type": "cooldown", "duration_meters": 2000, "target": _NO_TARGET},
    ],
    "tjb31_2026_20260916_trail_sharpening": [
        {"type": "warmup", "duration_meters": 2000, "target": _NO_TARGET},
        {
            "type": "repeat", "iterations": 5,
            "steps": [
                {"type": "interval", "duration_secs": 120, "target": _NO_TARGET, "description": "RPE 7"},
                {"type": "recovery", "duration_secs": 120, "target": _NO_TARGET, "description": "locker"},
            ],
        },
        {"type": "cooldown", "duration_meters": 2000, "target": _NO_TARGET},
    ],
    "tjb31_2026_20260923_race_primer": [
        {"type": "warmup", "duration_meters": 2000, "target": _NO_TARGET},
        {
            "type": "repeat", "iterations": 4,
            "steps": [
                {"type": "interval", "duration_secs": 30, "target": _NO_TARGET, "description": "flott nach Gefuehl"},
                {"type": "recovery", "duration_secs": 90, "target": _NO_TARGET, "description": "locker"},
            ],
        },
        {"type": "cooldown", "duration_meters": 2000, "target": _NO_TARGET},
    ],
    "tjb31_2026_20260925_shakeout": [
        {"type": "warmup", "duration_meters": 2000, "target": _NO_TARGET, "description": "locker"},
        {
            "type": "repeat", "iterations": 3,
            "steps": [
                {"type": "interval", "duration_secs": 15, "target": _NO_TARGET, "description": "Stride"},
                {"type": "recovery", "duration_secs": 45, "target": _NO_TARGET, "description": "locker"},
            ],
        },
        {"type": "cooldown", "duration_meters": 1000, "target": _NO_TARGET},
    ],
}


def _fetch_row(cur, external_id: str) -> dict | None:
    cur.execute(
        """SELECT id, name, distance_km, duration_min, notes, workout_steps, target,
                  session_type, session_zone, garmin_workout_id, sync_status, content_hash,
                  (week_date + (day_of_week - 1) * INTERVAL '1 day')::date AS session_date
           FROM training_plan WHERE external_id = %s""",
        (external_id,),
    )
    row = cur.fetchone()
    if not row:
        return None
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))


def _apply_update(cur, external_id: str, updates: dict, new_steps=None) -> str:
    current = _fetch_row(cur, external_id)
    if current is None:
        return f"SKIP (nicht gefunden): {external_id}"

    new_name = updates.get("name", current["name"])
    new_distance = updates.get("distance_km", current["distance_km"])
    new_duration = updates.get("duration_min", current["duration_min"])
    new_notes = updates.get("notes", current["notes"])
    new_workout_steps = new_steps if new_steps is not None else current["workout_steps"]

    candidate = {
        "date": str(current["session_date"]), "session_type": current["session_type"],
        "distance_km": new_distance, "duration_min": new_duration,
        "notes": new_notes, "workout_steps": new_workout_steps,
        "session_zone": current["session_zone"], "name": new_name, "target": current["target"],
    }
    new_hash = compute_content_hash(candidate)
    was_synced = current["garmin_workout_id"] is not None and current["sync_status"] == "synced"
    content_changed = new_hash != current["content_hash"]
    mark_dirty = was_synced and content_changed

    import json as _json
    cur.execute(
        """UPDATE training_plan
               SET name = %s, distance_km = %s, duration_min = %s, notes = %s,
                   workout_steps = COALESCE(%s, workout_steps),
                   sync_status = CASE WHEN %s THEN 'dirty' ELSE sync_status END,
                   content_hash = CASE WHEN %s THEN %s ELSE content_hash END,
                   updated_at = now()
           WHERE external_id = %s""",
        (
            new_name, new_distance, new_duration, new_notes,
            _json.dumps(new_workout_steps) if new_workout_steps is not None else None,
            mark_dirty, mark_dirty, new_hash,
            external_id,
        ),
    )
    tag = "dirty" if mark_dirty else ("unchanged" if not content_changed else "updated")
    return f"{tag}: {external_id} -> name={new_name!r} distance_km={new_distance} duration_min={new_duration}"


def run(apply: bool = True) -> None:
    conn = get_connection()
    try:
        cur = conn.cursor()
        results = []
        for ext_id, updates in RUN_UPDATES.items():
            steps = STRUCTURED_STEPS.get(ext_id)
            results.append(_apply_update(cur, ext_id, updates, new_steps=steps))
        for ext_id, updates in BIKE_UPDATES.items():
            results.append(_apply_update(cur, ext_id, updates))

        if apply:
            conn.commit()
        else:
            conn.rollback()

        for line in results:
            print(line)
        print(f"\n{len(results)} Sessions verarbeitet ({'committed' if apply else 'DRY-RUN, rollback'}).")
    finally:
        conn.close()


if __name__ == "__main__":
    run(apply=True)
