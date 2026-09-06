"""
Einmaliges Update: HR-Zonen-Labels (Running) + Rad-HR-Zonen + Power-Zonen-Platzhalter.

hr_zones (Running) ist bereits mit denselben min/max-Werten befuellt --
hier nur Labels ergaenzt (additiv, bricht den z1..z5/min/max-Validator in
coach/mcp_server.py nicht, da der nur min/max prueft und Zusatzfelder toleriert).
hr_zones_cycling und power_zones sind neue Spalten (siehe
database/migrations/20260827_athlete_hr_zones.sql) und werden hier erstmalig befuellt.
"""
import json
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.connection import get_connection

HR_ZONES_RUNNING = {
    "z1": {"min": 100, "max": 125, "label": "Easy"},
    "z2": {"min": 126, "max": 143, "label": "Aerobic"},
    "z3": {"min": 144, "max": 168, "label": "Tempo"},
    "z4": {"min": 169, "max": 175, "label": "Schwelle"},
    "z5": {"min": 176, "max": 183, "label": "VO2max"},
}

HR_ZONES_CYCLING = {
    "Z1": {"min": 0, "max": 131, "label": "Easy"},
    "Z2": {"min": 132, "max": 145, "label": "Aerobic"},
    "Z3": {"min": 146, "max": 152, "label": "Tempo"},
    "Z4": {"min": 153, "max": 162, "label": "Schwelle"},
    "Z5A": {"min": 163, "max": 166, "label": "VO2max low"},
    "Z5B": {"min": 167, "max": 173, "label": "VO2max mid"},
    "Z5C": {"min": 174, "max": 999, "label": "VO2max high"},
}

POWER_ZONES = {"cycling": {}}

conn = get_connection()
try:
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE athlete_profile SET
                   hr_zones = %s,
                   hr_zones_cycling = %s,
                   power_zones = %s
               WHERE id = (SELECT id FROM athlete_profile ORDER BY id DESC LIMIT 1)""",
            (json.dumps(HR_ZONES_RUNNING), json.dumps(HR_ZONES_CYCLING), json.dumps(POWER_ZONES)),
        )
        print(f"Aktualisierte Zeilen: {cur.rowcount}")
    conn.commit()
    print("HR-Zonen (Running+Rad) und Power-Zonen-Platzhalter gespeichert.")
finally:
    conn.close()
