#!/usr/bin/env python3
"""
Patch 1: api.py — plan_weeks km-Fallback auf Session-Summe wenn target_run_km NULL
Patch 2: cairn_app_v6.html — Revert padding-top CSS-Fehler, fix load-value Positionierung

Run from repo root: python patch_plan_api_km.py
"""

# ─── PATCH 1: coach/api.py — km aus Sessions wenn target_run_km fehlt ─────────
PATH_API = "coach/api.py"
with open(PATH_API, "r") as f:
    src = f.read()

OLD_SQL = '''        cur.execute("""
            SELECT week_number, week_start, phase, is_deload, is_peak, target_run_km, week_focus
            FROM plan_weeks WHERE plan_id = %s ORDER BY week_number
        """, (plan_id,))'''

NEW_SQL = '''        cur.execute("""
            SELECT pw.week_number, pw.week_start, pw.phase, pw.is_deload, pw.is_peak,
                   COALESCE(
                       NULLIF(pw.target_run_km, 0),
                       (SELECT ROUND(COALESCE(SUM(tp.distance_km * COALESCE(tp.km_factor, 1)), 0)::numeric, 1)
                        FROM training_plan tp
                        WHERE tp.plan_id = pw.plan_id
                          AND tp.week_date = pw.week_start
                          AND tp.session_type NOT IN ('Rest Day','Strength Training','Core','Mobility')
                          AND tp.status != 'archived')
                   ) AS km,
                   pw.week_focus
            FROM plan_weeks pw
            WHERE pw.plan_id = %s
            ORDER BY pw.week_number
        """, (plan_id,))'''

assert OLD_SQL in src, "PATCH 1 (plan_weeks SQL) nicht gefunden"
src = src.replace(OLD_SQL, NEW_SQL, 1)

with open(PATH_API, "w") as f:
    f.write(src)
print(f"✓ {PATH_API} gepatcht — km aus Sessions wenn target_run_km leer")


# ─── PATCH 2: cairn_app_v6.html — CSS fixes ───────────────────────────────────
PATH_HTML = "files/cairn_app_v6.html"
with open(PATH_HTML, "r", encoding="utf-8") as f:
    html = f.read()

# 2a: load-chart — padding-top revertieren (war falsch), overflow: visible ergänzen
OLD_CSS_CHART = "#cairn-plan-soft .load-chart { display: grid; grid-template-columns: repeat(16,minmax(0,1fr)); align-items: end; gap: 5px; height: 155px; padding-top: 1.4rem; box-sizing: border-box; }"
NEW_CSS_CHART  = "#cairn-plan-soft .load-chart { display: grid; grid-template-columns: repeat(16,minmax(0,1fr)); align-items: end; gap: 5px; height: 155px; overflow: visible; }"

# Falls der Patch noch nicht angewendet wurde (Original-CSS):
OLD_CSS_CHART_ORIG = "#cairn-plan-soft .load-chart { display: grid; grid-template-columns: repeat(16,minmax(0,1fr)); align-items: end; gap: 5px; height: 155px; }"
NEW_CSS_CHART_CLEAN = "#cairn-plan-soft .load-chart { display: grid; grid-template-columns: repeat(16,minmax(0,1fr)); align-items: end; gap: 5px; height: 155px; overflow: visible; }"

if OLD_CSS_CHART in html:
    html = html.replace(OLD_CSS_CHART, NEW_CSS_CHART, 1)
    print("✓ load-chart CSS: padding-top revertiert + overflow: visible")
elif OLD_CSS_CHART_ORIG in html:
    html = html.replace(OLD_CSS_CHART_ORIG, NEW_CSS_CHART_CLEAN, 1)
    print("✓ load-chart CSS: overflow: visible ergänzt (Original)")
else:
    print("⚠ load-chart CSS-Zeile nicht gefunden — überprüfen")

# 2b: mobile load-chart — ebenfalls revertieren
OLD_MOBILE = "  #cairn-plan-soft .load-chart { height: 132px; gap: 3px; padding-top: 1.2rem; box-sizing: border-box; }"
NEW_MOBILE  = "  #cairn-plan-soft .load-chart { height: 132px; gap: 3px; }"
OLD_MOBILE_ORIG = "  #cairn-plan-soft .load-chart { height: 132px; gap: 3px; }"

if OLD_MOBILE in html:
    html = html.replace(OLD_MOBILE, NEW_MOBILE, 1)
    print("✓ Mobile load-chart CSS revertiert")
else:
    print("  Mobile load-chart CSS: keine Änderung nötig")

# 2c: load-value — inside bar statt above (verhindert overflow aus dem Chart)
OLD_LOAD_VALUE = "#cairn-plan-soft .load-value { position: absolute; left: 50%; top: -.1rem; transform: translate(-50%,-100%); white-space: nowrap; }"
NEW_LOAD_VALUE = "#cairn-plan-soft .load-value { position: absolute; left: 50%; bottom: calc(100% + .15rem); transform: translateX(-50%); white-space: nowrap; pointer-events: none; }"

if OLD_LOAD_VALUE in html:
    html = html.replace(OLD_LOAD_VALUE, NEW_LOAD_VALUE, 1)
    print("✓ load-value: bottom-positioning statt top (kein overflow)")
else:
    print("⚠ load-value CSS nicht gefunden")

# 2d: load-block — overflow: visible damit Labels sichtbar bleiben
OLD_LOAD_BLOCK = "#cairn-plan-soft .load-block { margin: 1rem 0 .35rem; padding: .85rem 0 .75rem; border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }"
NEW_LOAD_BLOCK = "#cairn-plan-soft .load-block { margin: 1rem 0 .35rem; padding: .85rem 0 1.8rem; border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); overflow: visible; }"

if OLD_LOAD_BLOCK in html:
    html = html.replace(OLD_LOAD_BLOCK, NEW_LOAD_BLOCK, 1)
    print("✓ load-block: padding-bottom erhöht für km-Labels")
else:
    print("⚠ load-block CSS nicht gefunden")

with open(PATH_HTML, "w", encoding="utf-8") as f:
    f.write(html)
print(f"✓ {PATH_HTML} gepatcht")

print()
print("Nächster Schritt:")
print("  git add coach/api.py files/cairn_app_v6.html")
print("  git commit -m 'fix: plan chart km from sessions + load-value positioning'")
print("  git push")
