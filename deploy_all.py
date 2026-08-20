#!/usr/bin/env python3
"""
CAIRN Deploy Script — run from the root of the hybrid-fitness-os repo:
  python3 deploy_all.py

Was gemacht wird:
  1. Migration: plan_weeks + race_elevation_m/priority + is_peak + milestones.week_number
  2. coach/mcp_server.py: race-Felder + plan_weeks-Upsert + update_planned_workout
  3. coach/api.py: /api/frontend/plan nutzt plan_weeks + gibt elevation/priority/is_peak zurück
  4. files/cairn_app_v6.html: HÖHE statt PHASE, Peak-Balken, Dot-Track raus
"""
import os, sys, re

ROOT = os.getcwd()

def check_file(path):
    if not os.path.exists(path):
        print(f"✗ Nicht gefunden: {path}")
        print("  Bitte aus dem Repo-Root ausführen: cd ~/hybrid-fitness-os && python3 deploy_all.py")
        sys.exit(1)

check_file("coach/mcp_server.py")
check_file("coach/api.py")
check_file("files/cairn_app_v6.html")

print("CAIRN Deploy — starte Patches...\n")

# ═══════════════════════════════════════════════════════════════════════════════
# 1. MIGRATION
# ═══════════════════════════════════════════════════════════════════════════════
MIGRATION_SQL = """\
-- Migration: 20260819_plan_weeks
-- 1) Race-Felder auf plans (Höhe, Priorität, Zielzeit)
-- 2) plan_weeks (Phase, Deload, Peak, Wochenkilometer, Fokus)
-- 3) week_id FK auf training_plan
-- 4) week_number auf milestones (Milestone → Woche)
-- Idempotent: IF NOT EXISTS / ON CONFLICT

BEGIN;

ALTER TABLE plans
  ADD COLUMN IF NOT EXISTS race_elevation_m  INTEGER,
  ADD COLUMN IF NOT EXISTS race_priority     CHAR(1) DEFAULT 'A',
  ADD COLUMN IF NOT EXISTS target_time       VARCHAR(20),
  ADD COLUMN IF NOT EXISTS plan_start_date   DATE;

CREATE TABLE IF NOT EXISTS plan_weeks (
    id              SERIAL PRIMARY KEY,
    plan_id         INTEGER NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
    week_number     INTEGER NOT NULL,
    week_start      DATE    NOT NULL,
    phase           VARCHAR(20) NOT NULL DEFAULT 'base',
    is_deload       BOOLEAN NOT NULL DEFAULT FALSE,
    is_peak         BOOLEAN NOT NULL DEFAULT FALSE,
    target_run_km   NUMERIC(6,1),
    week_focus      VARCHAR(200),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (plan_id, week_number)
);

CREATE INDEX IF NOT EXISTS plan_weeks_plan_week_start
    ON plan_weeks (plan_id, week_start);

DROP TRIGGER IF EXISTS plan_weeks_updated_at ON plan_weeks;
CREATE TRIGGER plan_weeks_updated_at
    BEFORE UPDATE ON plan_weeks
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

ALTER TABLE training_plan
  ADD COLUMN IF NOT EXISTS week_id INTEGER REFERENCES plan_weeks(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS training_plan_week_id_idx
    ON training_plan (week_id);

-- Milestones: Wochenbezug
ALTER TABLE milestones
  ADD COLUMN IF NOT EXISTS week_number INTEGER;

COMMIT;
"""

MIGRATION_PATH = "database/migrations/20260819_plan_weeks.sql"
if not os.path.exists(MIGRATION_PATH):
    with open(MIGRATION_PATH, "w") as f:
        f.write(MIGRATION_SQL)
    print(f"✓ Migration erstellt: {MIGRATION_PATH}")
else:
    # Prüfen ob is_peak und milestones.week_number schon drin sind
    existing = open(MIGRATION_PATH).read()
    updated = False
    if "is_peak" not in existing:
        existing = existing.replace(
            "    is_deload       BOOLEAN NOT NULL DEFAULT FALSE,",
            "    is_deload       BOOLEAN NOT NULL DEFAULT FALSE,\n    is_peak         BOOLEAN NOT NULL DEFAULT FALSE,"
        )
        updated = True
    if "milestones" not in existing or "week_number" not in existing:
        existing = existing.replace("COMMIT;", "ALTER TABLE milestones\n  ADD COLUMN IF NOT EXISTS week_number INTEGER;\n\nCOMMIT;")
        updated = True
    if updated:
        with open(MIGRATION_PATH, "w") as f:
            f.write(existing)
        print(f"✓ Migration aktualisiert (is_peak + milestones.week_number): {MIGRATION_PATH}")
    else:
        print(f"  Migration schon aktuell: {MIGRATION_PATH}")

