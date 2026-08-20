"""
coach/mcp_server.py
CAIRN Remote MCP Server — Streamable HTTP transport via FastMCP.

Security contract:
- Bearer token auth via MCP_API_KEY env var (never logged or echoed)
- No Garmin credentials in tool responses or logs
- Garmin write tools log to garmin_mcp_log (idempotent via external_id)
- preview_garmin_workout makes zero network calls
"""

from __future__ import annotations

import datetime
import decimal
import json
import logging
import os
from typing import Any

try:
    from mcp.server import MCPServer as FastMCP  # mcp 2.0+
except ImportError:
    from mcp.server import FastMCP  # type: ignore[assignment]  # mcp 1.x
from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from coach.garmin_push import (
    GarminPushError,
    build_workout_payload,
    garmin_client,
    push_workout,
    schedule_workout,
)
from coach.session_routing import classify_for_push, resolve_sync_target, split_training_block
from coach.sync_utils import compute_content_hash
from coach.activity_data import (
    build_data_quality,
    build_hr_zones,
    build_native_laps,
    build_recovery_context,
    build_route,
    build_summary,
    build_trail_metrics_and_segments,
    compute_km_splits,
    compute_source_data_hash,
)
from database.connection import get_connection

logger = logging.getLogger(__name__)

# ── FastMCP instance ───────────────────────────────────────────────────────────

mcp = FastMCP("CAIRN")

# ── Auth middleware ────────────────────────────────────────────────────────────

class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Enforce Authorization: Bearer <MCP_API_KEY> on every request."""

    async def dispatch(self, request: Request, call_next):
        api_key = os.getenv("MCP_API_KEY", "")
        if not api_key:
            logger.error("MCP_API_KEY is not set — rejecting all MCP requests")
            return Response("Server misconfigured: MCP_API_KEY missing", status_code=500)

        auth = request.headers.get("Authorization", "")
        token = auth[len("Bearer "):] if auth.startswith("Bearer ") else ""
        if token != api_key:
            return Response("Unauthorized", status_code=401)

        return await call_next(request)


# ── DB helpers ─────────────────────────────────────────────────────────────────

def _to_serializable(obj: Any) -> Any:
    """Recursively convert psycopg2 native types to JSON-safe Python types."""
    if isinstance(obj, dict):
        return {k: _to_serializable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_serializable(v) for v in obj]
    if isinstance(obj, (datetime.datetime, datetime.date)):
        return obj.isoformat()
    if isinstance(obj, decimal.Decimal):
        return float(obj)
    return obj


def _fetchall(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, row)) for row in cur.fetchall()]
        return _to_serializable(rows)
    finally:
        conn.close()


def _fetchone(sql: str, params: tuple = ()) -> dict[str, Any] | None:
    rows = _fetchall(sql, params)
    return rows[0] if rows else None


# ── Read-only tools ────────────────────────────────────────────────────────────

@mcp.tool()
def get_athlete_profile() -> dict:
    """
    Return Alexander's athlete profile, active training plan metadata, and coach context.
    Always call this first to understand the athlete before suggesting workouts or plan changes.

    athlete_profile includes (SELECT * — new fields appear automatically):
    HR-Zonen (hr_zones jsonb, bevorzugt gegenüber den alten hr_z1_min..hr_z5_max
    Spalten, die aus Rückwärtskompatibilität weiterbestehen), pace_zones,
    preferred_surfaces/sports, training_preferences, injury_notes,
    long_term_goals_json. Zusätzlich gear_summary: aktive Gear-Gegenstände
    aus athlete_gear (siehe list_athlete_gear für Details je Gegenstand).
    """
    profile = _fetchone("SELECT * FROM athlete_profile ORDER BY id DESC LIMIT 1")
    plan = _fetchone(
        "SELECT id, goal_type, race_name, race_date, race_distance_km, "
        "total_weeks, current_week, status, created_at "
        "FROM plans ORDER BY created_at DESC LIMIT 1"
    )
    context = _fetchone("SELECT * FROM coach_context ORDER BY id DESC LIMIT 1")
    gear_summary = _fetchall(
        "SELECT id, gear_type, nickname, brand, model, active, target_distance_km "
        "FROM athlete_gear WHERE active = true ORDER BY gear_type, id"
    )
    return {
        "athlete_profile": profile,
        "current_plan": plan,
        "coach_context": context,
        "gear_summary": gear_summary,
    }


# ── Athlete-Profil: partielles Update mit Validierung + Audit-Log ──────────

_PROFILE_FIELD_TYPES: dict[str, type | tuple[type, ...]] = {
    "name": str, "age": int, "height_cm": (int, float), "weight_kg": (int, float),
    "resting_hr": int, "max_hr": int, "lactate_threshold_hr": int,
    "hr_zone_method": str, "hr_zones": dict, "pace_zones": dict,
    "preferred_surfaces": list, "preferred_sports": list,
    "training_preferences": dict, "injury_notes": list, "long_term_goals": list,
}

# JSON-Feldname im patch -> tatsächliche DB-Spalte. long_term_goals mappt
# bewusst auf die NEUE long_term_goals_json-Spalte, nicht auf die alte
# long_term_goals-TEXT-Spalte (bleibt für bestehende Leser unverändert).
_PROFILE_COLUMN_MAP: dict[str, str] = {"long_term_goals": "long_term_goals_json"}


def _validate_hr_zones(hr_zones: dict) -> list[str]:
    """Harte Fehler (leeren die Liste NICHT — sie werden zurückgegeben und
    lehnen das gesamte Update ab): Min > Max, Überlappung. Lücken sind nur
    eine Warnung (separat behandelt von der aufrufenden Funktion)."""
    errors = []
    zones = []
    for i in range(1, 6):
        z = hr_zones.get(f"z{i}")
        if z is None:
            errors.append(f"hr_zones.z{i} fehlt")
            continue
        if not isinstance(z, dict) or "min" not in z or "max" not in z:
            errors.append(f"hr_zones.z{i} muss {{'min':int,'max':int}} sein")
            continue
        zmin, zmax = z["min"], z["max"]
        if not isinstance(zmin, (int, float)) or not isinstance(zmax, (int, float)):
            errors.append(f"hr_zones.z{i}: min/max müssen Zahlen sein")
            continue
        if zmin > zmax:
            errors.append(f"hr_zones.z{i}: min ({zmin}) darf nicht größer als max ({zmax}) sein")
        zones.append((i, zmin, zmax))

    for (i1, _, max1), (i2, min2, _) in zip(zones, zones[1:]):
        if min2 <= max1:
            errors.append(f"hr_zones.z{i1} (max={max1}) und z{i2} (min={min2}) überlappen sich")
    return errors


def _hr_zone_gap_warnings(hr_zones: dict) -> list[str]:
    warnings = []
    zones = [(i, hr_zones[f"z{i}"]["min"], hr_zones[f"z{i}"]["max"])
             for i in range(1, 6) if isinstance(hr_zones.get(f"z{i}"), dict)
             and "min" in hr_zones[f"z{i}"] and "max" in hr_zones[f"z{i}"]]
    for (i1, _, max1), (i2, min2, _) in zip(zones, zones[1:]):
        if min2 > max1 + 1:
            warnings.append(f"Lücke zwischen hr_zones.z{i1} (max={max1}) und z{i2} (min={min2}) — "
                             f"{min2 - max1 - 1} bpm sind keiner Zone zugeordnet.")
    return warnings


# ── Pace-Zonen-Validierung ───────────────────────────────────────────────────

_PACE_ZONE_KEYS: frozenset[str] = frozenset({
    "recovery_sec_km",
    "easy_sec_km",
    "steady_sec_km",
    "tempo_sec_km",
    "threshold_sec_km",
    "vo2max_sec_km",
    "sprint_sec_km",
    "uphill_avg_sec_km",
    "downhill_avg_sec_km",
})


def _validate_pace_zones(pace_zones: dict) -> list[str]:
    """Validiert den Inhalt von athlete_profile.pace_zones.

    Erlaubte Schlüssel: recovery_sec_km, easy_sec_km, steady_sec_km,
    tempo_sec_km, threshold_sec_km, vo2max_sec_km, sprint_sec_km,
    uphill_avg_sec_km, downhill_avg_sec_km.

    Jeder Wert muss ein Integer > 0 sein (Einheit: Sekunden pro Kilometer).
    Partielle Updates sind erlaubt — nicht alle Schlüssel müssen gesetzt sein.
    Unbekannte Schlüssel werden mit einem Fehler abgelehnt.

    Gibt eine leere Liste zurück wenn alles korrekt ist.
    """
    errors: list[str] = []

    unknown = sorted(k for k in pace_zones if k not in _PACE_ZONE_KEYS)
    if unknown:
        errors.append(
            f"Unbekannte pace_zones-Schlüssel: {unknown}. "
            f"Erlaubt: {sorted(_PACE_ZONE_KEYS)}"
        )

    for key, val in pace_zones.items():
        if key not in _PACE_ZONE_KEYS:
            continue  # bereits oben gemeldet
        if isinstance(val, bool) or not isinstance(val, int):
            errors.append(
                f"pace_zones.{key}: muss ein Integer (sec/km) sein, "
                f"bekommen {type(val).__name__!r}"
            )
        elif val <= 0:
            errors.append(
                f"pace_zones.{key}: muss > 0 sein, bekommen {val}"
            )

    return errors


@mcp.tool()
def update_athlete_profile(patch: dict, expected_updated_at: str | None = None) -> dict:
    """
    Partielles Update von Alexanders Athlete-Profil. Nur im patch enthaltene
    Felder werden geändert — alles andere bleibt unverändert. patch["feld"]=null
    setzt das Feld explizit auf NULL; ein im patch komplett fehlendes Feld
    wird NICHT angefasst (Unterschied zwischen "null" und "nicht übergeben").

    Args:
        patch: {"name": str, "age": int, "height_cm": number, "weight_kg": number,
                "resting_hr": int, "max_hr": int, "lactate_threshold_hr": int,
                "hr_zone_method": str, "hr_zones": {"z1":{"min":int,"max":int},...,"z5":{...}},
                "pace_zones": {
                    Alle Werte in Sekunden pro Kilometer (Integer > 0).
                    Partielle Updates erlaubt — nur geänderte Schlüssel übergeben.
                    Erlaubte Schlüssel:
                      "recovery_sec_km":   int,  -- sehr locker, aktive Erholung
                      "easy_sec_km":       int,  -- lockerer Ausdauerlauf (Z1/Z2)
                      "steady_sec_km":     int,  -- aerober Grundlagenbereich
                      "tempo_sec_km":      int,  -- zügig, komfortabel hart
                      "threshold_sec_km":  int,  -- Laktatschwelle
                      "vo2max_sec_km":     int,  -- VO2max-Intervallbereich
                      "sprint_sec_km":     int,  -- kurze Maximalsprints
                      "uphill_avg_sec_km": int,  -- nur für interne Distanzschätzung, KEIN Garmin-Target
                      "downhill_avg_sec_km": int -- nur für interne Distanzschätzung, KEIN Garmin-Target
                } | null,
                "preferred_surfaces": [str], "preferred_sports": [str],
                "training_preferences": dict, "injury_notes": [str], "long_term_goals": [str]}
        expected_updated_at: Optional — ISO-Timestamp aus einem vorherigen
                get_athlete_profile-Aufruf. Wenn gesetzt und das Profil wurde
                seither von woanders geändert (optimistic locking), wird das
                Update mit einem Konflikt-Fehler abgelehnt statt es stillschweigend
                zu überschreiben.

    Returns: {updated, changed_fields, warnings, athlete_profile} oder
    {updated: false, error, ...} bei Validierungsfehlern/Konflikt.
    """
    if not isinstance(patch, dict) or not patch:
        return {"updated": False, "error": "patch darf nicht leer sein."}

    unknown = [k for k in patch if k not in _PROFILE_FIELD_TYPES]
    if unknown:
        return {"updated": False, "error": f"Unbekannte Felder: {unknown}. "
                f"Erlaubt: {sorted(_PROFILE_FIELD_TYPES)}"}

    errors: list[str] = []
    warnings: list[str] = []

    for field, value in patch.items():
        if value is None:
            continue  # explizites null ist immer erlaubt (Feld wird geleert)
        expected = _PROFILE_FIELD_TYPES[field]
        if not isinstance(value, expected):
            errors.append(f"{field}: erwartet {expected}, bekommen {type(value).__name__}")

    if patch.get("hr_zones") is not None and isinstance(patch["hr_zones"], dict):
        hr_errors = _validate_hr_zones(patch["hr_zones"])
        errors.extend(hr_errors)
        if not hr_errors:
            warnings.extend(_hr_zone_gap_warnings(patch["hr_zones"]))

    if patch.get("pace_zones") is not None and isinstance(patch["pace_zones"], dict):
        errors.extend(_validate_pace_zones(patch["pace_zones"]))

    if "resting_hr" in patch and "max_hr" in patch and patch["resting_hr"] and patch["max_hr"]:
        if patch["resting_hr"] >= patch["max_hr"]:
            errors.append(f"resting_hr ({patch['resting_hr']}) muss kleiner als max_hr ({patch['max_hr']}) sein")

    if patch.get("age") is not None and not (5 <= patch["age"] <= 110):
        warnings.append(f"age={patch['age']} wirkt unplausibel.")
    if patch.get("resting_hr") is not None and not (25 <= patch["resting_hr"] <= 110):
        warnings.append(f"resting_hr={patch['resting_hr']} wirkt unplausibel.")
    if patch.get("max_hr") is not None and not (100 <= patch["max_hr"] <= 230):
        warnings.append(f"max_hr={patch['max_hr']} wirkt unplausibel.")

    if errors:
        return {"updated": False, "error": "Validierung fehlgeschlagen.", "errors": errors}

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, updated_at FROM athlete_profile ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
            if not row:
                return {"updated": False, "error": "Kein athlete_profile-Datensatz vorhanden."}
            profile_id, current_updated_at = row

            if expected_updated_at:
                try:
                    expected_dt = datetime.datetime.fromisoformat(expected_updated_at)
                except ValueError:
                    return {"updated": False, "error": f"expected_updated_at {expected_updated_at!r} ist kein gültiges ISO-8601."}
                if current_updated_at and abs((current_updated_at - expected_dt).total_seconds()) > 1:
                    return {
                        "updated": False,
                        "error": "Konflikt: Profil wurde seit expected_updated_at bereits geändert.",
                        "current_updated_at": current_updated_at.isoformat(),
                    }

            old_values = {}
            columns = [_PROFILE_COLUMN_MAP.get(f, f) for f in patch]
            cur.execute(
                f"SELECT {', '.join(columns)} FROM athlete_profile WHERE id = %s", (profile_id,)
            )
            old_row = cur.fetchone()
            for col, val in zip(columns, old_row):
                old_values[col] = val.isoformat() if hasattr(val, "isoformat") else \
                    (float(val) if isinstance(val, decimal.Decimal) else val)

            set_clauses = []
            values = []
            for field, value in patch.items():
                col = _PROFILE_COLUMN_MAP.get(field, field)
                set_clauses.append(f"{col} = %s")
                values.append(json.dumps(value) if isinstance(value, (dict, list)) else value)
            set_clauses.append("updated_at = now()")
            values.append(profile_id)

            cur.execute(
                f"UPDATE athlete_profile SET {', '.join(set_clauses)} WHERE id = %s",
                values,
            )

            new_values = {_PROFILE_COLUMN_MAP.get(f, f): patch[f] for f in patch}
            cur.execute(
                """INSERT INTO athlete_profile_audit_log
                       (athlete_profile_id, changed_fields, old_values, new_values, source)
                   VALUES (%s, %s, %s, %s, %s)""",
                (profile_id, json.dumps(sorted(patch.keys())), json.dumps(old_values, default=str),
                 json.dumps(new_values, default=str), "mcp_update_athlete_profile"),
            )
        conn.commit()
        # Nur Feldnamen loggen, niemals Werte (Gesundheitsdaten) — Sicherheitsvorgabe.
        logger.info("update_athlete_profile: changed_fields=%s", sorted(patch.keys()))

        updated_profile = _fetchone("SELECT * FROM athlete_profile WHERE id = %s", (profile_id,))
        return {
            "updated": True,
            "changed_fields": sorted(patch.keys()),
            "warnings": warnings,
            "athlete_profile": updated_profile,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Gear (Schuhe etc.) ───────────────────────────────────────────────────────

_SHOE_GEAR_TYPES = frozenset({"running_shoe", "trail_shoe", "hiking_shoe"})
_GEAR_TYPES = frozenset({
    "running_shoe", "trail_shoe", "hiking_shoe", "bicycle", "watch",
    "heart_rate_sensor", "vest", "poles", "other",
})


def _gear_row_out(row: dict) -> dict:
    # row kommt aus _fetchall() (mcp_server.py) — dessen _to_serializable()
    # hat date/datetime bereits zu ISO-Strings und Decimal bereits zu float
    # konvertiert. Hier also direkt durchreichen, nicht erneut .isoformat().
    initial = float(row["initial_distance_km"] or 0)
    activity_dist = float(row["activity_distance_km"] or 0)
    total = round(initial + activity_dist, 2)
    target = float(row["target_distance_km"]) if row["target_distance_km"] is not None else None
    return {
        "id": row["id"], "gear_type": row["gear_type"], "brand": row["brand"], "model": row["model"],
        "nickname": row["nickname"], "size": row["size"], "color": row["color"],
        "primary_surface": row["primary_surface"], "active": row["active"],
        "initial_distance_km": round(initial, 2), "activity_distance_km": round(activity_dist, 2),
        "total_distance_km": total, "target_distance_km": target,
        "remaining_distance_km": round(target - total, 2) if target is not None else None,
        "activity_count": row["activity_count"] or 0,
        "last_used_at": row["last_used_at"],
        "purchase_date": row["purchase_date"],
        "first_use_date": row["first_use_date"],
        "retired_date": row["retired_date"],
        "notes": row["notes"],
    }


@mcp.tool()
def list_athlete_gear(gear_type: str | None = None, active_only: bool = True) -> list[dict]:
    """
    Liste aller Gear-Gegenstände (Schuhe, Rad, Uhr, ...) mit berechneter
    Gesamtdistanz (initial_distance_km + Summe der zugeordneten Aktivitäten —
    live berechnet, nicht blind hochgezählt).

    Args:
        gear_type:   Optional auf einen Typ filtern (z.B. "trail_shoe").
        active_only: True (Standard) = nur aktive, nicht stillgelegte Gegenstände.
    """
    if gear_type and gear_type not in _GEAR_TYPES:
        return [{"error": f"Unbekannter gear_type {gear_type!r}. Erlaubt: {sorted(_GEAR_TYPES)}"}]

    where = ["1=1"]
    params: list = []
    if gear_type:
        where.append("g.gear_type = %s")
        params.append(gear_type)
    if active_only:
        where.append("g.active = true")

    rows = _fetchall(
        f"""
        SELECT g.id, g.gear_type, g.brand, g.model, g.nickname, g.size, g.color,
               g.primary_surface, g.active, g.initial_distance_km, g.target_distance_km,
               g.purchase_date, g.first_use_date, g.retired_date, g.notes,
               COALESCE(SUM(u.distance_km), 0) AS activity_distance_km,
               COUNT(u.id) AS activity_count,
               MAX(t.date) AS last_used_at
        FROM athlete_gear g
        LEFT JOIN activity_gear_usage u ON u.gear_id = g.id
        LEFT JOIN trainings t ON t.id = u.training_id
        WHERE {' AND '.join(where)}
        GROUP BY g.id
        ORDER BY g.gear_type, g.id
        """,
        tuple(params),
    )
    return [_gear_row_out(r) for r in rows]


@mcp.tool()
def create_athlete_gear(
    gear_type: str,
    brand: str | None = None,
    model: str | None = None,
    nickname: str | None = None,
    size: str | None = None,
    color: str | None = None,
    primary_surface: str | None = None,
    purchase_date: str | None = None,
    first_use_date: str | None = None,
    initial_distance_km: float = 0,
    target_distance_km: float | None = None,
    notes: str | None = None,
) -> dict:
    """
    Legt einen neuen Gear-Gegenstand an (z.B. einen neuen Trailschuh).

    Args:
        gear_type: Einer von running_shoe, trail_shoe, hiking_shoe, bicycle,
                   watch, heart_rate_sensor, vest, poles, other.
        initial_distance_km: Bereits vor CAIRN gelaufene/gefahrene Kilometer
                   (z.B. beim Nacherfassen eines schon benutzten Schuhs).
    """
    if gear_type not in _GEAR_TYPES:
        return {"error": f"Unbekannter gear_type {gear_type!r}. Erlaubt: {sorted(_GEAR_TYPES)}"}

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM athlete_profile ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
            athlete_id = row[0] if row else None
            cur.execute(
                """INSERT INTO athlete_gear
                       (athlete_id, gear_type, brand, model, nickname, size, color,
                        primary_surface, purchase_date, first_use_date,
                        initial_distance_km, target_distance_km, notes)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (athlete_id, gear_type, brand, model, nickname, size, color, primary_surface,
                 purchase_date, first_use_date, initial_distance_km, target_distance_km, notes),
            )
            gear_id = cur.fetchone()[0]
        conn.commit()
    finally:
        conn.close()

    return {"created": True, "gear": _fetchone_gear(gear_id)}


