#!/usr/bin/env python3
"""
Patch: Rennrad-Typen + Long Trail Run + Hill Sprints in session_routing.py
        Cycling/Rennrad → cross_training Icon in cairn_app_v6.html

Run from repo root: python patch_routing_and_frontend.py
"""
import sys

# ─── PATCH 1: session_routing.py ─────────────────────────────────────────────
PATH_ROUTING = "coach/session_routing.py"
with open(PATH_ROUTING, "r") as f:
    src = f.read()

# Add Long Trail Run, Hill Sprints, Tempo Run, Time Trial to GARMIN_RUNNING_TYPES
OLD_RUNNING = '''GARMIN_RUNNING_TYPES = frozenset({
    # Basis-Typen
    "Easy Run", "Trail Run", "Recovery Run", "Long Run",
    "Tempo Session", "Interval Session", "Sprint Session", "Hill Session",
    "Race Day", "Race",
    # Erweiterte Typen (ChatGPT-kompatibel)
    "Interval Run", "Interval Training",
    "Threshold Run", "Trail Threshold", "Uphill Threshold",
    "Hill Technique", "Hill Run",
    "Race Activation", "Activation Run",
    "Fartlek", "Strides",
})'''

NEW_RUNNING = '''GARMIN_RUNNING_TYPES = frozenset({
    # Basis-Typen
    "Easy Run", "Trail Run", "Long Trail Run", "Recovery Run", "Long Run",
    "Tempo Session", "Tempo Run", "Interval Session", "Sprint Session", "Hill Session",
    "Race Day", "Race",
    # Erweiterte Typen (ChatGPT-kompatibel)
    "Interval Run", "Interval Training",
    "Threshold Run", "Trail Threshold", "Uphill Threshold",
    "Hill Technique", "Hill Run", "Hill Sprints",
    "Race Activation", "Activation Run",
    "Fartlek", "Strides", "Time Trial",
})'''

assert OLD_RUNNING in src, "PATCH 1 (GARMIN_RUNNING_TYPES) nicht gefunden"
src = src.replace(OLD_RUNNING, NEW_RUNNING, 1)

# Add Rennrad to GARMIN_CYCLING_TYPES
OLD_CYCLING = 'GARMIN_CYCLING_TYPES = frozenset({"Cycling", "Bike", "Road Bike", "MTB", "E-Bike"})'
NEW_CYCLING = 'GARMIN_CYCLING_TYPES = frozenset({"Cycling", "Bike", "Road Bike", "MTB", "E-Bike", "Rennrad", "Rennrad Endurance"})'

assert OLD_CYCLING in src, "PATCH 2 (GARMIN_CYCLING_TYPES) nicht gefunden"
src = src.replace(OLD_CYCLING, NEW_CYCLING, 1)

with open(PATH_ROUTING, "w") as f:
    f.write(src)
print(f"✓ {PATH_ROUTING} gepatcht")


# ─── PATCH 2: cairn_app_v6.html SESSION_ASSET ────────────────────────────────
PATH_HTML = "files/cairn_app_v6.html"
with open(PATH_HTML, "r", encoding="utf-8") as f:
    html = f.read()

OLD_ASSET = "  'Cross Training': 'cross_training',"
NEW_ASSET = (
    "  'Cycling': 'cross_training',\n"
    "  'Rennrad': 'cross_training',\n"
    "  'Rennrad Endurance': 'cross_training',\n"
    "  'Road Bike': 'cross_training',\n"
    "  'Bike': 'cross_training',\n"
    "  'MTB': 'cross_training',\n"
    "  'E-Bike': 'cross_training',\n"
    "  'Cross Training': 'cross_training',"
)

assert OLD_ASSET in html, "PATCH 3 (SESSION_ASSET Cross Training) nicht gefunden — ist 'Cross Training' schon im Asset-Map?"
html = html.replace(OLD_ASSET, NEW_ASSET, 1)

with open(PATH_HTML, "w", encoding="utf-8") as f:
    f.write(html)
print(f"✓ {PATH_HTML} gepatcht")

print()
print("Nächster Schritt:")
print("  git add coach/session_routing.py files/cairn_app_v6.html")
print("  git commit -m 'fix: Rennrad routing + cross_training icon + Long Trail Run + missing running types'")
print("  git push")