# ═══════════════════════════════════════════════════════════════════════════════
# 2. mcp_server.py
# ═══════════════════════════════════════════════════════════════════════════════
with open("coach/mcp_server.py") as f:
    src = f.read()

changed = False

# 2a: UPDATE plans
OLD_UPDATE = '''                    """UPDATE plans SET name=%s, goal_type=%s, race_name=%s, race_date=%s,
                           race_distance_km=%s
                       WHERE id=%s""",
                    (
                        race.get("name") or race.get("race_name"), race.get("goal_type"),
                        race.get("name") or race.get("race_name"), race["race_date"],
                        race.get("race_distance_km"), plan_id,
                    ),'''
NEW_UPDATE = '''                    """UPDATE plans SET name=%s, goal_type=%s, race_name=%s, race_date=%s,
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
                    ),'''
if OLD_UPDATE in src:
    src = src.replace(OLD_UPDATE, NEW_UPDATE, 1); changed = True
    print("✓ mcp_server.py: UPDATE plans mit race-Feldern")
else:
    print("  mcp_server.py: UPDATE plans — schon gepacht")

# 2b: INSERT plans
OLD_INSERT = '''                    """INSERT INTO plans (name, goal_type, race_name, race_date, race_distance_km, status)
                       VALUES (%s, %s, %s, %s, %s, 'active') RETURNING id""",
                    (
                        race.get("name") or race.get("race_name"), race.get("goal_type"),
                        race.get("name") or race.get("race_name"), race["race_date"],
                        race.get("race_distance_km"),
                    ),'''
NEW_INSERT = '''                    """INSERT INTO plans (name, goal_type, race_name, race_date, race_distance_km,
                                           race_elevation_m, race_priority, target_time, status)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'active') RETURNING id""",
                    (
                        race.get("name") or race.get("race_name"), race.get("goal_type"),
                        race.get("name") or race.get("race_name"), race["race_date"],
                        race.get("race_distance_km"),
                        race.get("race_elevation_m"),
                        race.get("race_priority", "A"),
                        race.get("target_time"),
                    ),'''
if OLD_INSERT in src:
    src = src.replace(OLD_INSERT, NEW_INSERT, 1); changed = True
    print("✓ mcp_server.py: INSERT plans mit race-Feldern")
else:
    print("  mcp_server.py: INSERT plans — schon gepacht")

# 2c: plan_weeks upsert nach Sessions
OLD_COMMIT = '''        conn.commit()
        logger.info(
            "upsert_training_block committed: plan_id=%s created=%s updated=%s race_date_changed=%s routing=%s",
            plan_id, created, updated, race_date_changed, routing_summary,
        )'''
NEW_COMMIT = '''        # ── plan_weeks: Wochenstruktur ableiten ──────────────────────────────────
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

        all_kms = [_week_km(week_groups[w]) for w in sorted_weeks]
        max_km = max(all_kms) if all_kms else 0

        with conn.cursor() as cur:
            pw_id_map: dict = {}
            prev_km = 0.0
            for wnum, wstart in enumerate(sorted_weeks, start=1):
                slist = week_groups[wstart]
                wkm = _week_km(slist)
                phases_in_week = [s.get("phase") or s.get("session_zone") for s in slist if s.get("phase") or s.get("session_zone")]
                phase_val = phases_in_week[0] if phases_in_week else "base"
                is_deload = (prev_km > 0 and wkm > 0 and wkm / prev_km < 0.75)
                is_peak   = (max_km > 0 and wkm == max_km)
                if wkm > 0:
                    prev_km = wkm
                cur.execute(
                    """INSERT INTO plan_weeks (plan_id, week_number, week_start, phase, is_deload, is_peak, target_run_km)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (plan_id, week_number) DO UPDATE SET
                           week_start=EXCLUDED.week_start, phase=EXCLUDED.phase,
                           is_deload=EXCLUDED.is_deload, is_peak=EXCLUDED.is_peak,
                           target_run_km=EXCLUDED.target_run_km, updated_at=now()
                       RETURNING id""",
                    (plan_id, wnum, wstart, phase_val, is_deload, is_peak, round(wkm, 1) if wkm else None),
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

        conn.commit()
        logger.info(
            "upsert_training_block committed: plan_id=%s created=%s updated=%s race_date_changed=%s routing=%s",
            plan_id, created, updated, race_date_changed, routing_summary,
        )'''