def _fetchone_gear(gear_id: int) -> dict | None:
    rows = _fetchall(
        """
        SELECT g.id, g.gear_type, g.brand, g.model, g.nickname, g.size, g.color,
               g.primary_surface, g.active, g.initial_distance_km, g.target_distance_km,
               g.purchase_date, g.first_use_date, g.retired_date, g.notes,
               COALESCE(SUM(u.distance_km), 0) AS activity_distance_km,
               COUNT(u.id) AS activity_count,
               MAX(t.date) AS last_used_at
        FROM athlete_gear g
        LEFT JOIN activity_gear_usage u ON u.gear_id = g.id
        LEFT JOIN trainings t ON t.id = u.training_id
        WHERE g.id = %s
        GROUP BY g.id
        """,
        (gear_id,),
    )
    return _gear_row_out(rows[0]) if rows else None


@mcp.tool()
def update_athlete_gear(gear_id: int, patch: dict) -> dict:
    """
    Partielles Update eines Gear-Gegenstands. Nur im patch enthaltene Felder
    werden geändert. Erlaubte Felder: brand, model, nickname, size, color,
    primary_surface, purchase_date, first_use_date, target_distance_km,
    initial_distance_km, notes, active.
    """
    allowed = {"brand", "model", "nickname", "size", "color", "primary_surface",
               "purchase_date", "first_use_date", "target_distance_km",
               "initial_distance_km", "notes", "active"}
    unknown = [k for k in patch if k not in allowed]
    if unknown:
        return {"error": f"Unbekannte Felder: {unknown}. Erlaubt: {sorted(allowed)}"}
    if not patch:
        return {"error": "patch darf nicht leer sein."}

    existing = _fetchone_gear(gear_id)
    if not existing:
        return {"error": f"Gear id={gear_id} nicht gefunden."}

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            set_clauses = [f"{k} = %s" for k in patch] + ["updated_at = now()"]
            cur.execute(
                f"UPDATE athlete_gear SET {', '.join(set_clauses)} WHERE id = %s",
                [*patch.values(), gear_id],
            )
        conn.commit()
    finally:
        conn.close()

    return {"updated": True, "changed_fields": sorted(patch.keys()), "gear": _fetchone_gear(gear_id)}


@mcp.tool()
def retire_athlete_gear(gear_id: int, retired_date: str | None = None) -> dict:
    """Stillgelegten Gegenstand markieren (active=false) — Historie bleibt erhalten."""
    existing = _fetchone_gear(gear_id)
    if not existing:
        return {"error": f"Gear id={gear_id} nicht gefunden."}

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE athlete_gear SET active = false, retired_date = COALESCE(%s, CURRENT_DATE), "
                "updated_at = now() WHERE id = %s",
                (retired_date, gear_id),
            )
        conn.commit()
    finally:
        conn.close()

    return {"retired": True, "gear": _fetchone_gear(gear_id)}


def _resolve_training_id(activity_id: int | None, garmin_id: str | None) -> dict | None:
    if activity_id:
        return _fetchone("SELECT id, distance_km FROM trainings WHERE id = %s", (activity_id,))
    if garmin_id:
        return _fetchone("SELECT id, distance_km FROM trainings WHERE garmin_id = %s", (str(garmin_id),))
    return None


@mcp.tool()
def assign_activity_gear(
    activity_id: int | None = None,
    garmin_id: str | None = None,
    gear_id: int = 0,
    distance_km: float | None = None,
    assignment_source: str | None = None,
) -> dict:
    """
    Ordnet einer Aktivität ein Gear-Item zu (z.B. den benutzten Schuh) und
    aktualisiert dessen Gesamtkilometer. Idempotent: derselbe Aufruf mit
    identischer distance_km zählt die Kilometer nicht doppelt.

    Bei Schuhen (running_shoe/trail_shoe/hiking_shoe): pro Aktivität ist
    maximal ein Schuh zugeordnet — ein neuer Schuh ersetzt automatisch einen
    zuvor zugeordneten anderen Schuh derselben Aktivität (Schuhwechsel-Logik).
    Andere Gear-Typen (Uhr, Weste, ...) werden zusätzlich zugeordnet, nie ersetzt.

    Args:
        distance_km: None (Standard) = Distanz der Aktivität übernehmen.
                     Explizit angeben, wenn nur ein Teil der Strecke zählt.
    """
    training = _resolve_training_id(activity_id, garmin_id)
    if not training:
        return {"error": "Aktivität nicht gefunden."}
    gear = _fetchone(
        "SELECT id, gear_type, nickname, initial_distance_km, target_distance_km "
        "FROM athlete_gear WHERE id = %s", (gear_id,)
    )
    if not gear:
        return {"error": f"Gear id={gear_id} nicht gefunden."}

    tid = training["id"]
    resolved_distance = float(distance_km) if distance_km is not None else \
        (float(training["distance_km"]) if training["distance_km"] is not None else 0.0)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            def _current_total(gid: int) -> float:
                cur.execute(
                    "SELECT COALESCE(SUM(distance_km),0) FROM activity_gear_usage WHERE gear_id = %s",
                    (gid,),
                )
                return float(cur.fetchone()[0])

            cur.execute(
                "SELECT id, distance_km FROM activity_gear_usage WHERE training_id = %s AND gear_id = %s",
                (tid, gear_id),
            )
            existing_usage = cur.fetchone()

            if existing_usage and float(existing_usage[1]) == round(resolved_distance, 2):
                gear_full = _fetchone_gear(gear_id)
                return {
                    "assigned": True, "idempotent": True, "distance_added_km": 0,
                    "activity_id": tid, "activity_distance_km": resolved_distance,
                    "gear": gear_full, "replaced_gear": None, "warnings": [],
                }

            replaced_gear = None
            if gear["gear_type"] in _SHOE_GEAR_TYPES:
                cur.execute(
                    """SELECT g.id, g.nickname FROM activity_gear_usage u
                           JOIN athlete_gear g ON g.id = u.gear_id
                       WHERE u.training_id = %s AND g.gear_type IN %s AND g.id != %s""",
                    (tid, tuple(_SHOE_GEAR_TYPES), gear_id),
                )
                old_shoe = cur.fetchone()
                if old_shoe:
                    old_gear_id, old_nickname = old_shoe
                    prev_total = _current_total(old_gear_id)
                    cur.execute(
                        "DELETE FROM activity_gear_usage WHERE training_id = %s AND gear_id = %s",
                        (tid, old_gear_id),
                    )
                    new_total = _current_total(old_gear_id)
                    replaced_gear = {
                        "id": old_gear_id, "nickname": old_nickname,
                        "previous_total_distance_km": round(prev_total, 2),
                        "new_total_distance_km": round(new_total, 2),
                    }

            previous_total = _current_total(gear_id)
            cur.execute(
                """INSERT INTO activity_gear_usage (training_id, gear_id, distance_km, assignment_source)
                       VALUES (%s, %s, %s, %s)
                   ON CONFLICT (training_id, gear_id) DO UPDATE SET
                       distance_km = EXCLUDED.distance_km,
                       assignment_source = EXCLUDED.assignment_source,
                       updated_at = now()""",
                (tid, gear_id, round(resolved_distance, 2), assignment_source),
            )
            new_total = _current_total(gear_id)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    gear_full = _fetchone_gear(gear_id)
    return {
        "assigned": True, "idempotent": False,
        "activity_id": tid, "activity_distance_km": round(resolved_distance, 2),
        "gear": {
            "id": gear_id, "nickname": gear["nickname"],
            "previous_total_distance_km": round(previous_total, 2),
            "new_total_distance_km": round(new_total, 2),
            "target_distance_km": float(gear["target_distance_km"]) if gear["target_distance_km"] is not None else None,
            "remaining_distance_km": round(float(gear["target_distance_km"]) - new_total, 2)
                if gear["target_distance_km"] is not None else None,
        },
        "replaced_gear": replaced_gear,
        "warnings": [],
    }


@mcp.tool()
def remove_activity_gear(
    activity_id: int | None = None,
    garmin_id: str | None = None,
    gear_id: int = 0,
) -> dict:
    """Entfernt eine Gear-Zuordnung von einer Aktivität (Korrektur einer Fehlzuordnung)."""
    training = _resolve_training_id(activity_id, garmin_id)
    if not training:
        return {"error": "Aktivität nicht gefunden."}

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM activity_gear_usage WHERE training_id = %s AND gear_id = %s RETURNING id",
                (training["id"], gear_id),
            )
            removed = cur.fetchone() is not None
        conn.commit()
    finally:
        conn.close()

    return {"removed": removed, "activity_id": training["id"], "gear_id": gear_id,
            "gear": _fetchone_gear(gear_id)}


@mcp.tool()
def get_recent_activities(days: int = 28) -> list[dict]:
    """
    Return recent Garmin activities from the last N days (default 28).
    Includes distance, duration, heart rate.
    """
    return _fetchall(
        """
        SELECT id, date, type, notes,
               distance_km, duration_minutes,
               heart_rate_avg, garmin_id
        FROM trainings
        WHERE date >= CURRENT_DATE - (INTERVAL '1 day' * %s)
        ORDER BY date DESC
        """,
        (days,),
    )


