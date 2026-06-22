# sync_all.py
import sys
import os
sys.path.append('.')

from dotenv import load_dotenv
load_dotenv()

from data.strava_sync import sync_strava_to_db
from data.hevy_sync import sync_hevy_to_db
from utils.fix_hevy_duration import fix_hevy_duration
from utils.fix_duplicates import fix_duplicates

def sync_all():
    print("🚀 HYBRID FITNESS OS – Sync gestartet")
    print("=" * 40)

    # Schritt 1: Strava
    print("\n📡 Schritt 1: Strava sync...")
    try:
        sync_strava_to_db()
    except Exception as e:
        print(f"❌ Strava sync fehlgeschlagen: {e}")

    # Schritt 2: Hevy
    print("\n💪 Schritt 2: Hevy sync...")
    try:
        sync_hevy_to_db()
    except Exception as e:
        print(f"❌ Hevy sync fehlgeschlagen: {e}")

    # Schritt 3: Hevy Dauern korrigieren
    print("\n⏱️  Schritt 3: Hevy Dauern korrigieren...")
    try:
        fix_hevy_duration()
    except Exception as e:
        print(f"❌ Hevy Duration fix fehlgeschlagen: {e}")

    # Schritt 4: Duplikate bereinigen
    print("\n🧹 Schritt 4: Duplikate bereinigen...")
    try:
        fix_duplicates()
    except Exception as e:
        print(f"❌ Duplikat-Bereinigung fehlgeschlagen: {e}")

    print("\n" + "=" * 40)
    print("✅ Sync komplett abgeschlossen!")

if __name__ == "__main__":
    sync_all()