if OLD_COMMIT in src and "plan_weeks" not in src:
    src = src.replace(OLD_COMMIT, NEW_COMMIT, 1); changed = True
    print("✓ mcp_server.py: plan_weeks-Upsert nach Sessions")
elif "plan_weeks" in src:
    # already has plan_weeks, just make sure is_peak is there
    if "is_peak" not in src:
        src = src.replace(
            "is_deload=EXCLUDED.is_deload,\n                           target_run_km=EXCLUDED.target_run_km",
            "is_deload=EXCLUDED.is_deload, is_peak=EXCLUDED.is_peak,\n                           target_run_km=EXCLUDED.target_run_km"
        ); changed = True
        print("✓ mcp_server.py: is_peak in plan_weeks ergänzt")
    else:
        print("  mcp_server.py: plan_weeks-Upsert — schon gepacht")
else:
    print("  mcp_server.py: plan_weeks — schon gepacht")

# 2d: update_planned_workout tool
NEW_TOOL = '''
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

    If date changes and crosses a week boundary, week_id is recalculated.
    Does NOT auto-push to Garmin — sets sync_status='dirty' if needed.
    reason: short log note, e.g. "moved long run due to weather".

    Returns: id, external_id, changed_fields, sync_status_after, needs_garmin_push.
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
                "session_type": "session_type", "session_zone": "session_zone",
                "name": "name", "distance_km": "distance_km", "duration_min": "duration_min",
                "notes": "notes", "elevation_gain_m": "elevation_gain_m",
                "km_factor": "km_factor", "status": "status",
                "sync_target": "sync_target", "phase": "phase",
            }

            sets, vals, changed_fields = [], [], []

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
                        """SELECT id FROM plan_weeks WHERE week_start=%s
                           AND plan_id=(SELECT plan_id FROM training_plan WHERE id=%s)""",
                        (new_week_date, row_id),
                    )
                    pw = cur.fetchone()
                    if pw:
                        sets.append("week_id=%s"); vals.append(pw[0])
                        changed_fields.append("week_id")

            if "target" in patch:
                sets.append("target=%s")
                vals.append(json.dumps(patch["target"]) if patch["target"] is not None else None)
                changed_fields.append("target")

            for key, col in COLUMN_MAP.items():
                if key in patch:
                    sets.append(f"{col}=%s"); vals.append(patch[key])
                    changed_fields.append(key)

            GARMIN_SENSITIVE = {"date", "session_type", "distance_km", "duration_min",
                                "notes", "session_zone", "name", "target", "elevation_gain_m"}
            new_sync_status = cur_sync_status
            if garmin_id and (set(changed_fields) & GARMIN_SENSITIVE):
                sets.append("sync_status=%s"); vals.append("dirty")
                new_sync_status = "dirty"
                changed_fields.append("_sync_status→dirty")

            if not sets:
                return {"id": row_id, "external_id": ext_id, "changed_fields": [], "note": "Keine Änderungen."}

            sets.append("updated_at=now()"); vals.append(row_id)
            cur.execute(f"UPDATE training_plan SET {', '.join(sets)} WHERE id=%s", vals)
            logger.info("update_planned_workout: id=%s ext=%s changed=%s reason=%r",
                        row_id, ext_id, changed_fields, reason)

        conn.commit()
        return {
            "id": row_id, "external_id": ext_id, "changed_fields": changed_fields,
            "sync_status_after": new_sync_status,
            "needs_garmin_push": (garmin_id is not None and new_sync_status == "dirty"),
        }
    except Exception:
        conn.rollback(); raise
    finally:
        conn.close()


'''
INSERT_BEFORE = "@mcp.tool()\ndef upsert_milestones"
if "def update_planned_workout" not in src:
    src = src.replace(INSERT_BEFORE, NEW_TOOL + INSERT_BEFORE, 1); changed = True
    print("✓ mcp_server.py: update_planned_workout Tool hinzugefügt")