@mcp.tool()
def get_training_summary(weeks: int = 4) -> dict:
    """
    Return weekly training volume for the last N weeks (default 4).
    Shows session count, total km, total duration, and average heart rate
    per week.
    """
    rows = _fetchall(
        """
        SELECT
            date_trunc('week', date)::date          AS week_start,
            COUNT(*)                                 AS sessions,
            ROUND(SUM(distance_km)::numeric, 2)     AS total_km,
            SUM(duration_minutes)                   AS total_duration_min,
            ROUND(AVG(heart_rate_avg)::numeric, 1)   AS avg_hr
        FROM trainings
        WHERE date >= CURRENT_DATE - (INTERVAL '1 week' * %s)
        GROUP BY week_start
        ORDER BY week_start DESC
        """,
        (weeks,),
    )
    return {"weeks": rows, "period_weeks": weeks}


@mcp.tool()
def get_health_data(days: int = 7) -> list[dict]:
    """
    Return daily health metrics for the last N days (default 7).
    Includes sleep, HRV status, body battery, and resting heart rate.
    """
    return _fetchall(
        """
        SELECT *
        FROM daily_logs
        WHERE date >= CURRENT_DATE - (INTERVAL '1 day' * %s)
        ORDER BY date DESC
        """,
        (days,),
    )


@mcp.tool()
def get_checkins(days: int = 14) -> list[dict]:
    """
    Return athlete subjective check-ins and free-text notes for the last N days (default 14).
    Shows feel, structured notes, and athlete free-text entries.
    """
    return _fetchall(
        """
        SELECT date, feel, notes, athlete_text
        FROM daily_logs
        WHERE date >= CURRENT_DATE - (INTERVAL '1 day' * %s)
          AND (feel IS NOT NULL OR notes IS NOT NULL OR athlete_text IS NOT NULL)
        ORDER BY date DESC
        """,
        (days,),
    )


@mcp.tool()
def get_races_and_goals() -> dict:
    """Return upcoming races, training goals, and current plan targets."""
    plans = _fetchall(
        """
        SELECT id, goal_type, race_name, race_date, race_distance_km,
               total_weeks, current_week, status, created_at
        FROM plans
        ORDER BY created_at DESC
        LIMIT 5
        """
    )
    # athlete_profile has no goal/current_fitness_level/weekly_volume_km columns —
    # long_term_goals is the only goal-related field that actually exists.
    profile = _fetchone(
        "SELECT long_term_goals FROM athlete_profile ORDER BY id DESC LIMIT 1"
    )
    return {"plans": plans, "profile_goals": profile}


@mcp.tool()
def get_planned_workouts(days: int = 14, start_date: str | None = None) -> list[dict]:
    """
    Return planned training sessions for N days starting at start_date
    (default: today). Includes session type, distance, target zone,
    structure, elevation_gain_m (CAIRN-only planning metadata, never sent to
    Garmin), and Garmin workout ID if already pushed.

    Args:
        days:       Number of days to include, counted from start_date.
        start_date: Optional YYYY-MM-DD. Omit for today (previous default
                    behavior unchanged). E.g. start_date="2026-08-16", days=3
                    returns sessions from 2026-08-16 through 2026-08-18.
    """
    if start_date:
        try:
            datetime.date.fromisoformat(start_date)
        except ValueError:
            return [{"error": f"start_date {start_date!r} ist kein gueltiges YYYY-MM-DD"}]
        range_start_sql = "%s::date"
        params = (start_date, start_date, days)
    else:
        range_start_sql = "CURRENT_DATE"
        params = (days,)

    return _fetchall(
        f"""
        SELECT id,
               (week_date + (day_of_week - 1) * INTERVAL '1 day')::date AS session_date,
               session_type, session_zone, distance_km, duration_min,
               notes, phase, garmin_workout_id, workout_steps, plan_week,
               elevation_gain_m, external_id, sport,
               km_factor, actual_distance_km, linked_garmin_activity_id,
               sync_status, sync_target
        FROM training_plan
        WHERE (week_date + (day_of_week - 1) * INTERVAL '1 day')::date
              BETWEEN {range_start_sql} AND {range_start_sql} + (INTERVAL '1 day' * %s)
        ORDER BY session_date ASC
        """,
        params,
    )


@mcp.tool()
def get_hevy_workouts(days: int = 60) -> list[dict]:
    """
    Return completed Hevy strength workouts from the last N days, with their
    logged exercises. Read-only, CAIRN DB only — reads trainings/hevy_exercises
    as already synced by data/hevy_sync.py, no live Hevy API call.

    Returns: [{date, hevy_id, title, duration_minutes,
               exercises: [{exercise_name, sets, reps_per_set, weight_kg_per_set}]}]
    Newest workout first; exercises within a workout in logged order.
    """
    rows = _fetchall(
        """
        SELECT t.id AS training_id, t.date, t.hevy_id, t.notes, t.duration_minutes,
               e.exercise_name, e.sets, e.reps_per_set, e.weight_kg_per_set, e.exercise_index
        FROM trainings t
        LEFT JOIN hevy_exercises e ON e.training_id = t.id
        WHERE t.type = 'WeightTraining'
          AND t.hevy_id IS NOT NULL
          AND t.date >= CURRENT_DATE - (INTERVAL '1 day' * %s)
        ORDER BY t.date DESC, t.id DESC, e.exercise_index ASC
        """,
        (days,),
    )

    workouts: dict[int, dict] = {}
    order: list[int] = []
    for row in rows:
        tid = row["training_id"]
        if tid not in workouts:
            workouts[tid] = {
                "date": row["date"], "hevy_id": row["hevy_id"], "title": row["notes"],
                "duration_minutes": row["duration_minutes"], "exercises": [],
            }
            order.append(tid)
        if row["exercise_name"] is not None:
            workouts[tid]["exercises"].append({
                "exercise_name": row["exercise_name"], "sets": row["sets"],
                "reps_per_set": row["reps_per_set"], "weight_kg_per_set": row["weight_kg_per_set"],
            })
    return [workouts[tid] for tid in order]


@mcp.tool()
def get_hevy_routines() -> list[dict]:
    """
    Return all CAIRN-known Hevy routines (synced via data/hevy_routines_sync.py
    into cairn_routines). Read-only, CAIRN DB only — no live Hevy API call.

    Returns: [{title, exercises: [str, ...]}]
    """
    return _fetchall("SELECT title, exercises FROM cairn_routines ORDER BY title")


@mcp.tool()
def list_garmin_workouts(start_date: str, end_date: str) -> list[dict]:
    """
    List existing Garmin calendar entries (workouts) in a date range, with their
    Garmin workout_id and schedule_id. Read-only — makes real Garmin API calls
    (client.get_scheduled_workouts per month) but writes nothing.

    Use this before upsert_training_block / reconciliation to see what's
    already on the Garmin calendar and avoid blind overwrites or duplicates.

    Args:
        start_date: YYYY-MM-DD
        end_date:   YYYY-MM-DD (inclusive)
    Returns:
        [{date, title, workout_id, schedule_id, sport_type}]
    """
    start = datetime.date.fromisoformat(start_date)
    end = datetime.date.fromisoformat(end_date)
    if end < start:
        return []

    client = garmin_client()
    months: set[tuple[int, int]] = set()
    cursor = start.replace(day=1)
    while cursor <= end:
        months.add((cursor.year, cursor.month))
        cursor = (cursor.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)

    seen_schedule_ids: set[int] = set()
    entries: list[dict] = []
    for year, month in sorted(months):
        data = client.get_scheduled_workouts(year, month)
        for item in data.get("calendarItems", []):
            if item.get("itemType") != "workout":
                continue
            item_date_str = item.get("date")
            if not item_date_str:
                continue
            item_date = datetime.date.fromisoformat(item_date_str)
            if not (start <= item_date <= end):
                continue
            schedule_id = item.get("id")
            if schedule_id in seen_schedule_ids:
                continue
            seen_schedule_ids.add(schedule_id)
            sport = item.get("sportTypeKey") or (item.get("sportType") or {}).get("sportTypeKey")
            entries.append({
                "date": item_date_str,
                "title": item.get("title"),
                "workout_id": item.get("workoutId"),
                "schedule_id": schedule_id,
                "sport_type": sport,
            })
    entries.sort(key=lambda e: e["date"])
    return entries


# ── Garmin write tools ─────────────────────────────────────────────────────────

@mcp.tool()
def preview_garmin_workout(workout_def: dict, sport: str = "running") -> dict:
    """
    Validate a workout definition and return a parsed preview — zero network calls.
    Always call this before create_garmin_workout to confirm structure.

    sport: "running" | "cycling" only. Strength Training must NEVER be pushed to
    Garmin (Hevy is the sole source for strength) — this is hard-rejected in
    coach/garmin_push.py, both by sport value and by workout_def['session_type'].

    workout_def shape:
    {
        "name": "CAIRN – 4x2min Z3",
        "estimated_duration_secs": 2160,
        "description": "optional",
        "optional": false,
        "steps": [
            {"type": "warmup", "duration_secs": 600, "target": {"type": "hr_zone", "zone": 2}},
            {"type": "repeat", "iterations": 4, "steps": [
                {"type": "interval", "duration_secs": 120, "target": {"type": "hr_zone", "zone": 3}},
                {"type": "recovery", "duration_secs": 120, "target": {"type": "hr_zone", "zone": 1}}
            ]},
            {"type": "cooldown", "duration_secs": 600, "target": {"type": "hr_zone", "zone": 2}}
        ]
    }
    """
    try:
        payload = build_workout_payload(workout_def, sport=sport)
        segment = payload.workoutSegments[0]
        steps_preview = []
        for i, step in enumerate(segment.workoutSteps):
            entry: dict[str, Any] = {"order": i + 1}
            if hasattr(step, "numberOfIterations"):
                entry["type"] = "repeat"
                entry["iterations"] = step.numberOfIterations
                entry["inner_steps"] = len(step.workoutSteps)
            else:
                step_type = getattr(step, "stepType", {})
                entry["type"] = step_type.get("stepTypeKey", "unknown") if isinstance(step_type, dict) else str(step_type)
                entry["duration_secs"] = getattr(step, "endConditionValue", None)
                target_type = getattr(step, "targetType", {})
                entry["target"] = (
                    target_type.get("workoutTargetTypeKey", "no.target")
                    if isinstance(target_type, dict) else str(target_type)
                )
                zone = getattr(step, "zoneNumber", None)
                if zone is not None:
                    entry["zone"] = zone
            steps_preview.append(entry)
        return {
            "valid": True,
            "sport": sport,
            "workout_name": payload.workoutName,
            "estimated_duration_secs": payload.estimatedDurationInSecs,
            "description": payload.description,
            "step_count": len(segment.workoutSteps),
            "steps": steps_preview,
        }
    except Exception as exc:
        return {"valid": False, "error": str(exc)}


@mcp.tool()
def create_garmin_workout(
    workout_def: dict,
    date_str: str,
    external_id: str,
    sport: str = "running",
) -> dict:
    """
    Create and schedule a structured workout on Garmin Connect.
    Idempotent: a duplicate external_id returns the existing record without re-uploading.

    Args:
        workout_def:  Workout definition (validate first with preview_garmin_workout).
        date_str:     Target date in YYYY-MM-DD format.
        external_id:  Unique caller ID, e.g. "plan_session_42_2026-08-15".
                      Use the training_plan row ID + date for natural idempotency.
        sport:        "running" | "cycling" only. Strength Training must never
                      reach Garmin — Hevy is the sole source for strength training.
    Returns:
        garmin_workout_id, garmin_schedule_id, workout_name, scheduled_date
    """
    conn = get_connection()
    try:
        # Idempotency: return existing success record if present
        with conn.cursor() as cur:
            cur.execute(
                "SELECT garmin_workout_id, garmin_schedule_id, workout_name, scheduled_date "
                "FROM garmin_mcp_log WHERE external_id = %s AND status = 'success'",
                (external_id,),
            )
            row = cur.fetchone()
        if row:
            logger.info("Idempotent hit: external_id=%s workout_id=%s", external_id, row[0])
            return {
                "garmin_workout_id": row[0],
                "garmin_schedule_id": row[1],
                "workout_name": row[2],
                "scheduled_date": row[3].isoformat() if hasattr(row[3], "isoformat") else str(row[3]),
                "idempotent": True,
            }

        # Insert pending log (ON CONFLICT DO NOTHING: safe for concurrent retries)
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO garmin_mcp_log
                       (external_id, action, status, workout_name, scheduled_date, metadata)
                   VALUES (%s, 'push', 'pending', %s, %s, %s)
                   ON CONFLICT (external_id) DO NOTHING""",
                (
                    external_id,
                    workout_def.get("name"),
                    date_str,
                    json.dumps({"source": "mcp_create_garmin_workout"}),
                ),
            )
            conn.commit()

        # Execute Garmin push (create + schedule)
        result = push_workout(workout_def, date_str, sport=sport)

        # Mark success
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE garmin_mcp_log
                   SET garmin_workout_id = %s,
                       garmin_schedule_id = %s,
                       status = 'success',
                       workout_name = %s
                   WHERE external_id = %s""",
                (
                    result["garmin_workout_id"],
                    result["garmin_schedule_id"],
                    result.get("workout_name"),
                    external_id,
                ),
            )
            conn.commit()

        logger.info(
            "MCP workout pushed: external_id=%s garmin_workout_id=%s date=%s",
            external_id,
            result["garmin_workout_id"],
            date_str,
        )
        return result

    except GarminPushError as exc:
        # Log failure — never log credential values
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE garmin_mcp_log SET status='failed', last_error=%s "
                    "WHERE external_id=%s",
                    (f"{type(exc).__name__}: {exc}", external_id),
                )
                conn.commit()
        except Exception:
            pass
        raise
    finally:
        conn.close()


@mcp.tool()
def move_garmin_workout(
    garmin_workout_id: int,
    new_date_str: str,
    old_schedule_id: int | None = None,
) -> dict:
    """
    Reschedule an existing Garmin workout to a new date.
    Deletes the old schedule entry (if provided) and creates a new one.

    Args:
        garmin_workout_id:  Garmin workout ID (from create_garmin_workout).
        new_date_str:       New target date in YYYY-MM-DD format.
        old_schedule_id:    Garmin schedule ID to remove (from garmin_mcp_log). Optional.
    """
    client = garmin_client()
    if old_schedule_id is not None:
        try:
            # garminconnect 0.3.6 has no delete_workout_schedule() method —
            # the actual API is unschedule_workout(scheduled_workout_id).
            # Confirmed live against the real Garmin API on 2026-08-14.
            client.unschedule_workout(old_schedule_id)
            logger.info("Deleted old Garmin schedule_id=%s", old_schedule_id)
        except Exception as exc:
            logger.warning(
                "Could not delete old schedule %s: %s — continuing with reschedule",
                old_schedule_id,
                type(exc).__name__,
            )
    return schedule_workout(garmin_workout_id, new_date_str, client=client)


