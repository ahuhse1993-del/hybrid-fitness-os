"""
CAIRN Garmin Batch Push Engine
Ein Login — mehrere Uploads — isolierte Fehlerbehandlung pro Session.
Sperrt gegen parallele Ausführung via PostgreSQL Advisory Lock.
Berührt niemals Garmin-Einträge die nicht in garmin_mcp_log registriert sind.

Fixes gegenueber der urspruenglichen Aufgabenstellung (2026-08-15, beim
Implementieren gefunden — alle live gegen echte Daten verifiziert):
- ADVISORY_LOCK_KEY war kein gueltiges Python-Literal (7472636e) -> jetzt
  deterministisch aus den Bytes von "cairn" berechnet.
- GARMIN_BLOCKED_TYPES existierte nicht in session_routing.py -> Alias ergaenzt.
- push_workout()/create_workout() wurden ohne sport= aufgerufen -> jede
  Rad-Session waere faelschlich als "running" gepusht worden. sport kommt
  jetzt aus training_plan.sport (via sessions_to_push), Fallback "running".
- Der "move"-Zweig hat die alte Terminierung nie unscheduled (derselbe Bug,
  der in move_garmin_workout am 2026-08-14 gefunden/gefixt wurde) -> haette
  einen doppelten Kalendereintrag auf Garmin hinterlassen. Jetzt:
  unschedule_workout(old_schedule_id) vor dem Neu-Terminieren, und die NEUE
  garmin_schedule_id wird in garmin_mcp_log nachgefuehrt (die alte war sonst
  verwaist und stimmte nach dem Move nicht mehr).
- training_plan.workout_steps ist fuer alle bisher real importierten
  Sessions leer (upsert_training_block befuellt diese Spalte nicht) -> ohne
  Fallback wuerde JEDE Session an "workout_def requires at least one step"
  scheitern. Fallback: ein Schritt ueber die volle duration_min, HF-Ziel aus
  session_zone geparst (z.B. "Z2" -> hr_zone 2), sonst kein Ziel — nutzt nur
  real vorhandene Daten, erfindet nichts.
"""

import logging
import time
from database.connection import get_connection
from coach.sync_utils import compute_content_hash, sessions_to_push
from coach.garmin_push import garmin_client, push_workout, schedule_workout
from coach.session_routing import GARMIN_BLOCKED_TYPES

logger = logging.getLogger(__name__)

# "cairn" als Bytes -> deterministischer Integer, passt in Postgres bigint
# (5 Bytes = 40 Bit, weit innerhalb von signed int64). Ersetzt das ungueltige
# Python-Literal 7472636e aus der urspruenglichen Aufgabenstellung.
ADVISORY_LOCK_KEY = int.from_bytes(b"cairn", "big")

# Pause nach jedem erfolgreichen Garmin-API-Call — reduziert Rate-Limiting
# bei Batches mit mehreren Sessions hintereinander.
GARMIN_CALL_DELAY_SECS = 0.8


def acquire_lock(conn) -> bool:
    """PostgreSQL Advisory Lock — verhindert parallele Job-Ausführung."""
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (ADVISORY_LOCK_KEY,))
        return cur.fetchone()[0]


def release_lock(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_KEY,))


