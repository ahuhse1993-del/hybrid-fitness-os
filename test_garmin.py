from garminconnect import Garmin
import os
from dotenv import load_dotenv

load_dotenv()

email = os.getenv("GARMIN_EMAIL")
password = os.getenv("GARMIN_PASSWORD")

print(f"Email: {email}")

try:
    client = Garmin(email, password)
    client.login()
    print("✅ Login erfolgreich")
    
    # Profil holen
    profile = client.get_user_profile()
    print(f"Name: {profile.get('displayName')}")
    
    # Letzte Aktivität
    activities = client.get_activities(0, 1)
    if activities:
        print(f"Letzte Aktivität: {activities[0].get('activityName')} — {activities[0].get('startTimeLocal')}")
        
except Exception as e:
    print(f"❌ Fehler: {e}")