def _clear_training_plan_sync_state(conn, garmin_workout_id: int) -> int:
    """
    Räumt training_plan nach einer erfolgreichen Garmin-Löschung auf.
    training_plan hat keine garmin_schedule_id-Spalte (nur garmin_workout_id
    VARCHAR(100) — Schedule-IDs leben ausschließlich in garmin_mcp_log), daher
    nicht Teil dieses UPDATEs. garmin_workout_id wird als str verglichen,
    passend zum tatsächlichen Spaltentyp.
    """
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE training_plan
               SET garmin_workout_id = NULL,
                   sync_status = NULL,
                   sync_error = NULL,
                   content_hash = NULL,
                   last_synced_at = NULL
               WHERE garmin_workout_id = %s""",
            (str(garmin_workout_id),),
        )
        return cur.rowcount


@mcp.tool()
def delete_garmin_workout(garmin_workout_id: int) -> dict:
    """
    Delete a workout from Garmin Connect (removes the workout definition and all schedule entries).
    Also clears the matching training_plan row's sync state (garmin_workout_id,
    sync_status, sync_error, content_hash, last_synced_at), so a later
    push_sessions_to_garmin treats it as never-pushed rather than stale-synced.

    Args:
        garmin_workout_id: Garmin workout ID to delete (from create_garmin_workout or garmin_mcp_log).
    """
    client = garmin_client()
    try:
        client.delete_workout(garmin_workout_id)
        logger.info("Deleted Garmin workout_id=%s", garmin_workout_id)
    except Exception as exc:
        raise RuntimeError(
            f"Failed to delete Garmin workout {garmin_workout_id}: {type(exc).__name__}"
        ) from exc

    conn = get_connection()
    try:
        db_rows_cleared = _clear_training_plan_sync_state(conn, garmin_workout_id)
        conn.commit()
    finally:
        conn.close()

    return {"deleted": True, "garmin_workout_id": garmin_workout_id, "db_rows_cleared": db_rows_cleared}


@mcp.tool()
def bulk_delete_garmin_workouts(garmin_workout_ids: list[int]) -> dict:
    """
    Delete multiple Garmin workouts in one call — a single Garmin login for
    the whole batch, isolated try/except per ID (one failure never blocks
    the rest). Clears each deleted workout's training_plan sync state, same
    as delete_garmin_workout.

    Args:
        garmin_workout_ids: Garmin workout IDs to delete.
    Returns:
        {deleted: [ids...], failed: [{garmin_workout_id, error}...], db_rows_cleared: int}
    """
    client = garmin_client()
    conn = get_connection()
    deleted: list[int] = []
    failed: list[dict] = []
    db_rows_cleared = 0

    try:
        for gw_id in garmin_workout_ids:
            try:
                client.delete_workout(gw_id)
                logger.info("Deleted Garmin workout_id=%s", gw_id)
                db_rows_cleared += _clear_training_plan_sync_state(conn, gw_id)
                conn.commit()
                deleted.append(gw_id)
            except Exception as exc:
                conn.rollback()
                logger.error("Failed to delete Garmin workout_id=%s: %s", gw_id, exc)
                failed.append({"garmin_workout_id": gw_id, "error": f"{type(exc).__name__}: {exc}"})
    finally:
        conn.close()

    return {"deleted": deleted, "failed": failed, "db_rows_cleared": db_rows_cleared}


@mcp.tool()
def get_activity_analysis_data(
    activity_id: int | None = None,
    garmin_id: str | None = None,
    include_stream: bool = True,
    stream_resolution_m: int = 50,
) -> dict:
    """
    Vollständige, deterministische Analyse-Daten für eine abgeschlossene
    Einheit — die objektive Grundlage, auf der ChatGPT die individuelle
    Coach-Interpretation aufbaut (siehe save_activity_coach_analysis).
    Zuerst get_recent_activities() aufrufen um die activity_id zu erhalten.

    Args:
        activity_id:         CAIRN trainings.id
        garmin_id:           Alternativ: Garmin activityId als String
        include_stream:      True = Activity Stream mitliefern (Standard).
        stream_resolution_m: Ziel-Punktabstand des zurückgegebenen Streams in
                             Metern (Standard 50). Rohdaten bleiben in der DB
                             unverändert — nur die Tool-Antwort wird ausgedünnt.

    Gibt zurück: summary, native_laps, km_splits, stream, route,
    trail_metrics, trail_segments, hr_zones, recovery_context, data_quality.
    Fehlende Werte sind null — nichts wird erfunden. Wenn Detaildaten fehlen,
    zuerst sync_activity_details aufrufen.
    """
    if activity_id:
        training = _fetchone(
            "SELECT id, date, type, notes, distance_km, duration_minutes, "
            "heart_rate_avg, garmin_id, elevation_gain_m, elevation_loss_m, "
            "max_hr, avg_cadence, training_load, aerobic_effect, anaerobic_effect, "
            "vo2max_estimate, avg_power "
            "FROM trainings WHERE id = %s",
            (activity_id,)
        )
    elif garmin_id:
        training = _fetchone(
            "SELECT id, date, type, notes, distance_km, duration_minutes, "
            "heart_rate_avg, garmin_id, elevation_gain_m, elevation_loss_m, "
            "max_hr, avg_cadence, training_load, aerobic_effect, anaerobic_effect, "
            "vo2max_estimate, avg_power "
            "FROM trainings WHERE garmin_id = %s",
            (str(garmin_id),)
        )
    else:
        return {"error": "activity_id oder garmin_id erforderlich"}

    if not training:
        return {"error": "Aktivität nicht gefunden"}

    tid = training["id"]
    conn = get_connection()
    try:
        native_laps = build_native_laps(conn, tid)
        km_splits, km_splits_reason = compute_km_splits(conn, tid)
        route = build_route(conn, tid)
        trail_metrics, trail_segments = build_trail_metrics_and_segments(conn, tid)
        summary = build_summary(conn, training)
        recovery_context = build_recovery_context(conn, training["date"]) if training.get("date") else None
        hr_zones = build_hr_zones(conn, tid)

        # Stream-basierte Werte haben Vorrang vor evtl. veralteten trainings-
        # Spalten (elevation_gain_m/loss_m dort oft NULL bei Altimporten).
        if trail_metrics.get("ascent_m") is not None:
            summary["elevation_gain_m"] = trail_metrics["ascent_m"]
        if trail_metrics.get("descent_m") is not None:
            summary["elevation_loss_m"] = trail_metrics["descent_m"]
        if trail_metrics.get("min_elevation_m") is not None:
            summary["min_elevation_m"] = trail_metrics["min_elevation_m"]
        if trail_metrics.get("max_elevation_m") is not None:
            summary["max_elevation_m"] = trail_metrics["max_elevation_m"]
        # trainings-Spalten sind bei Altimporten oft leer, obwohl Garmins
        # native Laps den Wert bereits liefern (z.B. maxHR aus lapDTOs).
        if summary.get("max_hr") is None and native_laps:
            lap_max_hrs = [lap["max_hr"] for lap in native_laps if lap.get("max_hr") is not None]
            if lap_max_hrs:
                summary["max_hr"] = max(lap_max_hrs)
        if summary.get("avg_cadence") is None and native_laps:
            lap_cadences = [lap["avg_cadence"] for lap in native_laps if lap.get("avg_cadence") is not None]
            if lap_cadences:
                summary["avg_cadence"] = round(sum(lap_cadences) / len(lap_cadences))

        # ── Stream (distanzbasiert, ausgedünnt auf stream_resolution_m) ──
        stream_out = None
        hr_stream_available = pace_stream_available = elevation_stream_available = False
        power_available = False
        stream_point_count = 0

        if include_stream:
            resolution = max(10, min(500, stream_resolution_m))
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT distance_m, elapsed_s, moving_s, heart_rate, speed_ms, "
                    "cadence, power, elevation_m, lat, lon "
                    "FROM activity_stream WHERE training_id = %s ORDER BY distance_m",
                    (tid,)
                )
                cols = [d[0] for d in cur.description]
                raw_stream = [dict(zip(cols, row)) for row in cur.fetchall()]

            if raw_stream:
                points = []
                last_d = -resolution
                orig_count = len(raw_stream)
                for r in raw_stream:
                    d = r["distance_m"]
                    if d >= last_d + resolution:
                        row_out = [
                            None,  # timestamp_ms — nicht separat gespeichert (elapsed_s deckt das ab)
                            round(r["elapsed_s"], 1) if r["elapsed_s"] is not None else None,
                            round(r["moving_s"], 1) if r["moving_s"] is not None else None,
                            round(d, 1),
                            r["heart_rate"],
                            round(r["speed_ms"], 3) if r["speed_ms"] is not None else None,
                            round(1000 / r["speed_ms"]) if r["speed_ms"] else None,
                            r["cadence"],
                            r["power"],
                            round(r["elevation_m"], 1) if r["elevation_m"] is not None else None,
                            None,  # grade_pct — bewusst nicht pro Punkt berechnet (siehe trail_segments)
                            r["lat"], r["lon"],
                        ]
                        points.append(row_out)
                        if r["heart_rate"] is not None: hr_stream_available = True
                        if r["speed_ms"] is not None: pace_stream_available = True
                        if r["elevation_m"] is not None: elevation_stream_available = True
                        if r["power"] is not None: power_available = True
                        last_d = d
                stream_point_count = len(points)
                stream_out = {
                    "source": "garmin_activity_stream",
                    "resolution_requested_m": stream_resolution_m,
                    "resolution_actual_m": resolution,
                    "original_point_count": orig_count,
                    "returned_point_count": stream_point_count,
                    "fields": ["timestamp_ms", "elapsed_s", "timer_time_s", "distance_m", "heart_rate",
                               "speed_mps", "pace_seconds_per_km", "cadence", "power", "altitude_m",
                               "grade_pct", "latitude", "longitude"],
                    "data": points,
                }
            else:
                hr_fallback = _fetchall(
                    "SELECT point_index, timestamp_ms, heart_rate FROM hr_tracks "
                    "WHERE training_id = %s ORDER BY point_index",
                    (tid,)
                )
                if hr_fallback:
                    hr_stream_available = True
                    stream_point_count = len(hr_fallback)
                    stream_out = {
                        "source": "hr_tracks_fallback",
                        "note": "activity_stream noch nicht importiert — nur HF auf Zeitachse, keine "
                                "Distanz-Korrelation möglich. sync_activity_details aufrufen, um den "
                                "vollen Distanz-Stream nachzuladen.",
                        "fallback": True,
                        "resolution_requested_m": stream_resolution_m,
                        "point_count": stream_point_count,
                        "fields": ["point_index", "timestamp_ms", "heart_rate"],
                        "data": [[p["point_index"], p["timestamp_ms"], p["heart_rate"]] for p in hr_fallback],
                    }
                else:
                    stream_out = {"available": False, "reason": "Kein Stream vorhanden"}

        gps_route_available = bool(route.get("available"))

        data_quality = build_data_quality(
            native_laps=native_laps, km_splits=km_splits,
            stream_available=stream_out is not None and stream_out.get("available") is not False,
            hr_stream_available=hr_stream_available, pace_stream_available=pace_stream_available,
            elevation_stream_available=elevation_stream_available, gps_route_available=gps_route_available,
            power_available=power_available, hr_zones_available=hr_zones is not None,
            summary=summary, hr_stream_point_count=stream_point_count,
        )
        if km_splits_reason:
            data_quality["warnings"].append(km_splits_reason)

        summary["stream_available"] = data_quality["hr_stream_available"] or data_quality["pace_stream_available"]
        summary["stream_source"] = stream_out.get("source") if stream_out else None

        source_data_hash = compute_source_data_hash(
            summary=summary, native_laps=native_laps, km_splits=km_splits, trail_metrics=trail_metrics,
        )

        return {
            "summary": summary,
            "native_laps": native_laps,
            "km_splits": km_splits,
            "km_splits_note": km_splits_reason,
            "stream": stream_out,
            "route": route,
            "trail_metrics": trail_metrics,
            "trail_segments": trail_segments,
            "hr_zones": hr_zones,
            "recovery_context": recovery_context,
            "data_quality": data_quality,
            "source_data_hash": source_data_hash,
        }
    finally:
        conn.close()


# ── Training-Block-Verwaltung (Rennen + Sessions + Milestones) ─────────────────

def _validate_session_fields(idx: int, s: dict) -> list[str]:
    errors = []
    if not s.get("external_id"):
        errors.append(f"Session {idx}: external_id fehlt")
    date_str = s.get("date")
    if not date_str:
        errors.append(f"Session {idx}: date fehlt")
    else:
        try:
            datetime.date.fromisoformat(date_str)
        except ValueError:
            errors.append(f"Session {idx}: date {date_str!r} ist kein gueltiges YYYY-MM-DD")
    if not s.get("session_type"):
        errors.append(f"Session {idx}: session_type fehlt")
    return errors


def _week_date_and_dow(session_date: datetime.date) -> tuple[datetime.date, int]:
    """training_plan speichert week_date (Montag der Woche) + day_of_week (1=Mo..7=So)."""
    dow = session_date.isoweekday()
    week_date = session_date - datetime.timedelta(days=dow - 1)
    return week_date, dow


@mcp.tool()
def preview_training_block(plan: dict) -> dict:
    """
    Validate a full training block (race + sessions) — NO writes, read-only.
    Always call this before upsert_training_block and show the result to the
    athlete for confirmation, especially when race_date_change is non-null.

    plan shape:
    {
      "race": {"name": str, "race_date": "YYYY-MM-DD", "race_distance_km": float, "goal_type": str},
      "sessions": [
        {"external_id": str, "date": "YYYY-MM-DD", "session_type": str,
         "distance_km": float, "duration_min": int, "session_zone": str, "notes": str,
         "elevation_gain_m": int},  # optional, CAIRN-only — never sent to Garmin
        ...
      ]
    }

    Returns: valid, summary (create/update/unchanged), race_date_change,
    conflicts (hard errors — missing fields, duplicate external_id within the
    block), warnings (collisions, empty block), sessions_preview.
    """
    conflicts: list[str] = []
    warnings: list[str] = []
    race = plan.get("race") or {}
    sessions = plan.get("sessions") or []

    if not race.get("race_date"):
        conflicts.append("race.race_date fehlt")
    if not sessions:
        warnings.append("Keine Sessions im Block")

    for i, s in enumerate(sessions):
        conflicts.extend(_validate_session_fields(i, s))

    # Duplikate innerhalb des eingereichten Blocks
    ext_ids = [s.get("external_id") for s in sessions if s.get("external_id")]
    for dupe in sorted({x for x in ext_ids if ext_ids.count(x) > 1}):
        conflicts.append(f"external_id {dupe!r} kommt mehrfach im selben Block vor")

    # Kollisionen: gleiches Datum + gleicher session_type mehrfach im Block
    # (unterschiedliche session_types am selben Tag sind normal, z.B. Gym + Lauf)
    date_type_pairs = [(s.get("date"), s.get("session_type")) for s in sessions if s.get("date") and s.get("session_type")]
    for pair in sorted({p for p in date_type_pairs if date_type_pairs.count(p) > 1}):
        warnings.append(f"Kollision: {pair[1]!r} kommt am {pair[0]} mehrfach im selben Block vor")

    # Race-Date-Aenderung gegen den aktuell aktiven Plan — eigenes Feld, blockiert
    # 'valid' NICHT (strukturell ok), aber upsert_training_block verlangt dafuer
    # explizit confirm_race_date_change=True.
    active_plan = _fetchone(
        "SELECT id, race_date, race_name FROM plans WHERE status='active' ORDER BY created_at DESC LIMIT 1"
    )
    race_date_change = None
    if active_plan and race.get("race_date"):
        old_date = active_plan["race_date"]
        old_date_str = old_date.isoformat() if hasattr(old_date, "isoformat") else str(old_date)
        new_date_str = race["race_date"]
        if old_date_str != new_date_str:
            race_date_change = {
                "plan_id": active_plan["id"],
                "race_name": active_plan["race_name"],
                "old_race_date": old_date_str,
                "new_race_date": new_date_str,
            }
            warnings.append(
                f"Race-Date-Aenderung: aktiver Plan (id={active_plan['id']}, {active_plan['race_name']}) "
                f"hat race_date={old_date_str}, dieser Block will {new_date_str} — "
                f"upsert_training_block lehnt das ohne confirm_race_date_change=True ab."
            )

    # Sessions klassifizieren: create / update / unchanged, gegen echte DB-Zeilen
    create_count = update_count = unchanged_count = 0
    sessions_preview = []
    for s in sessions:
        ext_id = s.get("external_id")
        existing = _fetchone(
            "SELECT id, session_type, distance_km, duration_min, session_zone "
            "FROM training_plan WHERE external_id = %s", (ext_id,)
        ) if ext_id else None

        action = "create"
        if existing:
            existing_distance = float(existing["distance_km"]) if existing.get("distance_km") is not None else None
            same = (
                str(existing.get("session_type")) == str(s.get("session_type"))
                and existing_distance == s.get("distance_km")
                and existing.get("duration_min") == s.get("duration_min")
                and (existing.get("session_zone") or None) == (s.get("session_zone") or None)
            )
            action = "unchanged" if same else "update"

        if action == "create":
            create_count += 1
        elif action == "update":
            update_count += 1
        else:
            unchanged_count += 1

        routing = classify_for_push(s.get("session_type", ""), s.get("sport_hint"))
        sessions_preview.append({
            "external_id": ext_id, "date": s.get("date"), "session_type": s.get("session_type"),
            "action": action, "existing_training_plan_id": existing["id"] if existing else None,
            "sync_target": routing["sync_target"], "garmin_sport": routing["garmin_sport"],
        })

        if routing["sync_target"] == "hevy" and s.get("hevy_routine_key"):
            match = _fetchone("SELECT 1 AS ok FROM cairn_routines WHERE title = %s", (s["hevy_routine_key"],))
            if not match:
                warnings.append(
                    f"Session {ext_id!r}: hevy_routine_key {s['hevy_routine_key']!r} "
                    f"nicht in cairn_routines gefunden."
                )

    routing_split = split_training_block(sessions)
    routing_summary = {k: len(v) for k, v in routing_split.items()}

    return {
        "valid": len(conflicts) == 0,
        "summary": {"create": create_count, "update": update_count, "unchanged": unchanged_count, "total": len(sessions)},
        "routing_summary": routing_summary,
        "race_date_change": race_date_change,
        "conflicts": conflicts,
        "warnings": warnings,
        "sessions_preview": sessions_preview,
    }


@mcp.tool()
def upsert_planned_workout(
    external_id: str,
    date: str,
    session_type: str,
    sport: str | None = None,
    name: str | None = None,
    duration_min: int | None = None,
    distance_km: float | None = None,
    structure: dict | list | None = None,
    target: dict | None = None,
    notes: str | None = None,
    source: str | None = None,
    sync_target: str | None = None,
    elevation_gain_m: int | None = None,
    km_factor: float | None = None,
) -> dict:
    """
    Save a single planned session in CAIRN. Idempotent via external_id
    (ON CONFLICT DO UPDATE, never duplicates). CAIRN-only write — never
    calls Garmin or Hevy directly; call create_garmin_workout separately
    afterward for sessions where the result's sync_target == "garmin".

    Args:
        external_id:  Stable caller ID for idempotency.
        date:         YYYY-MM-DD.
        session_type: e.g. "Easy Run", "Cross Training", "Strength Training".
        sport:        Optional hint, e.g. "cycling" for a Cross Training
                       session that IS a bike ride (see sync_target rules).
        name:         Display title, e.g. "Upper Body" — separate from notes.
        structure:    Workout step structure (stored in training_plan.workout_steps).
        target:       HR/pace target structure, e.g. {"type":"hr_zone","zone":2}.
        source:       Optional origin marker, e.g. "hevy" for strength sessions.
        sync_target:  Optional caller-requested "garmin"|"hevy"|"cairn_only".
                      VALIDATED, never trusted blindly: requesting "garmin" for
                      a Strength/Mobility/Core/Rest Day session_type is a hard
                      error (see coach/session_routing.py). A more conservative
                      request (e.g. "cairn_only" for a run) is honored as-is.
                      If omitted, sync_target is computed automatically.
        elevation_gain_m: Optional planned elevation gain in meters. CAIRN-only
                      metadata — never sent to Garmin (garmin_push.py has no
                      concept of this field and workout_def is built explicitly
                      key-by-key, so it can never leak into a Garmin push).
        km_factor:    Optional conversion factor for Cross-sessions (e.g. 0.25
                      for Rennrad: 80 km ride → 20 km running-equivalent).
                      NULL / omitted = no conversion (running, trail, hiking).
                      Stored for later use when actual_distance_km comes back
                      from Garmin, so running-equivalent can be computed as:
                      actual_distance_km * km_factor.
                      distance_km should already be in running-equivalent units
                      when km_factor is set (i.e. ChatGPT applies the formula
                      before calling this tool).

    Returns: training_plan id, sync_target (the actually applied value,
    post-validation), garmin_sport, created (bool).
    """
    if km_factor is not None:
        if not isinstance(km_factor, (int, float)) or km_factor <= 0 or km_factor > 1:
            return {"error": "km_factor muss eine Zahl zwischen 0 (exkl.) und 1 (inkl.) sein, z.B. 0.25"}
        km_factor = float(km_factor)
    if not external_id or not date or not session_type:
        return {"error": "external_id, date und session_type sind erforderlich."}
    try:
        session_date = datetime.date.fromisoformat(date)
    except ValueError:
        return {"error": f"date {date!r} ist kein gueltiges YYYY-MM-DD"}

    try:
        routing = resolve_sync_target(session_type, requested_sync_target=sync_target, sport_hint=sport)
    except ValueError as exc:
        return {"error": str(exc)}

    week_date, day_of_week = _week_date_and_dow(session_date)
    resolved_source = source or routing["source"]

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM plans WHERE status='active' ORDER BY created_at DESC LIMIT 1"
            )
            active = cur.fetchone()
            plan_id = active[0] if active else None

            # Vor dem Schreiben: bestehenden garmin_workout_id/session_zone
            # merken, um danach zu erkennen ob eine bereits gepushte Session
            # inhaltlich geaendert wurde (-> dirty markieren, Schritt 6).
            cur.execute(
                "SELECT garmin_workout_id, session_zone FROM training_plan WHERE external_id = %s",
                (external_id,),
            )
            prior = cur.fetchone()
            existing_garmin_id, existing_session_zone = prior if prior else (None, None)

            columns = [
                "external_id", "week_date", "day_of_week", "session_type", "sport",
                "name", "distance_km", "duration_min", "notes", "workout_steps",
                "target", "source", "garmin_push_required", "sync_target",
                "elevation_gain_m", "km_factor",
            ]
            values = [
                external_id, week_date, day_of_week, session_type, sport,
                name, distance_km, duration_min, notes,
                json.dumps(structure) if structure is not None else None,
                json.dumps(target) if target is not None else None,
                resolved_source, routing["garmin_push_required"], routing["sync_target"],
                elevation_gain_m, km_factor,
            ]
            if plan_id is not None:
                columns.append("plan_id")
                values.append(plan_id)

            placeholders = ", ".join(["%s"] * len(columns))
            update_clause = ", ".join(
                f"{c}=EXCLUDED.{c}" for c in columns if c != "external_id"
            ) + ", updated_at=now()"
            cur.execute(
                f"""INSERT INTO training_plan ({", ".join(columns)})
                    VALUES ({placeholders})
                    ON CONFLICT (external_id) DO UPDATE SET {update_clause}
                    RETURNING id, (xmax = 0) AS inserted""",
                values,
            )
            row_id, inserted = cur.fetchone()

            # Schritt 6 — Dirty-Marking: wenn diese Session schon einen
            # garmin_workout_id hatte (also bereits gepusht war) UND sich der
            # Inhalt gegenueber dem gespeicherten content_hash geaendert hat,
            # sync_status='dirty' setzen, damit der naechste Batch-Push sie
            # erneut aufnimmt (sessions_to_push() filtert genau darauf).
            if existing_garmin_id:
                new_hash = compute_content_hash({
                    "date": date, "session_type": session_type,
                    "distance_km": distance_km, "duration_min": duration_min,
                    "notes": notes, "workout_steps": structure,
                    "session_zone": existing_session_zone,
                    "name": name, "target": target,
                    "elevation_gain_m": elevation_gain_m,
                })
                cur.execute("SELECT content_hash FROM training_plan WHERE id = %s", (row_id,))
                old_hash = cur.fetchone()[0]
                if new_hash != old_hash:
                    cur.execute(
                        "UPDATE training_plan SET sync_status='dirty', content_hash=%s WHERE id=%s",
                        (new_hash, row_id),
                    )
        conn.commit()
        logger.info(
            "upsert_planned_workout committed: id=%s external_id=%s sync_target=%s created=%s",
            row_id, external_id, routing["sync_target"], inserted,
        )
        return {
            "id": row_id, "created": bool(inserted),
            "sync_target": routing["sync_target"], "garmin_sport": routing["garmin_sport"],
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@mcp.tool()
def link_activity_to_session(
    session_id: int,
    garmin_activity_id: int,
    actual_distance_km: float | None = None,
) -> dict:
    """
    Backdoor: Manually link a completed Garmin activity to a planned session.

    Use this when the athlete did NOT start the workout directly from the watch
    (so there is no automatic garmin_workout_id match). ChatGPT calls this after
    the user says "today's run was the planned interval session from Tuesday".

    The sync job automatically matches via garmin_workout_id (watch-started
    workouts). This tool is only needed for the fallback case.

    Args:
        session_id:           training_plan.id of the planned session.
        garmin_activity_id:   Garmin activity ID of the completed workout
                              (visible in Garmin Connect, returned by
                              get_recent_activities as "activity_id").
        actual_distance_km:   Actual distance in raw km (no factor applied).
                              If omitted, the value is read from the trainings
                              table if a matching garmin_id exists there.

    Returns: session_id, garmin_activity_id, actual_distance_km written,
             km_factor on the session, running_equivalent_km (if factor set).
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # Hole Session inkl. km_factor
            cur.execute(
                "SELECT id, km_factor, actual_distance_km FROM training_plan WHERE id = %s",
                (session_id,),
            )
            row = cur.fetchone()
            if not row:
                return {"error": f"Session {session_id} nicht gefunden."}
            _, km_factor, existing_actual = row

            # Falls actual_distance_km nicht angegeben, aus trainings lesen
            dist_to_write = actual_distance_km
            if dist_to_write is None:
                cur.execute(
                    "SELECT distance_km FROM trainings WHERE garmin_id = %s",
                    (str(garmin_activity_id),),
                )
                t_row = cur.fetchone()
                if t_row and t_row[0]:
                    dist_to_write = float(t_row[0])

            cur.execute(
                """UPDATE training_plan
                   SET linked_garmin_activity_id = %s,
                       actual_distance_km = %s,
                       updated_at = now()
                   WHERE id = %s""",
                (garmin_activity_id, dist_to_write, session_id),
            )
        conn.commit()

        running_equiv = None
        if dist_to_write is not None and km_factor is not None:
            running_equiv = round(float(dist_to_write) * float(km_factor), 2)

        logger.info(
            "link_activity_to_session: session=%s activity=%s actual_km=%s equiv=%s",
            session_id, garmin_activity_id, dist_to_write, running_equiv,
        )
        return {
            "session_id": session_id,
            "garmin_activity_id": garmin_activity_id,
            "actual_distance_km": dist_to_write,
            "km_factor": float(km_factor) if km_factor is not None else None,
            "running_equivalent_km": running_equiv,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@mcp.tool()
def patch_planned_workout(session_id: int, patch: dict) -> dict:
    """
    Patch specific fields of an existing planned session by its database ID.

    Use this when you know the session's numeric ID (visible in list_garmin_workouts
    and get_planned_workouts) and want to update only certain fields without
    touching the workout structure, Garmin state, or external_id.

    Patchable fields:
        elevation_gain_m (int)   — planned elevation gain in meters (CAIRN-only, never sent to Garmin)
        notes            (str)   — session notes / terrain description
        name             (str)   — display title
        distance_km      (float) — planned distance
        duration_min     (int)   — planned duration
        session_zone     (str)   — intensity label, e.g. "Uphill RPE 7-8"

    NOT patchable via this tool (use upsert_planned_workout for these):
        session_type, date, sport, workout_steps, external_id,
        garmin_workout_id, sync_status

    Args:
        session_id: The integer ID from training_plan (visible in list_garmin_workouts
                    and get_planned_workouts output).
        patch:      Dict containing only the fields to change, e.g.
                    {"elevation_gain_m": 800}
                    {"elevation_gain_m": 1200, "notes": "Bergtrail, technisch, ca. 1200 HM"}

    Returns: id, fields_patched, rows_changed, garmin_dirty flag.
    """
    ALLOWED = {"elevation_gain_m", "notes", "name", "distance_km", "duration_min", "session_zone"}

    unknown = set(patch.keys()) - ALLOWED
    if unknown:
        return {"error": f"Nicht erlaubte Felder: {sorted(unknown)}. Erlaubt: {sorted(ALLOWED)}"}

    if not patch:
        return {"error": "patch ist leer — nichts zu aendern."}

    # Type coercions for safety
    if "elevation_gain_m" in patch and patch["elevation_gain_m"] is not None:
        patch["elevation_gain_m"] = int(patch["elevation_gain_m"])
    if "distance_km" in patch and patch["distance_km"] is not None:
        patch["distance_km"] = float(patch["distance_km"])
    if "duration_min" in patch and patch["duration_min"] is not None:
        patch["duration_min"] = int(patch["duration_min"])

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # Verify session exists and get Garmin state
            cur.execute(
                "SELECT id, garmin_workout_id FROM training_plan WHERE id = %s",
                (session_id,)
            )
            row = cur.fetchone()
            if not row:
                return {"error": f"Session {session_id} nicht gefunden."}

            _, garmin_workout_id = row

            # Build targeted UPDATE — only patch fields, nothing else
            set_parts = [f"{k} = %s" for k in patch.keys()]
            set_parts.append("updated_at = now()")
            values = list(patch.values()) + [session_id]

            cur.execute(
                f"UPDATE training_plan SET {', '.join(set_parts)} WHERE id = %s",
                values
            )
            changed = cur.rowcount

            # Mark dirty if already on Garmin and a Garmin-visible field changed
            garmin_visible = {"distance_km", "duration_min", "name"}
            marked_dirty = False
            if garmin_workout_id and any(f in patch for f in garmin_visible):
                cur.execute(
                    "UPDATE training_plan SET sync_status = 'dirty' "
                    "WHERE id = %s AND sync_status = 'synced'",
                    (session_id,)
                )
                marked_dirty = cur.rowcount > 0

        conn.commit()
        logger.info(
            "patch_planned_workout committed: id=%s fields=%s dirty=%s",
            session_id, list(patch.keys()), marked_dirty,
        )
        return {
            "id": session_id,
            "fields_patched": list(patch.keys()),
            "rows_changed": changed,
            "garmin_dirty": marked_dirty,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()



@mcp.tool()
def upsert_training_block(plan: dict, confirm_race_date_change: bool = False) -> dict:
    """
    Save race + training block + all sessions atomically (single DB transaction —
    all rows or none). Idempotent per session via external_id (ON CONFLICT DO
    UPDATE, never duplicates). Always call preview_training_block first.

    If the block's race.race_date differs from the currently active plan's
    race_date, this call is REFUSED unless confirm_race_date_change=True is
    passed explicitly — race-date changes on an active plan must be a
    deliberate, confirmed decision, never a silent side effect.

    plan shape: same as preview_training_block.
    Returns: plan_id, created, updated, race_date_changed.
    """
    race = plan.get("race") or {}
    sessions = plan.get("sessions") or []

    errors = []
    if not race.get("race_date"):
        errors.append("race.race_date fehlt")
    for i, s in enumerate(sessions):
        errors.extend(_validate_session_fields(i, s))
    ext_ids = [s.get("external_id") for s in sessions if s.get("external_id")]
    for dupe in sorted({x for x in ext_ids if ext_ids.count(x) > 1}):
        errors.append(f"external_id {dupe!r} kommt mehrfach im selben Block vor")
    if errors:
        return {"error": "Validierung fehlgeschlagen — zuerst preview_training_block aufrufen.", "conflicts": errors}

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, race_date, race_name FROM plans WHERE status='active' ORDER BY created_at DESC LIMIT 1"
            )
            row = cur.fetchone()
            active_plan_id, active_race_date, active_race_name = row if row else (None, None, None)

            race_date_changed = False
            if active_plan_id and active_race_date and str(active_race_date) != race["race_date"]:
                if not confirm_race_date_change:
                    conn.rollback()
                    return {
                        "error": "Race-Date-Aenderung erkannt, aber nicht bestaetigt.",
                        "plan_id": active_plan_id,
                        "old_race_date": str(active_race_date),
                        "new_race_date": race["race_date"],
                        "hint": "upsert_training_block(plan, confirm_race_date_change=True) erneut aufrufen, "
                                "um diese Aenderung bewusst zu bestaetigen.",
                    }
                race_date_changed = True
                logger.info(
                    "Race date change CONFIRMED for plan_id=%s: %s -> %s",
                    active_plan_id, active_race_date, race["race_date"],
                )

            if active_plan_id:
                plan_id = active_plan_id
                cur.execute(
                    """UPDATE plans SET name=%s, goal_type=%s, race_name=%s, race_date=%s,
                           race_distance_km=%s, race_elevation_m=%s,
                           race_priority=%s, target_time=%s
                       WHERE id=%s""",
                    (
                        race.get("name") or race.get("race_name"), race.get("goal_type"),
                        race.get("name") or race.get("race_name"), race["race_date"],
                        race.get("race_distance_km"),
                        race.get("race_elevation_m"),
                        race.get("race_priority", "A"),
                        race.get("target_time"),
                        plan_id,
                    ),
                )
            else:
                cur.execute(
                    """INSERT INTO plans (name, goal_type, race_name, race_date, race_distance_km,
                                           race_elevation_m, race_priority, target_time, status)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'active') RETURNING id""",
                    (
                        race.get("name") or race.get("race_name"), race.get("goal_type"),
                        race.get("name") or race.get("race_name"), race["race_date"],
                        race.get("race_distance_km"),
                        race.get("race_elevation_m"),
                        race.get("race_priority", "A"),
                        race.get("target_time"),
                    ),
                )
                plan_id = cur.fetchone()[0]

            created = updated = 0
            routing_warnings: list[str] = []
            routing_summary = {"garmin": 0, "hevy": 0, "cairn_only": 0}
            for s in sessions:
                session_date = datetime.date.fromisoformat(s["date"])
                week_date, day_of_week = _week_date_and_dow(session_date)

                # Zentrale Klassifizierung — einzige Stelle, die ueber Garmin-
                # Zustaendigkeit entscheidet. Kraft/Mobility/Core/Rest Day
                # bekommen hier garmin_push_required=False und werden von
                # KEINEM Code-Pfad automatisch an Garmin geschickt.
                routing = classify_for_push(s["session_type"], s.get("sport_hint"))
                routing_summary[routing["sync_target"]] += 1

                hevy_routine_key = s.get("hevy_routine_key") if routing["sync_target"] == "hevy" else None
                if hevy_routine_key:
                    cur.execute("SELECT 1 FROM cairn_routines WHERE title = %s", (hevy_routine_key,))
                    if not cur.fetchone():
                        routing_warnings.append(
                            f"Session {s['external_id']!r}: hevy_routine_key {hevy_routine_key!r} "
                            f"nicht in cairn_routines gefunden — Referenz wird trotzdem gespeichert, "
                            f"aber ohne bestaetigte Hevy-Routine (nichts erfunden)."
                        )

                # sport: expliziter sport_hint hat Vorrang, sonst der aus session_type
                # berechnete garmin_sport (None fuer hevy/cairn_only) — damit die
                # Garmin-Batch-Engine (coach/garmin_batch.py) running/cycling immer
                # korrekt unterscheiden kann, ohne dass jeder Aufrufer sport explizit
                # mitgeben muss.
                # sport: sport_hint hat Vorrang, sport als Alias, dann garmin_sport
                resolved_sport = s.get("sport_hint") or s.get("sport") or routing["garmin_sport"]

                # Vor dem Schreiben: bestehenden garmin_workout_id/content_hash
                # merken, um danach zu erkennen ob eine bereits gepushte Session
                # inhaltlich geaendert wurde (-> dirty markieren, Schritt 6).
                cur.execute(
                    "SELECT garmin_workout_id, content_hash FROM training_plan WHERE external_id = %s",
                    (s["external_id"],),
                )
                prior = cur.fetchone()
                existing_garmin_id, old_hash = prior if prior else (None, None)

                # status wird bei INSERT gesetzt, aber bei ON CONFLICT bewusst NICHT
                # überschrieben — ein erneutes upsert_training_block darf einen bereits
                # von sync_hevy_completions auf 'completed' gesetzten Status nicht
                # stillschweigend auf 'planned' zurücksetzen.
                cur.execute(
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
                )
                row_id, inserted = cur.fetchone()
                if inserted:
                    created += 1
                else:
                    updated += 1

                # Schritt 6 — Dirty-Marking (siehe upsert_planned_workout fuer
                # dieselbe Logik): bereits gepushte Session inhaltlich geaendert?
                if existing_garmin_id:
                    new_hash = compute_content_hash({
                        "date": s["date"], "session_type": s["session_type"],
                        "distance_km": s.get("distance_km"), "duration_min": s.get("duration_min"),
                        "notes": s.get("notes"), "workout_steps": None,
                        "session_zone": s.get("session_zone"),
                        "name": s.get("name"), "target": s.get("target"),
                        "elevation_gain_m": s.get("elevation_gain_m"),
                    })
                    if new_hash != old_hash:
                        cur.execute(
                            "UPDATE training_plan SET sync_status='dirty', content_hash=%s WHERE id=%s",
                            (new_hash, row_id),
                        )

        # ─── plan_weeks: Wochenstruktur aus Sessions ableiten ────────────────────
        import collections as _collections
        import datetime as _dt

        week_groups = _collections.defaultdict(list)
        for s in sessions:
            sdate = _dt.date.fromisoformat(s["date"])
            wstart = sdate - _dt.timedelta(days=sdate.weekday())
            week_groups[wstart].append(s)

        sorted_weeks = sorted(week_groups.keys())

        def _week_km(slist):
            return sum(
                (s.get("distance_km") or 0)
                for s in slist
                if s.get("session_type") not in ("Rest Day", "Strength Training", "Core", "Mobility")
            )

        # Optionale Wochen-Metadaten (phase-Override, week_focus) aus plan.weeks
        week_meta: dict = {}
        for wm in (plan.get("weeks") or []):
            wn = wm.get("week_number")
            if wn:
                week_meta[int(wn)] = wm

        # Peak-Woche: höchstes km-Volumen
        week_km_totals = {
            wnum: _week_km(week_groups[wstart])
            for wnum, wstart in enumerate(sorted_weeks, start=1)
        }
        max_km = max(week_km_totals.values(), default=0)

        with conn.cursor() as cur:
            pw_id_map: dict = {}
            prev_km = 0.0
            for wnum, wstart in enumerate(sorted_weeks, start=1):
                slist = week_groups[wstart]
                wkm = _week_km(slist)
                meta = week_meta.get(wnum, {})
                phases_in_week = [s.get("phase") or s.get("session_zone") for s in slist if s.get("phase") or s.get("session_zone")]
                phase_val = meta.get("phase") or (phases_in_week[0] if phases_in_week else "base")
                if "is_deload" in meta:
                    is_deload = bool(meta["is_deload"])
                else:
                    is_deload = (prev_km > 0 and wkm > 0 and wkm / prev_km < 0.75)
                if wkm > 0:
                    prev_km = wkm
                is_peak = (max_km > 0 and wkm == max_km)
                week_focus = meta.get("week_focus") or ""

                cur.execute(
                    """INSERT INTO plan_weeks (plan_id, week_number, week_start, phase, is_deload, is_peak, target_run_km, week_focus)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (plan_id, week_number) DO UPDATE SET
                           week_start=EXCLUDED.week_start,
                           phase=EXCLUDED.phase,
                           is_deload=EXCLUDED.is_deload,
                           is_peak=EXCLUDED.is_peak,
                           target_run_km=EXCLUDED.target_run_km,
                           week_focus=EXCLUDED.week_focus,
                           updated_at=now()
                       RETURNING id""",
                    (plan_id, wnum, wstart, phase_val, is_deload, is_peak, round(wkm, 1) if wkm else None, week_focus),
                )
                pw_id = cur.fetchone()[0]
                pw_id_map[wstart] = pw_id

            for s in sessions:
                sdate = _dt.date.fromisoformat(s["date"])
                wstart = sdate - _dt.timedelta(days=sdate.weekday())
                pw_id = pw_id_map.get(wstart)
                if pw_id:
                    cur.execute(
                        "UPDATE training_plan SET week_id=%s WHERE external_id=%s AND plan_id=%s",
                        (pw_id, s["external_id"], plan_id),
                    )

        # total_weeks aus tatsächlichen Wochen ableiten und in plans speichern
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE plans SET total_weeks=%s WHERE id=%s",
                (len(sorted_weeks), plan_id),
            )

        conn.commit()
        logger.info(
            "upsert_training_block committed: plan_id=%s created=%s updated=%s race_date_changed=%s routing=%s total_weeks=%s",
            plan_id, created, updated, race_date_changed, routing_summary, len(sorted_weeks),
        )
        return {
            "plan_id": plan_id, "created": created, "updated": updated,
            "race_date_changed": race_date_changed,
            "routing_summary": routing_summary,
            "warnings": routing_warnings,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()



@mcp.tool()
def update_planned_workout(session_id: int | None = None, external_id: str | None = None,
                           patch: dict = {}, reason: str = "") -> dict:
    """
    Partial update of a single planned session in CAIRN.

    Provide either session_id (training_plan.id) or external_id.
    patch: dict with only the fields you want to change. Allowed keys:
        date (YYYY-MM-DD), session_type, session_zone, name, distance_km,
        duration_min, notes, elevation_gain_m, km_factor, status,
        sync_target, target, phase

    Fields NOT in patch are left untouched.
    If date changes and crosses a week boundary, week_id is recalculated.
    Does NOT auto-push to Garmin — if the session has garmin_workout_id
    and content-sensitive fields change, sync_status is set to 'dirty'.
    reason: short explanation for the log (e.g. "moved long run due to weather").

    Returns: id, external_id, changed_fields, sync_status_after.
    """
    if not session_id and not external_id:
        return {"error": "session_id oder external_id erforderlich."}
    if not patch:
        return {"error": "patch darf nicht leer sein."}

    ALLOWED = {
        "date", "session_type", "session_zone", "name", "distance_km",
        "duration_min", "notes", "elevation_gain_m", "km_factor", "status",
        "sync_target", "target", "phase",
    }
    unknown = set(patch) - ALLOWED
    if unknown:
        return {"error": f"Unbekannte patch-Felder: {sorted(unknown)}. Erlaubt: {sorted(ALLOWED)}"}

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if session_id:
                cur.execute(
                    "SELECT id, external_id, week_date, garmin_workout_id, sync_status FROM training_plan WHERE id=%s",
                    (session_id,),
                )
            else:
                cur.execute(
                    "SELECT id, external_id, week_date, garmin_workout_id, sync_status FROM training_plan WHERE external_id=%s",
                    (external_id,),
                )
            row = cur.fetchone()
            if not row:
                return {"error": f"Session nicht gefunden (session_id={session_id}, external_id={external_id!r})."}

            row_id, ext_id, cur_week_date, garmin_id, cur_sync_status = row

            COLUMN_MAP = {
                "session_type": "session_type",
                "session_zone": "session_zone",
                "name": "name",
                "distance_km": "distance_km",
                "duration_min": "duration_min",
                "notes": "notes",
                "elevation_gain_m": "elevation_gain_m",
                "km_factor": "km_factor",
                "status": "status",
                "sync_target": "sync_target",
                "phase": "phase",
            }

            sets = []
            vals = []
            changed_fields = []

            if "date" in patch:
                try:
                    new_date = datetime.date.fromisoformat(patch["date"])
                except ValueError:
                    return {"error": f"date {patch['date']!r} ist kein gültiges YYYY-MM-DD"}
                new_week_date, new_dow = _week_date_and_dow(new_date)
                sets += ["week_date=%s", "day_of_week=%s"]
                vals += [new_week_date, new_dow]
                changed_fields.append("date")
                if new_week_date != cur_week_date:
                    cur.execute(
                        """SELECT id FROM plan_weeks
                           WHERE week_start=%s AND plan_id=(
                               SELECT plan_id FROM training_plan WHERE id=%s
                           )""",
                        (new_week_date, row_id),
                    )
                    pw = cur.fetchone()
                    if pw:
                        sets.append("week_id=%s")
                        vals.append(pw[0])
                        changed_fields.append("week_id")

            if "target" in patch:
                sets.append("target=%s")
                vals.append(json.dumps(patch["target"]) if patch["target"] is not None else None)
                changed_fields.append("target")

            for key, col in COLUMN_MAP.items():
                if key in patch:
                    sets.append(f"{col}=%s")
                    vals.append(patch[key])
                    changed_fields.append(key)

            GARMIN_SENSITIVE = {"date", "session_type", "distance_km", "duration_min", "notes",
                                "session_zone", "name", "target", "elevation_gain_m"}
            new_sync_status = cur_sync_status
            if garmin_id and (set(changed_fields) & GARMIN_SENSITIVE):
                sets.append("sync_status=%s")
                vals.append("dirty")
                new_sync_status = "dirty"
                changed_fields.append("_sync_status→dirty")

            if not sets:
                return {"id": row_id, "external_id": ext_id, "changed_fields": [], "note": "Keine Änderungen."}

            sets.append("updated_at=now()")
            vals.append(row_id)
            cur.execute(
                f"UPDATE training_plan SET {', '.join(sets)} WHERE id=%s",
                vals,
            )
            logger.info(
                "update_planned_workout: id=%s ext=%s changed=%s reason=%r",
                row_id, ext_id, changed_fields, reason,
            )

        conn.commit()
        return {
            "id": row_id,
            "external_id": ext_id,
            "changed_fields": changed_fields,
            "sync_status_after": new_sync_status,
            "needs_garmin_push": (garmin_id is not None and new_sync_status == "dirty"),
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()



@mcp.tool()
def archive_plan_sessions(plan_id: int) -> dict:
    """
    Archiviert alle Sessions eines Plans (setzt status='archived').
    Verwendet um einen alten Plan zu deaktivieren bevor ein neuer importiert wird.
    Garmin-Workouts werden NICHT gelöscht — nur CAIRN-Status wird geändert.
    Gibt zurück: plan_id, archived_count.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE training_plan SET status='archived', updated_at=now()
                   WHERE plan_id=%s AND status NOT IN ('completed', 'archived')""",
                (plan_id,),
            )
            archived = cur.rowcount
        conn.commit()
        logger.info("archive_plan_sessions: plan_id=%s archived=%s", plan_id, archived)
        return {"plan_id": plan_id, "archived_count": archived}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@mcp.tool()
def fix_session_phases_from_weeks(plan_id: int | None = None) -> dict:
    """
    Korrigiert session.phase für alle Sessions eines Plans anhand von plan_weeks.
    Nützlich wenn upsert_training_block phase nicht korrekt gespeichert hat.

    Logik: Für jede Woche in plan_weeks → alle training_plan-Einheiten in dieser
    Woche (week_date = plan_weeks.week_start) erhalten plan_weeks.phase.

    Returns: updated_count, skipped_count (Rest Days werden übersprungen).
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if plan_id is None:
                cur.execute("SELECT id FROM plans WHERE status='active' ORDER BY created_at DESC LIMIT 1")
                row = cur.fetchone()
                if not row:
                    return {"error": "Kein aktiver Plan gefunden."}
                plan_id = row[0]

            cur.execute(
                "SELECT week_start, phase FROM plan_weeks WHERE plan_id=%s ORDER BY week_number",
                (plan_id,),
            )
            weeks = cur.fetchall()
            if not weeks:
                return {"error": f"plan_id={plan_id} hat keine plan_weeks-Einträge."}

            updated = skipped = 0
            for week_start, phase in weeks:
                cur.execute(
                    """UPDATE training_plan SET phase=%s, updated_at=now()
                       WHERE plan_id=%s AND week_date=%s
                         AND session_type NOT IN ('Rest Day')""",
                    (phase, plan_id, week_start),
                )
                updated += cur.rowcount
                cur.execute(
                    "SELECT COUNT(*) FROM training_plan WHERE plan_id=%s AND week_date=%s AND session_type='Rest Day'",
                    (plan_id, week_start),
                )
                skipped += cur.fetchone()[0]

        conn.commit()
        logger.info("fix_session_phases_from_weeks: plan_id=%s updated=%s skipped=%s", plan_id, updated, skipped)
        return {"plan_id": plan_id, "updated_count": updated, "skipped_count": skipped}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()



@mcp.tool()
def purge_all_sessions(confirm: bool = False) -> dict:
    """
    Löscht ALLE Sessions (training_plan) und plan_weeks aus der CAIRN-Datenbank.
    Pläne selbst bleiben erhalten (werden auf 'archived' gesetzt).
    Garmin-Workouts werden NICHT gelöscht — vorher bulk_delete_garmin_workouts aufrufen.

    confirm=True ist Pflicht — Sicherheitssperre gegen versehentliches Aufrufen.
    Returns: deleted_sessions, deleted_plan_weeks, archived_plans.
    """
    if not confirm:
        return {
            "error": "Sicherheitssperre: confirm=True explizit setzen um fortzufahren.",
            "hint": "purge_all_sessions(confirm=True)"
        }

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM training_plan")
            deleted_sessions = cur.rowcount

            cur.execute("DELETE FROM plan_weeks")
            deleted_plan_weeks = cur.rowcount

            cur.execute("UPDATE plans SET status='archived', updated_at=now() WHERE status='active'")
            archived_plans = cur.rowcount

        conn.commit()
        logger.info(
            "purge_all_sessions: deleted_sessions=%s deleted_plan_weeks=%s archived_plans=%s",
            deleted_sessions, deleted_plan_weeks, archived_plans,
        )
        return {
            "deleted_sessions":   deleted_sessions,
            "deleted_plan_weeks": deleted_plan_weeks,
            "archived_plans":     archived_plans,
            "note": "DB bereinigt. Pläne archiviert. Garmin-Workouts wurden nicht berührt.",
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@mcp.tool()
def upsert_milestones(milestones: list) -> dict:
    """
    Save milestones (title, criterion, target_date, status, evidence) into the
    milestones table. Matched on (plan_id, step_number) — updates existing rows
    instead of duplicating (no DB-level unique constraint on that pair exists,
    so this does an explicit check-then-update-or-insert per row).

    milestones shape:
    [{"plan_id": int, "step_number": int, "title": str, "criterion": str,
      "target_date": "YYYY-MM-DD", "status": "open|achieved|changed",
      "evidence": str, "notes": str}, ...]
    """
    if not milestones:
        return {"created": 0, "updated": 0}

    conn = get_connection()
    try:
        created = updated = 0
        with conn.cursor() as cur:
            for m in milestones:
                if not m.get("plan_id") or m.get("step_number") is None or not m.get("title"):
                    raise ValueError(f"Milestone fehlt plan_id/step_number/title: {m!r}")
                cur.execute(
                    "SELECT id FROM milestones WHERE plan_id=%s AND step_number=%s",
                    (m["plan_id"], m["step_number"]),
                )
                row = cur.fetchone()
                if row:
                    cur.execute(
                        """UPDATE milestones SET title=%s, criterion=%s, target_date=%s,
                               status=%s, evidence=%s, notes=%s, updated_at=now()
                           WHERE id=%s""",
                        (m["title"], m.get("criterion"), m.get("target_date"),
                         m.get("status", "open"), m.get("evidence"), m.get("notes"), row[0]),
                    )
                    updated += 1
                else:
                    cur.execute(
                        """INSERT INTO milestones
                               (plan_id, step_number, title, criterion, target_date, status, evidence, notes)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                        (m["plan_id"], m["step_number"], m["title"], m.get("criterion"),
                         m.get("target_date"), m.get("status", "open"), m.get("evidence"), m.get("notes")),
                    )
                    created += 1
        conn.commit()
        logger.info("upsert_milestones committed: created=%s updated=%s", created, updated)
        return {"created": created, "updated": updated}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@mcp.tool()