else:
    print("  mcp_server.py: update_planned_workout — schon vorhanden")

if changed:
    with open("coach/mcp_server.py", "w") as f:
        f.write(src)
    print("✓ coach/mcp_server.py geschrieben")

# ═══════════════════════════════════════════════════════════════════════════════
# 3. api.py — /api/frontend/plan
# ═══════════════════════════════════════════════════════════════════════════════
with open("coach/api.py") as f:
    api_src = f.read()

if "race_elevation_m" not in api_src or "is_peak" not in api_src:
    # Replace the entire frontend_plan function
    pattern = r'(@app\.route\(\'/api/frontend/plan\'\)\ndef frontend_plan\(\):.*?)(# ─── FRONTEND API: Sync)'
    match = re.search(pattern, api_src, re.DOTALL)
    if match:
        NEW_FN = '''@app.route('/api/frontend/plan')
def frontend_plan():
    """
    Planansicht: Race-Info, Phase, Belastungskurve (aus plan_weeks), Milestones.
    Fallback auf Heuristik wenn plan_weeks leer.
    """
    try:
        today = get_today()
        monday_today = today - timedelta(days=today.weekday())
        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            SELECT id, name, goal_type, race_name, race_date, race_distance_km,
                   total_weeks, current_week, status,
                   race_elevation_m, race_priority, target_time
            FROM plans WHERE status = 'active' ORDER BY created_at DESC LIMIT 1
        """)
        plan_row = cur.fetchone()
        if not plan_row:
            conn.close()
            return jsonify({"status": "ok", "has_plan": False})

        plan_id       = plan_row[0]
        race_date     = plan_row[4]
        elevation_m   = plan_row[9]
        race_priority = plan_row[10] or "A"
        target_time   = plan_row[11]
        countdown_days = (race_date - today).days if race_date else None

        # plan_weeks (kanonisch)
        cur.execute("""
            SELECT week_number, week_start, phase, is_deload, is_peak, target_run_km, week_focus
            FROM plan_weeks WHERE plan_id = %s ORDER BY week_number
        """, (plan_id,))
        pw_rows = cur.fetchall()

        weeks = []
        current_week = 1
        total_weeks  = 16

        if pw_rows:
            total_weeks = len(pw_rows)
            for r in pw_rows:
                wnum, wstart, phase, is_deload, is_peak, km, focus = r
                is_current = (wstart <= monday_today < wstart + timedelta(weeks=1))
                if is_current:
                    current_week = wnum
                weeks.append({
                    "week_date":   str(wstart),
                    "week_number": wnum,
                    "total_km":    float(km) if km else 0,
                    "phase":       phase or "base",
                    "is_deload":   bool(is_deload),
                    "is_peak":     bool(is_peak),
                    "is_current":  is_current,
                    "week_focus":  focus or "",
                })
        else:
            # Fallback aus training_plan
            cur.execute("""
                SELECT week_date,
                       ROUND(COALESCE(SUM(distance_km), 0)::numeric, 1) AS total_km,
                       MAX(phase) AS phase
                FROM training_plan
                WHERE plan_id = %s
                  AND session_type NOT IN (\'Rest Day\', \'Strength Training\', \'Core\', \'Mobility\')
                GROUP BY week_date ORDER BY week_date
            """, (plan_id,))
            fb_rows = cur.fetchall()
            total_weeks = max(len(fb_rows), plan_row[6] or 16)
            prev_km = 0.0
            all_kms = [float(r[1]) if r[1] else 0 for r in fb_rows]
            max_km = max(all_kms) if all_kms else 0
            for i, r in enumerate(fb_rows):
                wdate, wkm, phase = r
                wkm = float(wkm) if wkm else 0
                is_deload = (prev_km > 0 and wkm > 0 and wkm / prev_km < 0.75)
                is_peak   = (max_km > 0 and wkm == max_km)
                if wkm > 0:
                    prev_km = wkm
                is_current = (str(wdate) == str(monday_today))
                if is_current:
                    current_week = i + 1
                weeks.append({
                    "week_date":   str(wdate),
                    "week_number": i + 1,
                    "total_km":    wkm,
                    "phase":       phase or "base",
                    "is_deload":   is_deload,
                    "is_peak":     is_peak,
                    "is_current":  is_current,
                    "week_focus":  "",
                })
            if not any(w["is_current"] for w in weeks):
                current_week = plan_row[7] or 1

        current_phase  = weeks[current_week - 1]["phase"] if weeks and 1 <= current_week <= len(weeks) else "base"
        current_focus  = weeks[current_week - 1]["week_focus"] if weeks and 1 <= current_week <= len(weeks) else ""

        # Milestones
        cur.execute("""
            SELECT id, step_number, title, criterion, target_date,
                   status, evidence, notes, achieved_at, week_number
            FROM milestones WHERE plan_id = %s ORDER BY step_number
        """, (plan_id,))
        milestone_rows = cur.fetchall()

        milestones = []
        next_set = False
        for r in milestone_rows:
            ms_status = r[5]
            is_next = (ms_status == 'open' and not next_set)
            if is_next:
                next_set = True
            milestones.append({
                "id": r[0], "step_number": r[1], "title": r[2],
                "criterion": r[3] or "", "target_date": str(r[4]) if r[4] else None,
                "status": ms_status, "is_next": is_next,
                "evidence": r[6] or "", "notes": r[7] or "",
                "achieved_at": str(r[8]) if r[8] else None,
                "week_number": r[9],
            })

        conn.close()

        return jsonify({
            "status": "ok",
            "has_plan": True,
            "race": {
                "name":           plan_row[3],
                "date":           str(race_date) if race_date else None,
                "distance_km":    plan_row[5],
                "elevation_m":    elevation_m,
                "priority":       race_priority,
                "target_time":    target_time,
                "countdown_days": countdown_days,
            },
            "block": {
                "current_week": current_week,
                "total_weeks":  total_weeks,
                "phase":        current_phase,
                "week_focus":   current_focus,
            },
            "weeks":      weeks,
            "milestones": milestones,
        })

    except Exception as e:
        return jsonify({"status": "error", "message": str(e), "trace": traceback.format_exc()}), 500


'''
        api_src = api_src[:match.start(1)] + NEW_FN + match.group(2) + api_src[match.end():]
        with open("coach/api.py", "w") as f:
            f.write(api_src)
        print("✓ coach/api.py: /api/frontend/plan aktualisiert (is_peak, week_focus)")
    else:
        print("✗ coach/api.py: Funktionsgrenze nicht gefunden — manuell prüfen")
