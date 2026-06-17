import garth
from dotenv import load_dotenv
import os

load_dotenv()

def login_garmin():
    email = os.getenv("GARMIN_EMAIL")
    password = os.getenv("GARMIN_PASSWORD")
    
    try:
        # Versuche gespeicherten Token zu laden
        garth.resume("~/.garth")
        garth.client.username
        print("✅ Garmin Token geladen!")
    except Exception:
        # Neu einloggen
        print("🔄 Garmin Login...")
        garth.login(email, password)
        garth.save("~/.garth")
        print("✅ Garmin eingeloggt und Token gespeichert!")

def get_sleep_data(date):
    try:
        sleep = garth.connectapi(f"/wellness-service/wellness/dailySleepData/{garth.client.username}?date={date}&nonSleepBufferMinutes=60")
        return sleep
    except Exception as e:
        print(f"❌ Fehler: {e}")
        return None

if __name__ == "__main__":
    login_garmin()
    sleep = get_sleep_data("2026-06-16")
    print(sleep)
    