def sync_hevy_completions(days: int = 30) -> dict:
    """
    Match completed Hevy workouts (trainings.type='WeightTraining', hevy_id
    IS NOT NULL) to planned CAIRN sessions in training_plan, over the last N
    days. Links via training_plan.hevy_id = trainings.hevy_id (2026-08-17 —
    replaces the earlier matched_training_id approach, which never produced
    matches because training_plan had no hevy_id column at all).

    Match rule, per Hevy workout:
        training_plan.session_date = workout.date
        AND session_type IN ('Strength Training', 'Krafttraining', 'Core', 'Mobility')
        AND status = 'planned'
    On match: training_plan.status = 'completed', training_plan.hevy_id = workout.hevy_id.

    Idempotent: a Hevy workout whose hevy_id is already linked to some
    training_plan row is excluded from consideration up front — repeated
    calls never re-match or double-link the same workout.

    A failure matching one workout does not abort the rest (isolated
    per-workout try/except) — this must never block Garmin sync, a separate tool.

    Returns: {matched: int, unmatched_hevy: [...], unmatched_cairn: [...]}
    - unmatched_hevy:  Hevy workouts in the window with no matching planned session.
    - unmatched_cairn: planned Strength/Core/Mobility sessions in the window
                        that are still unmatched after this run.
    """
    conn = get_connection()
    try:
        matched = 0
        unmatched_hevy: list[dict] = []

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, date, hevy_id, notes
                FROM trainings
                WHERE type = 'WeightTraining'
                  AND hevy_id IS NOT NULL
                  AND date >= CURRENT_DATE - (INTERVAL '1 day' * %s)
                  AND hevy_id NOT IN (
                      SELECT hevy_id FROM training_plan WHERE hevy_id IS NOT NULL
                  )
                ORDER BY date
                """,
                (days,),
            )
            hevy_workouts = cur.fetchall()

            for training_id, workout_date, hevy_id, notes in hevy_workouts:
                try:
                    cur.execute(
                        """
                        SELECT id, external_id FROM training_plan
                        WHERE (week_date + (day_of_week - 1) * INTERVAL '1 day')::date = %s
                          AND session_type IN ('Strength Training', 'Krafttraining', 'Core', 'Mobility')
                          AND status = 'planned'
                        ORDER BY id
                        LIMIT 1
                        """,
                        (workout_date,),
                    )
                    row = cur.fetchone()

                    if row:
                        tp_id, ext_id = row
                        cur.execute(
                            "UPDATE training_plan SET status='completed', hevy_id=%s WHERE id=%s",
                            (hevy_id, tp_id),
                        )
                        matched += 1
                        logger.info(
                            "sync_hevy_completions matched: training_plan_id=%s <- hevy_id=%s (training_id=%s)",
                            tp_id, hevy_id, training_id,
                        )
                    else:
                        unmatched_hevy.append({
                            "hevy_id": hevy_id, "training_id": training_id,
                            "date": str(workout_date), "title": notes,
                        })
                except Exception as exc:
                    unmatched_hevy.append({
                        "hevy_id": hevy_id, "training_id": training_id, "date": str(workout_date),
                        "title": notes, "error": f"{type(exc).__name__}: {exc}",
                    })
                    continue

            cur.execute(
                """
                SELECT id, external_id,
                       (week_date + (day_of_week - 1) * INTERVAL '1 day')::date AS session_date
                FROM training_plan
                WHERE session_type IN ('Strength Training', 'Krafttraining', 'Core', 'Mobility')
                  AND status = 'planned'
                  AND (week_date + (day_of_week - 1) * INTERVAL '1 day')::date
                      >= CURRENT_DATE - (INTERVAL '1 day' * %s)
                ORDER BY session_date
                """,
                (days,),
            )
            unmatched_cairn = [
                {"training_plan_id": r[0], "external_id": r[1], "date": str(r[2])}
                for r in cur.fetchall()
            ]

        conn.commit()
        logger.info(
            "sync_hevy_completions committed: matched=%s unmatched_hevy=%s unmatched_cairn=%s",
            matched, len(unmatched_hevy), len(unmatched_cairn),
        )
        return {"matched": matched, "unmatched_hevy": unmatched_hevy, "unmatched_cairn": unmatched_cairn}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@mcp.tool()
def reconcile_training_block(start_date: str, end_date: str) -> dict:
    """
    Compare CAIRN's training_plan sessions against the real Garmin calendar in
    a date range. Read-only — makes real Garmin API calls (via
    list_garmin_workouts) but writes nothing.

    Categorizes each CAIRN session as:
    - created:   garmin_workout_id set AND a matching Garmin entry exists on the same date
    - updated:   garmin_workout_id set, Garmin entry exists, but on a DIFFERENT date
                 (moved on the Garmin side without CAIRN's training_plan reflecting it)
    - unchanged: garmin_workout_id NOT set, no Garmin entry expected yet
    - conflict:  garmin_workout_id set but NO matching Garmin entry exists anymore
                 (deleted/changed externally), or an unmatched Garmin entry exists
                 on a day with a CAIRN session that has no garmin_workout_id
    - failed:    garmin_mcp_log shows status='failed' for this session's external_id
    """
    plan_sessions = _fetchall(
        """
        SELECT id, external_id,
               (week_date + (day_of_week - 1) * INTERVAL '1 day')::date AS session_date,
               session_type, garmin_workout_id
        FROM training_plan
        WHERE (week_date + (day_of_week - 1) * INTERVAL '1 day')::date BETWEEN %s AND %s
        ORDER BY session_date
        """,
        (start_date, end_date),
    )
    garmin_entries = list_garmin_workouts(start_date, end_date)
    garmin_by_id = {e["workout_id"]: e for e in garmin_entries if e.get("workout_id")}
    garmin_dates_matched: set[str] = set()

    failed_external_ids: set[str] = set()
    ext_ids = [s["external_id"] for s in plan_sessions if s.get("external_id")]
    if ext_ids:
        failed_rows = _fetchall(
            "SELECT external_id FROM garmin_mcp_log WHERE external_id = ANY(%s) AND status = 'failed'",
            (ext_ids,),
        )
        failed_external_ids = {r["external_id"] for r in failed_rows}

    results = []
    counts = {"created": 0, "updated": 0, "unchanged": 0, "conflict": 0, "failed": 0}
    for s in plan_sessions:
        status = "unchanged"
        detail = None
        if s.get("external_id") and s["external_id"] in failed_external_ids:
            status = "failed"
            detail = "garmin_mcp_log status=failed fuer diese external_id"
        elif s.get("garmin_workout_id"):
            gw_id_int = int(s["garmin_workout_id"]) if str(s["garmin_workout_id"]).isdigit() else None
            match = garmin_by_id.get(gw_id_int)
            if not match:
                status = "conflict"
                detail = f"garmin_workout_id={s['garmin_workout_id']} existiert nicht mehr auf Garmin"
            elif match["date"] != str(s["session_date"]):
                status = "updated"
                detail = f"Garmin-Termin liegt jetzt am {match['date']}, CAIRN-Plan hat {s['session_date']}"
                garmin_dates_matched.add(match["date"])
            else:
                status = "created"
                garmin_dates_matched.add(match["date"])
        counts[status] += 1
        results.append({
            "training_plan_id": s["id"], "external_id": s.get("external_id"),
            "date": str(s["session_date"]), "session_type": s["session_type"],
            "garmin_workout_id": s.get("garmin_workout_id"), "status": status, "detail": detail,
        })

    # Unzugeordnete Garmin-Eintraege: existieren auf Garmin, aber keine CAIRN-Session referenziert sie
    orphan_garmin_entries = [e for e in garmin_entries if e["date"] not in garmin_dates_matched]
    if orphan_garmin_entries:
        counts["conflict"] += len(orphan_garmin_entries)

    return {
        "period": {"start_date": start_date, "end_date": end_date},
        "counts": counts,
        "sessions": results,
        "orphan_garmin_entries": orphan_garmin_entries,
    }


@mcp.tool()
def push_sessions_to_garmin(session_ids: list[int], chunk_size: int = 7) -> dict:
    """
    Pusht eine Liste von CAIRN-Sessions gesammelt zu Garmin.
    Ein Login — mehrere Uploads — isolierte Fehlerbehandlung.

    Nutzen:
    - Nach Planänderungen: [session_id] der geänderten Session
    - Nach Wochenumstrukturierung: alle IDs der betroffenen Woche
    - Nachholpush: IDs aller fehlgeschlagenen Sessions

    Werden mehr session_ids übergeben als chunk_size, wird nur der erste
    Chunk gepusht (Timeout-/Rate-Limit-Schutz bei langen Listen). Die
    Antwort enthält pushed_ids, remaining_ids und total_remaining, damit
    ChatGPT sofort mit der nächsten Runde weitermachen kann.

    Gibt zurück: created / updated / moved / unchanged / failed pro Session.
    Strength, Core, Mobility werden vor dem ersten Garmin-Kontakt hart abgelehnt.
    """
    chunk = session_ids[:chunk_size]
    remaining = session_ids[chunk_size:]
    try:
        from coach.garmin_batch import run_batch
        result = run_batch(session_ids=chunk)
        if isinstance(result, dict):
            result["pushed_ids"] = chunk
            result["remaining_ids"] = remaining
            result["total_remaining"] = len(remaining)
        return result
    except Exception as exc:
        return {
            "error": str(exc),
            "type": type(exc).__name__,
            "pushed_ids": [],
            "remaining_ids": session_ids,
            "total_remaining": len(session_ids),
        }


@mcp.tool()
def sync_activity_details(
    activity_id: int | None = None,
    garmin_id: str | None = None,
    force_refresh: bool = False,
) -> dict:
    """
    Lädt fehlende Detaildaten für EINE bereits importierte Aktivität nach:
    native Garmin-Laps, den distanzbasierten Activity-Stream (HF/Pace/
    Höhe/GPS) und daraus ableitbare km_splits/route. Macht echte, read-only
    Garmin-API-Calls. Idempotent: mit force_refresh=False wird nur ergänzt,
    was noch fehlt — bereits vorhandene Daten werden nicht neu geholt.

    Call this vor get_activity_analysis_data, wenn dessen data_quality-Block
    fehlende Kategorien meldet (z.B. kein Stream, keine Route).

    Args:
        activity_id:   CAIRN trainings.id
        garmin_id:     Alternativ: Garmin activityId als String
        force_refresh: True = bestehende native_laps/activity_stream löschen
                       und komplett neu importieren.
    """
    if activity_id:
        training = _fetchone("SELECT id, garmin_id FROM trainings WHERE id = %s", (activity_id,))
    elif garmin_id:
        training = _fetchone("SELECT id, garmin_id FROM trainings WHERE garmin_id = %s", (str(garmin_id),))
    else:
        return {"error": "activity_id oder garmin_id erforderlich"}

    if not training:
        return {"error": "Aktivität nicht gefunden"}
    if not training.get("garmin_id"):
        return {"error": "Keine Garmin-ID für diese Aktivität hinterlegt — Nachladen nicht möglich."}

    tid = training["id"]
    g_id = int(training["garmin_id"])
    fields_added: list[str] = []
    warnings: list[str] = []
    stream_points_imported = 0

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM splits WHERE training_id = %s", (tid,))
            has_splits = cur.fetchone()[0] > 0
            cur.execute("SELECT COUNT(*) FROM activity_stream WHERE training_id = %s", (tid,))
            has_stream = cur.fetchone()[0] > 0

        if force_refresh or not has_splits:
            try:
                client = garmin_client()
                from data.garmin_import_splits import import_splits_for_activity
                import_splits_for_activity(client, tid, g_id, force=force_refresh)
                fields_added.append("native_laps")
            except Exception as exc:
                warnings.append(f"native_laps: {type(exc).__name__}: {exc}")

        if force_refresh or not has_stream:
            try:
                client = garmin_client()
                from data.garmin_import_stream import import_stream_for_activity
                result = import_stream_for_activity(client, tid, g_id, force=force_refresh)
                if "imported" in result:
                    stream_points_imported = result["imported"]
                    fields_added.append("activity_stream")
                    fields_added.append("route")
                elif "error" in result:
                    warnings.append(f"activity_stream: {result['error']}")
            except Exception as exc:
                warnings.append(f"activity_stream: {type(exc).__name__}: {exc}")

        km_splits, km_reason = compute_km_splits(conn, tid)
        if km_splits:
            fields_added.append("km_splits")
        elif km_reason:
            warnings.append(km_reason)
    finally:
        conn.close()

    return {
        "activity_id": tid,
        "garmin_id": str(g_id),
        "updated": bool(fields_added),
        "fields_added": fields_added,
        "stream_points_imported": stream_points_imported,
        "warnings": warnings,
    }


# ── Persistente Coach-Analyse: Status / Vorbereitung / Speichern ───────────

def _frontend_url(activity_id: int) -> str:
    return f"/activities/{activity_id}/analysis"


def _latest_analysis(training_id: int) -> dict | None:
    return _fetchone(
        "SELECT * FROM activity_analyses WHERE training_id = %s ORDER BY version DESC LIMIT 1",
        (training_id,),
    )


@mcp.tool()
def get_activity_analysis_status(activity_id: int) -> dict:
    """
    Prüft, ob für eine Aktivität bereits eine gespeicherte Coach-Analyse
    existiert und ob sie noch zu den aktuellen Rohdaten passt ("fresh") oder
    veraltet ist ("stale", weil sich Distanz/HF/Stream seither geändert haben —
    z.B. durch einen nachträglichen sync_activity_details-Aufruf).

    Call this als ersten Schritt im Analyse-Workflow, um unnötige Neuberechnung
    zu vermeiden, wenn bereits eine aktuelle Analyse vorliegt.
    """
    training = _fetchone("SELECT id, date FROM trainings WHERE id = %s", (activity_id,))
    if not training:
        return {"error": "Aktivität nicht gefunden."}

    latest = _latest_analysis(activity_id)
    if not latest:
        return {
            "activity_id": activity_id, "analysis_exists": False, "status": "missing",
            "source_data_hash": None, "analysis_schema_version": None,
            "frontend_url": _frontend_url(activity_id), "generated_at": None,
        }

    current = get_activity_analysis_data(activity_id=activity_id, include_stream=False)
    current_hash = current.get("source_data_hash")
    status = "fresh" if current_hash == latest["source_data_hash"] else "stale"

    if status == "stale" and latest["status"] == "fresh":
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE activity_analyses SET status = 'stale', updated_at = now() WHERE id = %s",
                    (latest["id"],),
                )
            conn.commit()
        finally:
            conn.close()

    return {
        "activity_id": activity_id,
        "analysis_exists": True,
        "status": status,
        "source_data_hash": latest["source_data_hash"],
        "analysis_schema_version": latest["analysis_schema_version"],
        "frontend_url": _frontend_url(activity_id),
        "generated_at": latest["generated_at"],
    }


@mcp.tool()
def prepare_activity_analysis(
    activity_id: int,
    force_refresh: bool = False,
    stream_resolution_m: int = 50,
) -> dict:
    """
    Ergänzt fehlende Aktivitätsdaten (ruft intern sync_activity_details auf —
    das ist ein No-Op ohne Garmin-Zugriff, wenn native Laps und Stream bereits
    vorhanden sind) und liefert dann alle deterministischen Analysewerte plus
    eine evtl. bereits gespeicherte Coach-Analyse zur Wiederverwendung.

    Args:
        force_refresh: True = Rohdaten zwangsweise neu von Garmin laden,
                       auch wenn schon vorhanden.
    """
    training = _fetchone("SELECT id, garmin_id FROM trainings WHERE id = %s", (activity_id,))
    if not training:
        return {"error": "Aktivität nicht gefunden."}

    sync_warnings: list[str] = []
    if training.get("garmin_id"):
        sync_result = sync_activity_details(activity_id=activity_id, force_refresh=force_refresh)
        sync_warnings = sync_result.get("warnings", [])

    analysis_data = get_activity_analysis_data(
        activity_id=activity_id, include_stream=True, stream_resolution_m=stream_resolution_m,
    )
    if "error" in analysis_data:
        return analysis_data

    data_quality = analysis_data["data_quality"]
    missing_data = []
    if not data_quality["native_laps_available"] and not data_quality["km_splits_available"]:
        missing_data.append("splits")
    if not data_quality["hr_stream_available"]:
        missing_data.append("hr_stream")
    if not data_quality["gps_route_available"]:
        missing_data.append("route")
    if not data_quality["elevation_stream_available"]:
        missing_data.append("elevation_stream")

    existing = _latest_analysis(activity_id)
    existing_out = None
    if existing:
        existing_out = {**existing, "is_fresh": existing["source_data_hash"] == analysis_data["source_data_hash"]}

    return {
        "activity_id": activity_id,
        "ready": True,
        "source_data_hash": analysis_data["source_data_hash"],
        "analysis_data": analysis_data,
        "missing_data": missing_data,
        "data_quality": data_quality,
        "existing_coach_analysis": existing_out,
        "frontend_url": _frontend_url(activity_id),
        "sync_warnings": sync_warnings,
    }


@mcp.tool()
def save_activity_coach_analysis(
    activity_id: int,
    source_data_hash: str,
    analysis: dict,
    analysis_schema_version: int = 1,
) -> dict:
    """
    Speichert ChatGPTs Coach-Interpretation dauerhaft und versioniert bei der
    Aktivität. Verändert NIEMALS objektive Garmin-/CAIRN-Werte (trainings/
    splits/activity_stream) — nur die subjektive Interpretation.

    Args:
        source_data_hash: Muss exakt dem source_data_hash aus dem letzten
                prepare_activity_analysis/get_activity_analysis_data-Aufruf
                entsprechen — verhindert, dass eine Analyse gegen inzwischen
                veraltete Rohdaten gespeichert wird. Bei Mismatch: Fehler,
                zuerst prepare_activity_analysis erneut aufrufen.
        analysis: {"verdict": str, "goal_achievement": str, "summary": str,
                   "positive_findings": [str], "limitations": [str],
                   "recovery_context": str, "coach_recommendation": str,
                   "data_quality_note": str}

    Idempotent: identischer source_data_hash + identischer Analyseinhalt wie
    die bereits gespeicherte neueste Version erzeugt keine neue Version.
    """
    training = _fetchone("SELECT id FROM trainings WHERE id = %s", (activity_id,))
    if not training:
        return {"error": "Aktivität nicht gefunden."}

    current = get_activity_analysis_data(activity_id=activity_id, include_stream=False)
    if "error" in current:
        return current
    if current.get("source_data_hash") != source_data_hash:
        return {
            "error": "source_data_hash stimmt nicht mit den aktuellen Rohdaten überein — "
                     "die Analyse würde gegen veraltete Daten gespeichert. "
                     "Zuerst prepare_activity_analysis erneut aufrufen.",
            "current_source_data_hash": current.get("source_data_hash"),
        }

    latest = _latest_analysis(activity_id)
    content_fields = ("verdict", "goal_achievement", "summary", "recovery_context",
                       "coach_recommendation", "data_quality_note")
    if latest and latest["source_data_hash"] == source_data_hash and latest["status"] != "stale":
        same_content = all(
            (latest.get(f) or None) == (analysis.get(f) or None) for f in content_fields
        ) and (latest.get("positive_findings_json") or []) == (analysis.get("positive_findings") or []) \
          and (latest.get("limitations_json") or []) == (analysis.get("limitations") or [])
        if same_content:
            return {
                "saved": True, "idempotent": True, "analysis_id": latest["id"],
                "version": latest["version"], "frontend_url": _frontend_url(activity_id),
            }

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            next_version = 1
            if latest:
                cur.execute(
                    "UPDATE activity_analyses SET status = 'superseded', updated_at = now() WHERE id = %s",
                    (latest["id"],),
                )
                next_version = latest["version"] + 1

            cur.execute(
                """INSERT INTO activity_analyses
                       (training_id, analysis_schema_version, version, source_data_hash, status,
                        verdict, goal_achievement, summary, positive_findings_json, limitations_json,
                        recovery_context, coach_recommendation, data_quality_note, generated_by, generated_at)
                   VALUES (%s, %s, %s, %s, 'fresh', %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                   RETURNING id""",
                (
                    activity_id, analysis_schema_version, next_version, source_data_hash,
                    analysis.get("verdict"), analysis.get("goal_achievement"), analysis.get("summary"),
                    json.dumps(analysis.get("positive_findings") or []),
                    json.dumps(analysis.get("limitations") or []),
                    analysis.get("recovery_context"), analysis.get("coach_recommendation"),
                    analysis.get("data_quality_note"), "chatgpt",
                ),
            )
            analysis_id = cur.fetchone()[0]
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {
        "saved": True, "idempotent": False, "analysis_id": analysis_id,
        "version": next_version, "frontend_url": _frontend_url(activity_id),
    }


@mcp.tool()
def sync_completed_activities(source: str = "both") -> dict:
    """
    Syncs completed workout sessions from Garmin and/or Hevy into the CAIRN database.

    Call this tool BEFORE get_recent_activities or get_activity_analysis_data whenever
    the user asks about a recently completed session — e.g. 'look at my run today',
    'how was my workout yesterday', 'analyse my last session', 'what did I do today'.

    Args:
        source: Which source to sync. Options: "garmin", "hevy", "both" (default: "both")

    Returns:
        Dict with imported/skipped counts per source, or error messages if a source fails.
        Example: {"garmin": {"imported": 1, "skipped": 0}, "hevy": {"imported": 0, "skipped": 2}}
    """
    from coach.jobs.sync_completed_activities import run_sync, _garmin_sync, _hevy_sync

    if source == "both":
        return run_sync()

    conn = get_connection()
    try:
        if source == "garmin":
            result = _garmin_sync(conn)
            conn.commit()
            return {"garmin": result}
        elif source == "hevy":
            result = _hevy_sync(conn)
            conn.commit()
            return {"hevy": result}
        else:
            return {"error": f"Invalid source '{source}'. Use: garmin, hevy, both"}
    except Exception as exc:
        logger.error("sync_completed_activities failed (source=%s): %s", source, exc)
        return {"error": str(exc)}
    finally:
        conn.close()


# ── ASGI app factory ───────────────────────────────────────────────────────────

def create_mcp_asgi_app() -> ASGIApp:
    """
    Return the MCPServer ASGI app — UNAUTHENTICATED.

    KNOWN, TEMPORARY SECURITY TRADE-OFF (2026-08-14, per Alexander):
    BearerAuthMiddleware is defined above but intentionally NOT applied here.
    ChatGPT's custom-connector setup rejected our Bearer-token auth ("Fehler
    beim Erstellen des Konnektors"), so auth was removed to unblock the
    ChatGPT connector. This means /mcp is reachable by ANYONE who has the
    URL, with no authentication at all — including create_garmin_workout /
    move_garmin_workout / delete_garmin_workout (real writes/deletes on
    Garmin Connect) and every read tool (full training + health history).
    "URL not publicly advertised" is NOT a real control — Railway subdomains
    are enumerable and the URL now lives in the ChatGPT connector config.
    Durable fix: implement proper OAuth (the mcp SDK already exposes
    AuthSettings/token_verifier/auth_server_provider for this — see
    MCPServer.__init__), which ChatGPT connectors do support, instead of
    leaving this endpoint open indefinitely.

    The inner app handles POST /mcp (Streamable HTTP transport).

    transport_security: streamable_http_app() auto-enables DNS-rebinding
    protection with allowed_hosts=["127.0.0.1:*", ...] whenever no `host=`
    is passed (its internal default is host="127.0.0.1"). That rejects any
    real deployment host with "Invalid Host header". We pass an explicit
    TransportSecuritySettings instead — protection stays ON, but the actual
    public host (env var, falls back to the known Railway host) is allowed
    too. Never disable enable_dns_rebinding_protection outright — that would
    remove the protection entirely rather than fixing the allow-list.

    Requires: mcp>=1.0. MCP_API_KEY is no longer read here (BearerAuthMiddleware
    is not applied) — see the security trade-off note above.
    """
    public_host = os.getenv("MCP_PUBLIC_HOST", "web-production-297f2.up.railway.app")
    transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[public_host, "127.0.0.1:*", "localhost:*", "[::1]:*"],
        allowed_origins=[
            f"https://{public_host}",
            "http://127.0.0.1:*",
            "http://localhost:*",
            "http://[::1]:*",
        ],
    )
    return mcp.streamable_http_app(transport_security=transport_security)