else:
    print("  coach/api.py: schon aktuell")

# ═══════════════════════════════════════════════════════════════════════════════
# 4. Frontend: files/cairn_app_v6.html
# ═══════════════════════════════════════════════════════════════════════════════
with open("files/cairn_app_v6.html") as f:
    html = f.read()

html_changed = False

# 4a: Hero — HÖHE statt PHASE
OLD_HERO = "        <div><span class=\"text-small\">PHASE</span><strong>${phase}</strong></div>"
NEW_HERO_LINE = "        <div><span class=\"text-small\">HÖHE</span><strong>${hmLabel}</strong></div>"
if OLD_HERO in html:
    html = html.replace(OLD_HERO, NEW_HERO_LINE, 1); html_changed = True
    print("✓ Frontend: HÖHE statt PHASE in Hero-Metriken")

# 4b: race fields renaming  (distance_km → distLabel, add hmLabel + priorityBadge)
OLD_DIST = "  const distLabel = race.distance_km ? `${race.distance_km} km` : (race.distance || '—');"
NEW_DIST_BLOCK = (
    "  // race fields: distance_km, elevation_m, priority, countdown_days\n"
    "  const distLabel = race.distance_km ? `${race.distance_km} km` : '—';\n"
    "  const hmLabel   = race.elevation_m  ? `${Number(race.elevation_m).toLocaleString('de-CH')} HM` : '—';\n"
    "  const priorityBadge = `${race.priority || 'A'}-RENNEN`;\n"
    "  const raceDateLabel = race.date\n"
    "    ? new Date(race.date + 'T00:00:00').toLocaleDateString('de-CH', { day: 'numeric', month: 'long', year: 'numeric' })\n"
    "    : '';"
)
if OLD_DIST in html:
    html = html.replace(OLD_DIST, NEW_DIST_BLOCK, 1); html_changed = True
    print("✓ Frontend: hmLabel + priorityBadge + Datumsformat hinzugefügt")

