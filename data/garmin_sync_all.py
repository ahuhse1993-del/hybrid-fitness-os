"""
CAIRN Garmin Full Sync
health: Schlaf, HRV, Body Battery (morgens)
activities: Neue Trainings + Splits (alle 2h)
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

sync_type = os.getenv('SYNC_TYPE', 'activities')
print(f"=== CAIRN Garmin Sync ({sync_type}) ===\n")

if sync_type == 'health':
    print("--- Health Sync ---")
    from data.data_service import sync_garmin_health
    from datetime import date, timedelta
    sync_garmin_health((date.today() - timedelta(days=1)).isoformat())
    sync_garmin_health(date.today().isoformat())
    print("✅ Health sync abgeschlossen")
else:
    print("--- Aktivitäten Sync ---")
    from data.garmin_import_history import import_all_activities
    import_all_activities()

    print("\n--- Splits Sync ---")
    from data.garmin_import_splits import import_splits
    import_splits()

    print("\n--- Kalender Sync ---")
    from data.garmin_calendar_sync import sync_garmin_calendar
    sync_garmin_calendar()

print("\n=== SYNC ABGESCHLOSSEN ===")