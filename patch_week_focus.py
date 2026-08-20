#!/usr/bin/env python3
"""
Patch: upsert_training_block akzeptiert optionales plan.weeks Array
mit week_number, week_focus, phase, is_deload Override.

Run from repo root:  python patch_week_focus.py
"""

PATH = "coach/mcp_server.py"
with open(PATH, "r") as f:
    src = f.read()

OLD = '''        week_groups = _collections.defaultdict(list)
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

        with conn.cursor() as cur:
            pw_id_map: dict = {}
            prev_km = 0.0
            for wnum, wstart in enumerate(sorted_weeks, start=1):
                slist = week_groups[wstart]
                wkm = _week_km(slist)
                phases_in_week = [s.get("phase") or s.get("session_zone") for s in slist if s.get("phase") or s.get("session_zone")]
                phase_val = phases_in_week[0] if phases_in_week else "base"
                is_deload = (prev_km > 0 and wkm > 0 and wkm / prev_km < 0.75)
                if wkm > 0:
                    prev_km = wkm
                cur.execute(
                    """INSERT INTO plan_weeks (plan_id, week_number, week_start, phase, is_deload, target_run_km)
                       VALUES (%s, %s, %s, %s, %s, %s)
                       ON CONFLICT (plan_id, week_number) DO UPDATE SET
                           week_start=EXCLUDED.week_start,
                           phase=EXCLUDED.phase,
                           is_deload=EXCLUDED.is_deload, is_peak=EXCLUDED.is_peak,
                           target_run_km=EXCLUDED.target_run_km,
                           updated_at=now()
                       RETURNING id""",
                    (plan_id, wnum, wstart, phase_val, is_deload, round(wkm, 1) if wkm else None),
                )'''

NEW = '''        week_groups = _collections.defaultdict(list)
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
                )'''

assert OLD in src, "Patch-Ziel nicht gefunden — mcp_server.py Version prüfen"
src = src.replace(OLD, NEW, 1)

with open(PATH, "w") as f:
    f.write(src)

print("✓ coach/mcp_server.py gepatcht (week_focus + is_peak in plan_weeks)")
