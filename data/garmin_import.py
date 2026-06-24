"""
CAIRN Garmin History Import
Holt alle Aktivitäten von Garmin und speichert sie in die DB.
Duplikate werden via garmin_id verhindert.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from garminconnect import Garmin
from database.connection import get_connection
from datetime import date
import time
from dotenv import load_dotenv


load_dotenv()

def login_garmin():
    email = os.getenv("GARMIN_EMAIL")
    password = os.getenv("GARMIN_PASSWORD")
    
    client = Garmin(
        email=email,
        password=password,
        prompt_mfa=lambda: input("MFA code: ")
    )
    
    client.login("~/.garminconnect")
    print("✅ Garmin eingeloggt!")
    return client

def get_garmin_sleep(date):
    client = login_garmin()
    sleep = client.get_sleep_data(date)
    return sleep

def get_garmin_hrv(date):
    client = login_garmin()
    hrv = client.get_hrv_data(date)
    return hrv

def get_garmin_body_battery(date):
    client = login_garmin()
    battery = client.get_body_battery(date, date)
    return battery

if __name__ == "__main__":
    client = login_garmin()
    sleep = client.get_sleep_data("2026-06-16")
    print(f"Schlaf: {sleep}")