def _mark_session(conn, session_id: int, status: str, error: str = None,
                   garmin_workout_id=None, content_hash: str = None):
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE training_plan SET
                sync_status    = %s,
                sync_error     = %s,
                last_synced_at = NOW(),
                garmin_workout_id = COALESCE(%s, garmin_workout_id),
                content_hash   = COALESCE(%s, content_hash)
            WHERE id = %s
        """, (status, error, garmin_workout_id, content_hash, session_id))
    conn.commit()


def _build_workout_steps(session: dict) -> list[dict]:
    """
    training_plan.workout_steps ist fuer real importierte Sessions aktuell
    leer (siehe Modul-Docstring). Fallback: ein Schritt ueber die volle
    Dauer, HF-Ziel aus session_zone geparst falls im Format "Z<1-5>"
    vorhanden (z.B. "Z2" -> hr_zone 2) — sonst kein Ziel. Nutzt nur real
    gespeicherte Felder (duration_min, session_zone), erfindet nichts.
    """
    steps = session.get("workout_steps")
    if steps:
        return steps
    duration_secs = (session.get("duration_min") or 60) * 60
    target = {"type": "no_target"}
    zone_str = (session.get("session_zone") or "").strip().upper()
    if len(zone_str) == 2 and zone_str[0] == "Z" and zone_str[1].isdigit():
        zone = int(zone_str[1])
        if 1 <= zone <= 5:
            target = {"type": "hr_zone", "zone": zone}
    return [{"type": "interval", "duration_secs": duration_secs, "target": target}]


def _push_single_session(client, conn, session: dict) -> dict:
    """
    Pusht eine einzelne Session. Erstellt oder updated.
    Berührt nur Garmin-Einträge die in garmin_mcp_log stehen.
    """
    sid = session["id"]
    session_date = str(session["session_date"])
    new_hash = compute_content_hash(session)
    sport = session.get("sport") or "running"

    # Bereits synced und unverändert?
    if session.get("sync_status") == "synced" and session.get("content_hash") == new_hash:
        return {"session_id": sid, "action": "unchanged"}

    external_id = session.get("external_id") or f"cairn_{sid}_{session_date}"

    # workout_def aufbauen (nutzt bestehenden build_workout_payload via push_workout)
    workout_def = {
        "name": f"CAIRN – {session['session_type']} {session_date}",
        "estimated_duration_secs": (session.get("duration_min") or 60) * 60,
        "description": session.get("notes") or "",
        "steps": _build_workout_steps(session),
    }

    existing_garmin_id = session.get("garmin_workout_id")

    if existing_garmin_id:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT scheduled_date, garmin_schedule_id FROM garmin_mcp_log "
                "WHERE external_id=%s AND status='success'",
                (external_id,)
            )
            row = cur.fetchone()
        old_scheduled_date = str(row[0]) if row else None
        old_schedule_id = row[1] if row else None

        if row and old_scheduled_date != session_date:
            # Datum geändert → move: alte Terminierung entfernen, bevor neu
            # terminiert wird (sonst bleibt ein doppelter Kalendereintrag
            # auf Garmin stehen — derselbe Bug wie in move_garmin_workout).
            if old_schedule_id:
                try:
                    client.unschedule_workout(old_schedule_id)
                    time.sleep(GARMIN_CALL_DELAY_SECS)
                except Exception as exc:
                    logger.warning(
                        "Konnte alte Terminierung %s nicht entfernen: %s — mache trotzdem weiter",
                        old_schedule_id, exc,
                    )
            result = schedule_workout(existing_garmin_id, session_date, client=client)
            time.sleep(GARMIN_CALL_DELAY_SECS)
            new_schedule_id = result["garmin_schedule_id"]
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE garmin_mcp_log SET scheduled_date=%s, garmin_schedule_id=%s, updated_at=NOW() "
                    "WHERE external_id=%s",
                    (session_date, new_schedule_id, external_id)
                )
            conn.commit()
            _mark_session(conn, sid, "synced", content_hash=new_hash)
            return {"session_id": sid, "action": "moved", "garmin_workout_id": existing_garmin_id}
        else:
            # Inhalt geändert (gleiches Datum) → delete + recreate
            try:
                client.delete_workout(existing_garmin_id)
                time.sleep(GARMIN_CALL_DELAY_SECS)
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE garmin_mcp_log SET status='deleted', updated_at=NOW() WHERE external_id=%s",
                        (external_id,)
                    )
                conn.commit()
            except Exception as e:
                logger.warning("Delete vor Recreate fehlgeschlagen: %s", e)

    # Neu erstellen
    result = push_workout(workout_def, session_date, sport=sport)
    time.sleep(GARMIN_CALL_DELAY_SECS)
    garmin_workout_id = result["garmin_workout_id"]
    garmin_schedule_id = result.get("garmin_schedule_id")

    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO garmin_mcp_log
                (external_id, action, status, garmin_workout_id, garmin_schedule_id,
                 workout_name, scheduled_date, metadata)
            VALUES (%s, 'push', 'success', %s, %s, %s, %s, '{"source":"batch"}')
            ON CONFLICT (external_id) DO UPDATE SET
                garmin_workout_id=EXCLUDED.garmin_workout_id,
                garmin_schedule_id=EXCLUDED.garmin_schedule_id,
                status='success', updated_at=NOW()
        """, (external_id, garmin_workout_id, garmin_schedule_id,
              workout_def["name"], session_date))
    conn.commit()

    _mark_session(conn, sid, "synced",
                  garmin_workout_id=str(garmin_workout_id),
                  content_hash=new_hash)
    action = "created" if not existing_garmin_id else "updated"
    return {"session_id": sid, "action": action, "garmin_workout_id": garmin_workout_id}


def run_batch(session_ids: list[int] = None) -> dict:
    """
    Hauptfunktion: Login einmal, alle Sessions pushen.
    session_ids=None → automatische Auswahl (tägl. Job).
    session_ids=[...] → explizite Liste (MCP-Tool).
    """
    conn = get_connection()
    if not acquire_lock(conn):
        conn.close()
        return {"error": "Anderer Batch-Job läuft gerade. Bitte später nochmal versuchen."}

    results = {"created": [], "updated": [], "moved": [], "unchanged": [], "failed": []}

    try:
        if session_ids:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id,
                           (week_date + (day_of_week - 1) * INTERVAL '1 day')::date AS session_date,
                           session_type, session_zone, distance_km, duration_min,
                           notes, workout_steps, plan_week, phase, sport,
                           garmin_workout_id, external_id, content_hash, sync_status
                    FROM training_plan WHERE id = ANY(%s)
                """, (session_ids,))
                cols = [d[0] for d in cur.description]
                sessions = [dict(zip(cols, row)) for row in cur.fetchall()]
        else:
            sessions = sessions_to_push(conn)

        if not sessions:
            return {"message": "Nichts zu pushen.", **results}

        # Validierung vor erstem Garmin-Kontakt
        blocked = [s for s in sessions if s["session_type"] in GARMIN_BLOCKED_TYPES]
        if blocked:
            return {
                "error": "Enthält geblockte Session-Typen",
                "blocked": [{"id": s["id"], "type": s["session_type"]} for s in blocked]
            }

        # Genau einmal einloggen
        logger.info("Garmin Login für %d Sessions", len(sessions))
        client = garmin_client()

        for session in sessions:
            try:
                result = _push_single_session(client, conn, session)
                action = result["action"]
                if action in results:
                    results[action].append(result.get("garmin_workout_id") or session["id"])
                else:
                    results["unchanged"].append(session["id"])
            except Exception as exc:
                logger.error("Session %d fehlgeschlagen: %s", session["id"], exc)
                _mark_session(conn, session["id"], "failed", error=str(exc))
                results["failed"].append({"session_id": session["id"], "error": str(exc)})

        return results

    finally:
        release_lock(conn)
        conn.close()
