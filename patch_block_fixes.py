#!/usr/bin/env python3
"""
Fixes für upsert_training_block:
  1. phase + km_factor in SESSION-INSERT
  2. sport als Alias für sport_hint
  3. plans.total_weeks nach Import setzen
  4. Neues Tool: archive_plan_sessions
  5. Neues Tool: fix_session_phases_from_weeks (korrigiert bestehende Sessions)

Run from repo root:  python patch_block_fixes.py
"""

PATH = "coach/mcp_server.py"
with open(PATH, "r") as f:
    src = f.read()

# ─── PATCH 1: sport-Alias + phase + km_factor in SESSION-INSERT ──────────────
OLD1 = '''                resolved_sport = s.get("sport_hint") or routing["garmin_sport"]

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
                            distance_km, duration_min, notes, plan_id,
                            status, source, garmin_push_required, hevy_routine_key,
                            sport, sync_target, name, target, elevation_gain_m)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (external_id) DO UPDATE SET
                           week_date=EXCLUDED.week_date, day_of_week=EXCLUDED.day_of_week,
                           session_type=EXCLUDED.session_type, session_zone=EXCLUDED.session_zone,
                           distance_km=EXCLUDED.distance_km, duration_min=EXCLUDED.duration_min,
                           notes=EXCLUDED.notes, plan_id=EXCLUDED.plan_id,
                           source=EXCLUDED.source, garmin_push_required=EXCLUDED.garmin_push_required,
                           hevy_routine_key=EXCLUDED.hevy_routine_key,
                           sport=EXCLUDED.sport, sync_target=EXCLUDED.sync_target,
                           name=EXCLUDED.name, target=EXCLUDED.target,
                           elevation_gain_m=EXCLUDED.elevation_gain_m, updated_at=now()
                       RETURNING id, (xmax = 0) AS inserted""",
                    (
                        s["external_id"], week_date, day_of_week, s["session_type"],
                        s.get("session_zone"), s.get("distance_km"), s.get("duration_min"),
                        s.get("notes"), plan_id,
                        s.get("status", "planned"), routing["source"],
                        routing["garmin_push_required"], hevy_routine_key,
                        resolved_sport, routing["sync_target"], s.get("name"),
                        json.dumps(s["target"]) if s.get("target") is not None else None,
                        s.get("elevation_gain_m"),
                    ),
                )'''

NEW1 = '''                # sport: sport_hint hat Vorrang, sport als Alias, dann garmin_sport
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
                )'''

# ─── PATCH 2: total_weeks nach Import in plans setzen ────────────────────────
OLD2 = '''        conn.commit()
        logger.info(
            "upsert_training_block committed: plan_id=%s created=%s updated=%s race_date_changed=%s routing=%s",
            plan_id, created, updated, race_date_changed, routing_summary,
        )'''

NEW2 = '''        # total_weeks aus tatsächlichen Wochen ableiten und in plans speichern
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE plans SET total_weeks=%s WHERE id=%s",
                (len(sorted_weeks), plan_id),
            )

        conn.commit()
        logger.info(
            "upsert_training_block committed: plan_id=%s created=%s updated=%s race_date_changed=%s routing=%s total_weeks=%s",
            plan_id, created, updated, race_date_changed, routing_summary, len(sorted_weeks),
        )'''

# ─── PATCH 3: Neue Tools — archive_plan_sessions + fix_session_phases ────────
INSERT_BEFORE = "@mcp.tool()\ndef upsert_milestones"

NEW_TOOLS = '''
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


'''

assert OLD1 in src, "PATCH 1 nicht gefunden — mcp_server.py Version prüfen"
src = src.replace(OLD1, NEW1, 1)

assert OLD2 in src, "PATCH 2 nicht gefunden"
src = src.replace(OLD2, NEW2, 1)

assert INSERT_BEFORE in src, "PATCH 3 Einfügepunkt nicht gefunden"
src = src.replace(INSERT_BEFORE, NEW_TOOLS + INSERT_BEFORE, 1)

with open(PATH, "w") as f:
    f.write(src)

print("✓ coach/mcp_server.py gepatcht:")
print("  - phase + km_factor + sport-Alias in upsert_training_block")
print("  - total_weeks wird nach Import gesetzt")
print("  - Neues Tool: archive_plan_sessions(plan_id)")
print("  - Neues Tool: fix_session_phases_from_weeks(plan_id)")