# 4c: priorityBadge + raceDateLabel in Hero HTML
OLD_BADGE = '        <p class="eyebrow text-small">A-RENNEN</p>'
NEW_BADGE = '        <p class="eyebrow text-small">${priorityBadge}</p>'
if OLD_BADGE in html:
    html = html.replace(OLD_BADGE, NEW_BADGE, 1); html_changed = True
    print("✓ Frontend: Priority-Badge dynamisch")

OLD_DATE = '        <p class="race-date">${race.date || \'\'}</p>'
NEW_DATE = '        <p class="race-date">${raceDateLabel}</p>'
if OLD_DATE in html:
    html = html.replace(OLD_DATE, NEW_DATE, 1); html_changed = True
    print("✓ Frontend: Datum auf Deutsch")

# 4d: is_peak in weeklyLoad-Map
OLD_WLOAD = (
    "  const weeklyLoad = (data.weeks || []).map(w => ({\n"
    "    week: w.week_number,\n"
    "    week_number: w.week_number,\n"
    "    km: w.total_km || 0,\n"
    "    is_deload: w.is_deload,\n"
    "  }));"
)
NEW_WLOAD = (
    "  const weeklyLoad = (data.weeks || []).map(w => ({\n"
    "    week: w.week_number,\n"
    "    week_number: w.week_number,\n"
    "    km: w.total_km || 0,\n"
    "    is_deload: w.is_deload,\n"
    "    is_peak: w.is_peak || false,\n"
    "  }));"
)
if OLD_WLOAD in html:
    html = html.replace(OLD_WLOAD, NEW_WLOAD, 1); html_changed = True
    print("✓ Frontend: is_peak in weeklyLoad")

# 4e: week_focus aus block
OLD_PLAN_OBJ = (
    "  const plan = {\n"
    "    race_date: race.date || '',\n"
    "    days_until_race: race.countdown_days || null,\n"
    "    phase_focus: block.phase || '',\n"
    "  };"
)
NEW_PLAN_OBJ = (
    "  const plan = {\n"
    "    race_date: race.date || '',\n"
    "    days_until_race: race.countdown_days || null,\n"
    "    phase_focus: block.week_focus || block.phase || '',\n"
    "  };"
)
if OLD_PLAN_OBJ in html:
    html = html.replace(OLD_PLAN_OBJ, NEW_PLAN_OBJ, 1); html_changed = True
    print("✓ Frontend: week_focus als Phasenfokus")

# 4f: is_peak + peak-Klasse in buildProgressCard + Dot-Track entfernen
OLD_BARS = (
    "    const isDone = i < currentWeek;\n"
    "    const isCurrent = i === currentWeek;\n"
    "    const isDeload = weekData.is_deload;\n"
    "    const classes = ['load-week', isDone ? 'done' : '', isCurrent ? 'current' : '', isDeload ? 'deload' : ''].filter(Boolean).join(' ');\n"
    "    const valueTag = (isCurrent || (i === totalWeeks)) && km ? `<span class=\"load-value text-small\">${Math.round(km)}</span>` : '';"
)
NEW_BARS = (
    "    const isDone    = i < currentWeek;\n"
    "    const isCurrent = i === currentWeek;\n"
    "    const isDeload  = weekData.is_deload;\n"
    "    const isPeak    = weekData.is_peak;\n"
    "    const classes = ['load-week', isDone?'done':'', isCurrent?'current':'', isDeload?'deload':'', isPeak?'peak':''].filter(Boolean).join(' ');\n"
    "    const showVal = (isCurrent || isPeak) && km;\n"
    "    const valueTag = showVal ? `<span class=\"load-value text-small\">${Math.round(km)}</span>` : '';"
)
if OLD_BARS in html:
    html = html.replace(OLD_BARS, NEW_BARS, 1); html_changed = True
    print("✓ Frontend: isPeak-Klasse im Balkendiagramm")

