"""
coach/activity_data.py
Deterministische Aktivitäts-Analysewerte für CAIRN — reine Berechnungslogik
über bereits importierte Daten (trainings/splits/activity_stream/daily_logs).
Kein Garmin-API-Zugriff hier (der lebt in data/garmin_import_*.py und wird
von coach/mcp_server.py::sync_activity_details orchestriert).

Begriffe (siehe CAIRN Activity-Analysis-Auftrag 2026-08-18):
- native_laps: Garmins tatsächlich aufgezeichnete Laps (splits-Tabelle,
  befüllt aus client.get_activity_splits()::lapDTOs). Werden NIE verändert
  oder durch berechnete Splits ersetzt.
- km_splits:   aus activity_stream (distance_m + elapsed_s) berechnete
  1000m-Segmente, nur wenn ein zeitlich aufgelöster Distanz-Stream existiert.
  Nie erfunden, wenn die Datengrundlage fehlt.
"""
from __future__ import annotations

import decimal
import hashlib
import json
from typing import Any


def _dictify(cur) -> list[dict]:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


_SPORT_MAP = {
    "Run": "running", "TrailRun": "trail_running", "Ride": "cycling",
    "WeightTraining": "strength_training", "Swim": "swimming",
    "Walk": "walking", "Hike": "hiking", "Yoga": "yoga", "Cardio": "cardio",
}


def _f(v):
    return float(v) if isinstance(v, decimal.Decimal) else v


# ── Summary ──────────────────────────────────────────────────────────────────

def build_summary(conn, training: dict) -> dict:
    """
    Aktivitätskopf. Nicht in der DB vorhandene Felder (sub_sport, device_name,
    normalized_power, calories, temperature) bleiben bewusst null — nichts
    davon wird aktuell irgendwo importiert/gespeichert, daher "nicht
    verfügbar" statt erfunden. max_cadence/max_power werden, wenn möglich,
    zusätzlich aus activity_stream abgeleitet (dort real vorhanden, aber nie
    zuvor ausgewertet).
    """
    tid = training["id"]

    with conn.cursor() as cur:
        cur.execute(
            "SELECT start_time, moving_duration_s, pace_seconds, distance_km "
            "FROM splits WHERE training_id = %s ORDER BY split_number LIMIT 1",
            (tid,),
        )
        first_lap = cur.fetchone()
        cur.execute(
            "SELECT MAX(cadence), MAX(power) FROM activity_stream WHERE training_id = %s",
            (tid,),
        )
        max_cad_row = cur.fetchone()
        cur.execute(
            "SELECT g.id, g.gear_type, g.nickname, u.distance_km FROM activity_gear_usage u "
            "JOIN athlete_gear g ON g.id = u.gear_id WHERE u.training_id = %s",
            (tid,),
        )
        gear_rows = _dictify(cur)

    start_time = first_lap[0] if first_lap else None
    start_time_utc = start_time.isoformat() if start_time else None
    start_time_local = None
    if start_time:
        try:
            from zoneinfo import ZoneInfo
            start_time_local = start_time.astimezone(ZoneInfo("Europe/Zurich")).isoformat()
        except Exception:
            start_time_local = start_time_utc

    moving_s = first_lap[1] if first_lap else None
    max_cadence = max_cad_row[0] if max_cad_row else None
    max_power = max_cad_row[1] if max_cad_row else None

    dist = training.get("distance_km")
    dur = training.get("duration_minutes")
    avg_pace_s = int(dur * 60 / float(dist)) if dist and dur and float(dist) > 0 else None

    return {
        "training_id": tid,
        "garmin_id": training.get("garmin_id"),
        "date": str(training["date"]) if training.get("date") else None,
        "start_time_local": start_time_local,
        "start_time_utc": start_time_utc,
        "activity_name": training.get("notes"),
        "sport": _SPORT_MAP.get(training.get("type"), (training.get("type") or "").lower() or None),
        "sub_sport": None,
        "device_name": None,
        "distance_km": _f(dist),
        "duration_min": dur,
        "moving_time_min": round(moving_s / 60, 1) if moving_s is not None else None,
        "elapsed_time_min": dur,
        "avg_pace_per_km": f"{avg_pace_s // 60}:{str(avg_pace_s % 60).zfill(2)}" if avg_pace_s else None,
        "avg_hr": training.get("heart_rate_avg"),
        "max_hr": training.get("max_hr"),
        "avg_cadence": training.get("avg_cadence"),
        "max_cadence": max_cadence,
        "avg_power": training.get("avg_power"),
        "max_power": max_power,
        "normalized_power": None,
        "elevation_gain_m": _f(training.get("elevation_gain_m")),
        "elevation_loss_m": _f(training.get("elevation_loss_m")),
        "min_elevation_m": None,
        "max_elevation_m": None,
        "training_load": _f(training.get("training_load")),
        "aerobic_effect": _f(training.get("aerobic_effect")),
        "anaerobic_effect": _f(training.get("anaerobic_effect")),
        "vo2max_estimate": _f(training.get("vo2max_estimate")),
        "calories": None,
        "temperature_avg_c": None,
        "temperature_min_c": None,
        "temperature_max_c": None,
        "gear": [
            {"id": g["id"], "gear_type": g["gear_type"], "nickname": g["nickname"],
             "distance_km": _f(g["distance_km"])}
            for g in gear_rows
        ],
    }


