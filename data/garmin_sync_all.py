"""
CAIRN Garmin Full Sync
Importiert neue Aktivitäten und Garmin Health Daten.
Läuft via GitHub Actions alle 2 Stunden.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

print("=== CAIRN Garmin Sync ===\n")

# 1. Neue Aktivitäten importieren
print("--- Aktivitäten ---")
from data.garmin_import_history import import_all_activities
import_all_activities()

# 2. Garmin Health Daten syncen
print("\n--- Health ---")
from data.data_service import sync_garmin_health
from datetime import date, timedelta
sync_garmin_health((date.today() - timedelta(days=1)).isoformat())
sync_garmin_health(date.today().isoformat())

print("\n=== SYNC ABGESCHLOSSEN ===")