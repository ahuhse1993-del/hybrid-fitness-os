# -----------------------------------------------------------------------
# Diesen Block in coach/mcp_server.py einfügen — bei den anderen Write-Tools
# -----------------------------------------------------------------------

@mcp.tool()
def sync_completed_activities(source: str = "both") -> dict:
    """
    Syncs completed workout sessions from Garmin and/or Hevy into the CAIRN database.

    Call this tool BEFORE get_recent_activities or get_health_data whenever the user
    asks about a recently completed session (e.g. 'look at my run today', 'how was
    my workout yesterday', 'analyse my last session').

    Args:
        source: Which source to sync. Options: "garmin", "hevy", "both" (default: "both")

    Returns:
        Dict with imported/skipped counts per source, or error messages if a source fails.
    """
    from coach.jobs.sync_completed_activities import run_sync, _garmin_sync, _hevy_sync
    from database.connection import get_connection

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
        return {"error": str(exc)}
    finally:
        conn.close()