# ── Native Laps (Garmin lapDTOs, unverändert aus splits) ────────────────────

def build_native_laps(conn, training_id: int) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT split_number, start_time, distance_km, pace_seconds,
                   moving_duration_s, heart_rate_avg, max_hr, cadence_avg,
                   avg_power, elevation_gain, elevation_loss_m, lap_type
            FROM splits WHERE training_id = %s ORDER BY split_number
            """,
            (training_id,),
        )
        rows = _dictify(cur)

    laps = []
    for r in rows:
        distance_m = float(r["distance_km"]) * 1000 if r["distance_km"] is not None else None
        duration_s = r["pace_seconds"] * float(r["distance_km"]) if r["pace_seconds"] and r["distance_km"] else None
        laps.append({
            "lap_number": r["split_number"],
            "start_time": r["start_time"].isoformat() if r["start_time"] else None,
            "distance_m": round(distance_m, 1) if distance_m is not None else None,
            "duration_s": round(duration_s) if duration_s is not None else None,
            "moving_duration_s": r["moving_duration_s"],
            "pace_seconds_per_km": r["pace_seconds"],
            "avg_hr": r["heart_rate_avg"],
            "max_hr": r["max_hr"],
            "avg_cadence": r["cadence_avg"],
            "avg_power": r["avg_power"],
            "elevation_gain_m": float(r["elevation_gain"]) if r["elevation_gain"] is not None else None,
            "elevation_loss_m": float(r["elevation_loss_m"]) if r["elevation_loss_m"] is not None else None,
            "lap_type": r["lap_type"] or "unknown",
        })
    return laps


# ── km_splits — aus dem Distanz/Zeit-Stream berechnet ───────────────────────

def _interpolate_elapsed_s(points: list[tuple], target_m: float) -> float | None:
    """points: sortierte Liste von (distance_m, elapsed_s). Lineare Interpolation."""
    if not points:
        return None
    if target_m <= points[0][0]:
        return points[0][1]
    for (d1, t1), (d2, t2) in zip(points, points[1:]):
        if d1 <= target_m <= d2:
            if d2 == d1:
                return t1
            frac = (target_m - d1) / (d2 - d1)
            return t1 + frac * (t2 - t1)
    return points[-1][1]


def compute_km_splits(conn, training_id: int) -> tuple[list[dict] | None, str | None]:
    """
    Berechnet 1000m-Splits aus activity_stream (distance_m + elapsed_s).
    Rückgabe: (splits, reason). splits=None wenn nicht berechenbar — reason
    erklärt dann warum (kein künstliches Erfinden von Splits).
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT distance_m, elapsed_s, heart_rate, cadence, power, elevation_m
            FROM activity_stream
            WHERE training_id = %s AND distance_m IS NOT NULL AND elapsed_s IS NOT NULL
            ORDER BY distance_m
            """,
            (training_id,),
        )
        rows = _dictify(cur)

    if len(rows) < 2:
        return None, "Kilometer-Splits nicht berechenbar – zeitlich aufgelöste Distanzdaten fehlen."

    dist_time_pts = [(r["distance_m"], r["elapsed_s"]) for r in rows]
    total_m = dist_time_pts[-1][0]
    if total_m < 1000:
        return None, "Kilometer-Splits nicht berechenbar – Aktivität ist kürzer als 1 km."

    n_full_km = int(total_m // 1000)
    boundaries_m = [k * 1000 for k in range(0, n_full_km + 1)]
    remainder_m = total_m - n_full_km * 1000
    has_partial = remainder_m > 20  # Rauschen an der Streckenkante ignorieren
    if has_partial:
        boundaries_m.append(total_m)

    boundary_times = [_interpolate_elapsed_s(dist_time_pts, b) for b in boundaries_m]

    splits: list[dict] = []
    for i in range(len(boundaries_m) - 1):
        d_start, d_end = boundaries_m[i], boundaries_m[i + 1]
        t_start, t_end = boundary_times[i], boundary_times[i + 1]
        seg_rows = [r for r in rows if d_start <= r["distance_m"] <= d_end]

        duration_s = (t_end - t_start) if (t_start is not None and t_end is not None) else None
        seg_distance_m = d_end - d_start
        pace_s = round(duration_s / (seg_distance_m / 1000)) if duration_s and seg_distance_m > 0 else None

        hr_vals = [r["heart_rate"] for r in seg_rows if r["heart_rate"] is not None]
        cad_vals = [r["cadence"] for r in seg_rows if r["cadence"] is not None]
        pwr_vals = [r["power"] for r in seg_rows if r["power"] is not None]
        ele_vals = [(r["distance_m"], r["elevation_m"]) for r in seg_rows if r["elevation_m"] is not None]

        ele_gain = ele_loss = None
        if len(ele_vals) >= 2:
            ele_gain, ele_loss = 0.0, 0.0
            for (_, e1), (_, e2) in zip(ele_vals, ele_vals[1:]):
                diff = e2 - e1
                if diff > 0:
                    ele_gain += diff
                else:
                    ele_loss += abs(diff)

        splits.append({
            "km": i + 1,
            "distance_m": round(seg_distance_m, 1),
            "duration_s": round(duration_s) if duration_s is not None else None,
            "pace_seconds_per_km": pace_s,
            "pace_per_km": f"{pace_s // 60}:{str(pace_s % 60).zfill(2)}" if pace_s else None,
            "avg_hr": round(sum(hr_vals) / len(hr_vals)) if hr_vals else None,
            "max_hr": max(hr_vals) if hr_vals else None,
            "avg_cadence": round(sum(cad_vals) / len(cad_vals)) if cad_vals else None,
            "avg_power": round(sum(pwr_vals) / len(pwr_vals)) if pwr_vals else None,
            "elevation_gain_m": round(ele_gain, 1) if ele_gain is not None else None,
            "elevation_loss_m": round(ele_loss, 1) if ele_loss is not None else None,
            "partial": has_partial and (i == len(boundaries_m) - 2),
        })
    return splits, None


# ── Route (GPS) ──────────────────────────────────────────────────────────────

def build_route(conn, training_id: int) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT lat, lon FROM activity_stream "
            "WHERE training_id = %s AND lat IS NOT NULL AND lon IS NOT NULL "
            "ORDER BY distance_m",
            (training_id,),
        )
        rows = cur.fetchall()

    if not rows:
        return {"available": False, "coordinates": None, "bounds": None, "start": None, "finish": None}

    coords = [[round(lon, 6), round(lat, 6)] for lat, lon in rows]
    lats = [lat for lat, _ in rows]
    lons = [lon for _, lon in rows]
    return {
        "available": True,
        "coordinates": coords,
        "bounds": {
            "min_lat": round(min(lats), 6), "max_lat": round(max(lats), 6),
            "min_lon": round(min(lons), 6), "max_lon": round(max(lons), 6),
        },
        "start": {"latitude": round(lats[0], 6), "longitude": round(lons[0], 6)},
        "finish": {"latitude": round(lats[-1], 6), "longitude": round(lons[-1], 6)},
    }


# ── Trail-Segmente + Trail-Metriken (aus activity_stream) ──────────────────

_UPHILL_GRADE_PCT = 3.0
_DOWNHILL_GRADE_PCT = -3.0


def _merge_short_segments(segments: list[dict], min_length_m: float) -> list[dict]:
    """
    Verschmilzt Segmente unter min_length_m mit dem vorigen Segment (dessen
    Typ gewinnt) — glaettet Rauschen im Hoehenprofil zu wenigen, sinnvollen
    Anstiegs-/Abstiegs-/Flach-Abschnitten statt vieler Mikrosegmente.
    """
    if len(segments) <= 1:
        return segments
    merged: list[dict] = [segments[0]]
    for seg in segments[1:]:
        if seg["distance_m"] < min_length_m and merged:
            prev = merged[-1]
            prev["end_distance_m"] = seg["end_distance_m"]
            prev["distance_m"] = prev["end_distance_m"] - prev["start_distance_m"]
            prev["elevation_change_m"] = round(prev["elevation_change_m"] + seg["elevation_change_m"], 1)
            prev["duration_s"] = (prev.get("duration_s") or 0) + (seg.get("duration_s") or 0)
            prev["_hr_vals"].extend(seg["_hr_vals"])
            prev["max_grade_pct"] = round(max(prev["max_grade_pct"], seg["max_grade_pct"]), 1)
            if prev["distance_m"] > 0:
                prev["avg_grade_pct"] = round(prev["elevation_change_m"] / prev["distance_m"] * 100, 1)
                prev["type"] = (
                    "uphill" if prev["avg_grade_pct"] > _UPHILL_GRADE_PCT
                    else ("downhill" if prev["avg_grade_pct"] < _DOWNHILL_GRADE_PCT else "flat")
                )
        else:
            merged.append(seg)
    # Nach dem Verschmelzen koennen benachbarte Segmente denselben Typ haben
    # (z.B. flat -> [kurzes uphill absorbiert] -> flat wird faelschlich zwei
    # "flat"-Eintraege) -- ein zweiter Durchlauf fasst gleiche Nachbarn zusammen.
    collapsed: list[dict] = []
    for seg in merged:
        if collapsed and collapsed[-1]["type"] == seg["type"]:
            prev = collapsed[-1]
            prev["end_distance_m"] = seg["end_distance_m"]
            prev["distance_m"] = prev["end_distance_m"] - prev["start_distance_m"]
            prev["elevation_change_m"] = round(prev["elevation_change_m"] + seg["elevation_change_m"], 1)
            prev["duration_s"] = (prev.get("duration_s") or 0) + (seg.get("duration_s") or 0)
            prev["_hr_vals"].extend(seg["_hr_vals"])
            prev["max_grade_pct"] = round(max(prev["max_grade_pct"], seg["max_grade_pct"]), 1)
        else:
            collapsed.append(seg)
    return collapsed


def build_trail_metrics_and_segments(conn, training_id: int) -> tuple[dict, list[dict]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT distance_m, elapsed_s, elevation_m, heart_rate, speed_ms "
            "FROM activity_stream WHERE training_id = %s ORDER BY distance_m",
            (training_id,),
        )
        rows = _dictify(cur)

    ele_rows = [r for r in rows if r["elevation_m"] is not None]
    if len(ele_rows) < 2:
        return (
            {"ascent_m": None, "descent_m": None, "min_elevation_m": None, "max_elevation_m": None,
             "avg_grade_pct": None, "max_grade_pct": None, "vertical_speed_m_per_h": None,
             "longest_climb_m": None, "steepest_sustained_climb_pct": None, "source": None},
            [],
        )

    ascent = descent = 0.0
    elevations = [r["elevation_m"] for r in ele_rows]
    for e1, e2 in zip(elevations, elevations[1:]):
        diff = e2 - e1
        if diff > 0:
            ascent += diff
        else:
            descent += abs(diff)

    # Segmentierung: Steigung ueber ein gleitendes ~200m-Fenster. Ein kleineres
    # Fenster (urspruenglich 50m) erzeugte bei rauschigen GPS-/Barometerdaten
    # dutzende Mikrosegmente (z.B. 89 Segmente auf 15km) statt der wenigen
    # zusammenhaengenden Anstiege/Abstiege, die ein Coach sehen will.
    segments: list[dict] = []
    current = None
    window_m = 200
    i = 0
    max_grade_pct = 0.0
    while i < len(ele_rows) - 1:
        d0, e0 = ele_rows[i]["distance_m"], ele_rows[i]["elevation_m"]
        j = i + 1
        while j < len(ele_rows) and ele_rows[j]["distance_m"] - d0 < window_m:
            j += 1
        if j >= len(ele_rows):
            j = len(ele_rows) - 1
        d1, e1 = ele_rows[j]["distance_m"], ele_rows[j]["elevation_m"]
        seg_dist = d1 - d0
        grade = ((e1 - e0) / seg_dist * 100) if seg_dist > 0 else 0.0
        max_grade_pct = max(max_grade_pct, abs(grade))
        terrain = "uphill" if grade > _UPHILL_GRADE_PCT else ("downhill" if grade < _DOWNHILL_GRADE_PCT else "flat")

        seg_rows = [r for r in rows if d0 <= r["distance_m"] <= d1]
        hr_vals = [r["heart_rate"] for r in seg_rows if r["heart_rate"] is not None]
        t0 = next((r["elapsed_s"] for r in seg_rows if r["elapsed_s"] is not None), None)
        t1 = next((r["elapsed_s"] for r in reversed(seg_rows) if r["elapsed_s"] is not None), None)
        duration_s = (t1 - t0) if (t0 is not None and t1 is not None and t1 > t0) else None
        pace_s = round(duration_s / (seg_dist / 1000)) if duration_s and seg_dist > 0 else None

        if current and current["type"] == terrain:
            current["end_distance_m"] = d1
            current["distance_m"] = current["end_distance_m"] - current["start_distance_m"]
            current["elevation_change_m"] = round(e1 - current["_start_ele"], 1)
            if duration_s: current["duration_s"] = (current.get("duration_s") or 0) + duration_s
            if hr_vals: current["_hr_vals"].extend(hr_vals)
            current["max_grade_pct"] = round(max(current["max_grade_pct"], abs(grade)), 1)
        else:
            if current:
                segments.append(current)
            current = {
                "type": terrain, "start_distance_m": d0, "end_distance_m": d1,
                "distance_m": d1 - d0, "duration_s": duration_s or 0,
                "elevation_change_m": round(e1 - e0, 1), "_start_ele": e0,
                "avg_grade_pct": round(grade, 1), "max_grade_pct": round(abs(grade), 1),
                "_hr_vals": list(hr_vals),
            }
        i = j

    if current:
        segments.append(current)

    segments = _merge_short_segments(segments, min_length_m=150)

    out_segments = []
    longest_climb_m = 0.0
    steepest_sustained_pct = 0.0
    for seg in segments:
        avg_hr = round(sum(seg["_hr_vals"]) / len(seg["_hr_vals"])) if seg["_hr_vals"] else None
        pace_s = round(seg["duration_s"] / (seg["distance_m"] / 1000)) if seg["duration_s"] and seg["distance_m"] > 0 else None
        vam = round(seg["elevation_change_m"] / (seg["duration_s"] / 3600), 1) if seg["type"] == "uphill" and seg["duration_s"] else None
        if seg["type"] == "uphill":
            longest_climb_m = max(longest_climb_m, seg["distance_m"])
            steepest_sustained_pct = max(steepest_sustained_pct, seg["avg_grade_pct"])
        out_segments.append({
            "type": seg["type"],
            "start_distance_m": round(seg["start_distance_m"], 1),
            "end_distance_m": round(seg["end_distance_m"], 1),
            "distance_m": round(seg["distance_m"], 1),
            "duration_s": round(seg["duration_s"]) if seg["duration_s"] else None,
            "elevation_change_m": seg["elevation_change_m"],
            "avg_grade_pct": seg["avg_grade_pct"],
            "max_grade_pct": seg["max_grade_pct"],
            "avg_hr": avg_hr,
            "avg_pace_seconds_per_km": pace_s,
            "vertical_speed_m_per_h": vam,
        })

    total_dist_km = (ele_rows[-1]["distance_m"] - ele_rows[0]["distance_m"]) / 1000
    total_time_h = (ele_rows[-1]["elapsed_s"] - ele_rows[0]["elapsed_s"]) / 3600 \
        if ele_rows[-1]["elapsed_s"] is not None and ele_rows[0]["elapsed_s"] is not None else None
    avg_grade = (ascent / (total_dist_km * 1000) * 100) if total_dist_km > 0 else None

    trail_metrics = {
        "ascent_m": round(ascent, 1),
        "descent_m": round(descent, 1),
        "min_elevation_m": round(min(elevations), 1),
        "max_elevation_m": round(max(elevations), 1),
        "avg_grade_pct": round(avg_grade, 1) if avg_grade is not None else None,
        "max_grade_pct": round(max_grade_pct, 1),
        "vertical_speed_m_per_h": round(ascent / total_time_h, 1) if total_time_h else None,
        "longest_climb_m": round(longest_climb_m, 1) if longest_climb_m else None,
        "steepest_sustained_climb_pct": round(steepest_sustained_pct, 1) if steepest_sustained_pct else None,
        "source": "activity_stream",
    }
    return trail_metrics, out_segments


# ── Data Quality ─────────────────────────────────────────────────────────────

def build_data_quality(
    *, native_laps: list[dict], km_splits: list[dict] | None, stream_available: bool,
    hr_stream_available: bool, pace_stream_available: bool, elevation_stream_available: bool,
    gps_route_available: bool, power_available: bool, hr_zones_available: bool,
    summary: dict, hr_stream_point_count: int,
) -> dict:
    warnings: list[str] = []

    ascent, descent = summary.get("elevation_gain_m"), summary.get("elevation_loss_m")
    if ascent and (descent is None or descent == 0):
        warnings.append(f"{ascent} m Aufstieg, aber kein/0 m Abstieg erfasst — unplausibel für eine Rundstrecke.")

    if native_laps and len(native_laps) == 1 and (not km_splits or len(km_splits) <= 1):
        warnings.append("Nur ein Gesamtsplit über die gesamte Aktivität — keine kilometerweise Auflösung.")

    if summary.get("avg_hr") is not None and summary.get("max_hr") is None:
        warnings.append("Durchschnittliche Herzfrequenz vorhanden, aber kein Maximalwert.")

    dur_min = summary.get("duration_min")
    if hr_stream_available and dur_min and hr_stream_point_count and dur_min > 0:
        points_per_min = hr_stream_point_count / dur_min
        if points_per_min < 1:
            warnings.append(
                f"Nur {hr_stream_point_count} HF-Punkte für {dur_min} Minuten — sehr grobe Auflösung."
            )

    if stream_available and not pace_stream_available:
        warnings.append("Fehlende Distanzkorrelation für Pace-Berechnung im Stream.")

    if not gps_route_available:
        warnings.append("Keine GPS-Koordinaten verfügbar — keine Streckenkarte darstellbar.")

    dur = summary.get("duration_min")
    if dur is not None and dur <= 0:
        warnings.append("Inkonsistenter Dauerwert (<= 0 Minuten).")

    return {
        "summary_complete": summary.get("distance_km") is not None and summary.get("duration_min") is not None,
        "native_laps_available": bool(native_laps),
        "km_splits_available": bool(km_splits),
        "hr_stream_available": hr_stream_available,
        "pace_stream_available": pace_stream_available,
        "elevation_stream_available": elevation_stream_available,
        "gps_route_available": gps_route_available,
        "power_available": power_available,
        "hr_zones_available": hr_zones_available,
        "warnings": warnings,
    }


# ── HR-Zonen (zeitgewichtet, aus athlete_profile) ───────────────────────────

def _zone_bounds_from_profile(profile: dict) -> tuple[list[tuple[int, int, int]], str] | None:
    """Bevorzugt das neue hr_zones-jsonb-Schema, faellt auf die alten
    hr_z1_min..hr_z5_max-Spalten zurueck. None wenn beides fehlt."""
    if profile.get("hr_zones"):
        try:
            zones_raw = profile["hr_zones"]
            bounds = []
            for i in range(1, 6):
                z = zones_raw.get(f"z{i}")
                if not z:
                    return None
                bounds.append((i, int(z["min"]), int(z["max"])))
            return bounds, profile.get("hr_zone_method") or "profile_hr_zones"
        except (KeyError, TypeError, ValueError):
            pass

    keys = ["hr_z1_min", "hr_z1_max", "hr_z2_min", "hr_z2_max", "hr_z3_min", "hr_z3_max",
            "hr_z4_min", "hr_z4_max", "hr_z5_min", "hr_z5_max"]
    if not all(profile.get(k) is not None for k in keys):
        return None
    bounds = [
        (1, profile["hr_z1_min"], profile["hr_z1_max"]),
        (2, profile["hr_z2_min"], profile["hr_z2_max"]),
        (3, profile["hr_z3_min"], profile["hr_z3_max"]),
        (4, profile["hr_z4_min"], profile["hr_z4_max"]),
        (5, profile["hr_z5_min"], profile["hr_z5_max"]),
    ]
    return bounds, "legacy_hr_z_columns"


def build_hr_zones(conn, training_id: int) -> dict | None:
    """
    Zeitgewichtete Zeit-in-Zone-Berechnung: gewichtet jeden Stream-Punkt mit
    der Zeit bis zum naechsten Punkt (nicht einfache Punktanzahl), da
    Messabstaende unterschiedlich sein koennen (Pausen, Auto-Pause, variable
    Sendefrequenz).
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, updated_at, hr_zones, hr_zone_method, "
            "hr_z1_min, hr_z1_max, hr_z2_min, hr_z2_max, hr_z3_min, hr_z3_max, "
            "hr_z4_min, hr_z4_max, hr_z5_min, hr_z5_max "
            "FROM athlete_profile ORDER BY id DESC LIMIT 1"
        )
        row = cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        profile = dict(zip(cols, row))

    resolved = _zone_bounds_from_profile(profile)
    if resolved is None:
        return None
    bounds, method = resolved

    with conn.cursor() as cur:
        cur.execute(
            "SELECT elapsed_s, heart_rate FROM activity_stream "
            "WHERE training_id = %s AND heart_rate IS NOT NULL AND elapsed_s IS NOT NULL "
            "ORDER BY elapsed_s",
            (training_id,),
        )
        pts = cur.fetchall()

    if len(pts) < 2:
        return None

    zone_seconds = {z: 0.0 for z, _, _ in bounds}
    unclassified = 0.0
    for (t1, hr1), (t2, _) in zip(pts, pts[1:]):
        dt = max(0.0, t2 - t1)
        # Grosse Luecken (Pause/Signalverlust) nicht als kontinuierliche Zeit
        # in einer Zone zaehlen.
        if dt > 120:
            continue
        matched = False
        for z, lo, hi in bounds:
            if lo <= hr1 <= hi:
                zone_seconds[z] += dt
                matched = True
                break
        if not matched:
            unclassified += dt

    total = sum(zone_seconds.values()) + unclassified
    if total <= 0:
        return None

    zones_out = []
    for z, lo, hi in bounds:
        secs = zone_seconds[z]
        zones_out.append({
            "zone": z, "min_bpm": lo, "max_bpm": hi,
            "duration_s": round(secs), "percentage": round(secs / total * 100, 1),
        })

    return {
        "method": method,
        "profile_updated_at": profile["updated_at"].isoformat() if profile.get("updated_at") else None,
        "zones": zones_out,
        "unclassified_duration_s": round(unclassified),
    }


