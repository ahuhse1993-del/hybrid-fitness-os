#!/usr/bin/env python3
"""
Fügt purge_all_sessions Tool hinzu — löscht alle Sessions + plan_weeks aus der DB.
Garmin-Workouts müssen vorher via bulk_delete_garmin_workouts entfernt werden.

Run from repo root: python patch_purge.py
"""

PATH = "coach/mcp_server.py"
with open(PATH, "r") as f:
    src = f.read()

INSERT_BEFORE = "@mcp.tool()\ndef upsert_milestones"

NEW_TOOL = '''
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


'''

assert INSERT_BEFORE in src, "Einfügepunkt nicht gefunden"
src = src.replace(INSERT_BEFORE, NEW_TOOL + INSERT_BEFORE, 1)

with open(PATH, "w") as f:
    f.write(src)

print("✓ coach/mcp_server.py gepatcht — purge_all_sessions Tool hinzugefügt")