# 4g: Dot-Track und Progress-Footer raus, Deload-Label dynamisch
OLD_CHART_RETURN = (
    "  return `\n"
    "    <div class=\"section-head\">\n"
    "      <div><p class=\"eyebrow text-small\">AKTUELLE PHASE</p><h2 id=\"phase-title\">${phase}</h2></div>\n"
    "      <div class=\"focus\"><span class=\"text-small\">FOKUS</span><strong>${plan.phase_focus || '—'}</strong></div>\n"
    "    </div>\n"
    "    <div class=\"phase-tabs text-small\" aria-label=\"Trainingsphasen\">${phaseTabs}</div>\n"
    "    <div class=\"load-block\">\n"
    "      <div class=\"load-head\"><div><p class=\"eyebrow text-small\">BELASTUNGSVERTEILUNG</p><h3>Wochenkilometer</h3></div><p class=\"text-small\">DELOAD · W4 / W8 / W12</p></div>\n"
    "      <div class=\"load-chart\" role=\"img\" aria-label=\"Geplante Wochenkilometer\">${bars.join('')}</div>\n"
    "    </div>\n"
    "    <div class=\"week-track\" role=\"img\" aria-label=\"Woche ${currentWeek} von ${totalWeeks}\">${dots.join('')}</div>\n"
    "    <div class=\"track-labels text-small\"><span>1</span><span>${Math.round(totalWeeks/4)}</span><span class=\"current-label\">${currentWeek} · DU BIST HIER</span><span>${Math.round(totalWeeks*0.75)}</span><span>${totalWeeks}</span></div>\n"
    "    <div class=\"progress-foot text-small\"><span>${done} WOCHE${done !== 1 ? 'N' : ''} ERLEDIGT</span><span>${remaining} WOCHE${remaining !== 1 ? 'N' : ''} + RACE</span></div>\n"
    "  `;"
)
NEW_CHART_RETURN = (
    "  const deloadLabel = weeklyLoad.filter(w=>w.is_deload).map(w=>'W'+(w.week_number||w.week)).join(' / ') || '';\n"
    "  return `\n"
    "    <div class=\"section-head\">\n"
    "      <div><p class=\"eyebrow text-small\">AKTUELLE PHASE</p><h2 id=\"phase-title\">${phase}</h2></div>\n"
    "      <div class=\"focus\"><span class=\"text-small\">FOKUS</span><strong>${plan.phase_focus || '—'}</strong></div>\n"
    "    </div>\n"
    "    <div class=\"phase-tabs text-small\" aria-label=\"Trainingsphasen\">${phaseTabs}</div>\n"
    "    <div class=\"load-block\">\n"
    "      <div class=\"load-head\">\n"
    "        <div><p class=\"eyebrow text-small\">BELASTUNGSVERTEILUNG</p><h3>Wochenkilometer</h3></div>\n"
    "        ${deloadLabel ? `<p class=\"text-small\">DELOAD · ${deloadLabel}</p>` : ''}\n"
    "      </div>\n"
    "      <div class=\"load-chart\" role=\"img\" aria-label=\"Geplante Wochenkilometer\">${bars.join('')}</div>\n"
    "    </div>\n"
    "  `;"
)
if OLD_CHART_RETURN in html:
    html = html.replace(OLD_CHART_RETURN, NEW_CHART_RETURN, 1); html_changed = True
    print("✓ Frontend: Dot-Track + Progress-Footer entfernt, Deload-Label dynamisch")

# Peak-CSS falls noch nicht drin
if ".load-week.peak" not in html:
    html = html.replace(
        "#cairn-plan-soft .load-week.deload .load-bar {",
        "#cairn-plan-soft .load-week.peak .load-bar { background: var(--forest); opacity: 1; }\n    #cairn-plan-soft .load-week.deload .load-bar {"
    ); html_changed = True
    print("✓ Frontend: Peak-CSS hinzugefügt")

if html_changed:
    with open("files/cairn_app_v6.html", "w") as f:
        f.write(html)
    print("✓ files/cairn_app_v6.html geschrieben")
else:
    print("  files/cairn_app_v6.html — schon aktuell")

# ═══════════════════════════════════════════════════════════════════════════════
# Syntax-Check
# ═══════════════════════════════════════════════════════════════════════════════
import py_compile
for f in ["coach/mcp_server.py", "coach/api.py"]:
    try:
        py_compile.compile(f, doraise=True)
        print(f"✓ {f} — Syntax OK")
    except py_compile.PyCompileError as e:
        print(f"✗ {f} — SYNTAX FEHLER: {e}")

print()
print("─" * 60)
print("Fertig! Jetzt committen:")
print()
print("  git add database/migrations/20260819_plan_weeks.sql \\")
print("        coach/mcp_server.py coach/api.py \\")
print("        files/cairn_app_v6.html")
print("  git commit -m 'feat: plan_weeks, race fields, update_planned_workout'")
print("  git push")