# ── Recovery-Kontext (aus daily_logs, nichts erfinden) ──────────────────────

def build_recovery_context(conn, activity_date) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT date, sleep_duration_h, sleep_score, hrv_last_night, hrv_5day_avg, "
            "hrv_status, resting_hr, body_battery_charged, body_battery_drained, "
            "feel, athlete_text "
            "FROM daily_logs WHERE date = %s",
            (activity_date,),
        )
        row = cur.fetchone()
        cols = [d[0] for d in cur.description] if cur.description else []
        morning_of = dict(zip(cols, row)) if row else None

    with conn.cursor() as cur:
        cur.execute(
            "SELECT date, sleep_duration_h, sleep_score, hrv_last_night, hrv_5day_avg, "
            "hrv_status, resting_hr, body_battery_charged, body_battery_drained, "
            "feel, athlete_text "
            "FROM daily_logs WHERE date = %s::date + INTERVAL '1 day'",
            (activity_date,),
        )
        row2 = cur.fetchone()
        cols2 = [d[0] for d in cur.description] if cur.description else []
        next_day = dict(zip(cols2, row2)) if row2 else None

    def _serialize(d: dict | None) -> dict | None:
        if not d:
            return None
        out = {}
        for k, v in d.items():
            if k == "date":
                out[k] = v.isoformat() if v else None
            elif isinstance(v, decimal.Decimal):
                out[k] = float(v)
            else:
                out[k] = v
        return out

    return {
        "morning_of_activity": _serialize(morning_of),
        "next_day": _serialize(next_day),
    }


# ── Content-Hash fuer Stale-Erkennung ────────────────────────────────────────

def compute_source_data_hash(*, summary: dict, native_laps: list[dict],
                              km_splits: list[dict] | None, trail_metrics: dict) -> str:
    """
    Hash über die objektiven, deterministischen Analysewerte. Ändert sich
    dieser Hash gegenüber dem einer gespeicherten Coach-Analyse, gelten neue
    Rohdaten als vorhanden → Analyse ist "stale".
    """
    payload = {
        "distance_km": summary.get("distance_km"), "duration_min": summary.get("duration_min"),
        "avg_hr": summary.get("avg_hr"), "max_hr": summary.get("max_hr"),
        "elevation_gain_m": summary.get("elevation_gain_m"), "elevation_loss_m": summary.get("elevation_loss_m"),
        "native_laps_count": len(native_laps), "km_splits_count": len(km_splits) if km_splits else 0,
        "trail_segments_ascent": trail_metrics.get("ascent_m"),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
