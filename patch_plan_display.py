#!/usr/bin/env python3
"""
Patch: Plan-Seite
  1. weekCardColor: threshold/fartlek/strides/activation ebenfalls coral
  2. load-chart: padding-top damit load-value nicht clippt ("55"-Bug)

Run from repo root: python patch_plan_display.py
"""

PATH = "files/cairn_app_v6.html"
with open(PATH, "r", encoding="utf-8") as f:
    src = f.read()

# ─── PATCH 1: weekCardColor — Regex erweitern ────────────────────────────────
OLD1 = "  if (/interval|hill|tempo|sprint|time trial/i.test(t)) return 'is-coral';"
NEW1 = "  if (/interval|hill|tempo|threshold|fartlek|strides|sprint|time trial|activation/i.test(t)) return 'is-coral';"

assert OLD1 in src, "PATCH 1 (weekCardColor regex) nicht gefunden"
src = src.replace(OLD1, NEW1, 1)

# ─── PATCH 2: load-chart — padding-top für floating labels ───────────────────
OLD2 = "#cairn-plan-soft .load-chart { display: grid; grid-template-columns: repeat(16,minmax(0,1fr)); align-items: end; gap: 5px; height: 155px; }"
NEW2 = "#cairn-plan-soft .load-chart { display: grid; grid-template-columns: repeat(16,minmax(0,1fr)); align-items: end; gap: 5px; height: 155px; padding-top: 1.4rem; box-sizing: border-box; }"

assert OLD2 in src, "PATCH 2 (load-chart padding) nicht gefunden"
src = src.replace(OLD2, NEW2, 1)

# ─── PATCH 3: mobile load-chart — selbe Anpassung ────────────────────────────
OLD3 = "  #cairn-plan-soft .load-chart { height: 132px; gap: 3px; }"
NEW3 = "  #cairn-plan-soft .load-chart { height: 132px; gap: 3px; padding-top: 1.2rem; box-sizing: border-box; }"

assert OLD3 in src, "PATCH 3 (mobile load-chart padding) nicht gefunden"
src = src.replace(OLD3, NEW3, 1)

with open(PATH, "w", encoding="utf-8") as f:
    f.write(src)

print(f"✓ {PATH} gepatcht")
print()
print("Nächster Schritt:")
print("  git add files/cairn_app_v6.html")
print("  git commit -m 'fix: structured session colors + load-chart value label overflow'")
print("  git push")
