import requests
from dotenv import load_dotenv
import os

load_dotenv()

client_id = os.getenv("STRAVA_CLIENT_ID")
client_secret = os.getenv("STRAVA_CLIENT_SECRET")

# Schritt 1: Öffne diese URL im Browser
auth_url = f"https://www.strava.com/oauth/authorize?client_id={client_id}&response_type=code&redirect_uri=http://localhost&approval_prompt=force&scope=read,activity:read_all"

print("Öffne diese URL in deinem Browser:")
print(auth_url)
print()
code = input("Füge den 'code' Parameter aus der URL ein: ")

# Schritt 2: Token holen
response = requests.post("https://www.strava.com/oauth/token", data={
    "client_id": client_id,
    "client_secret": client_secret,
    "code": code,
    "grant_type": "authorization_code"
})

tokens = response.json()
print(f"\nStrava Antwort: {tokens}")
print(f"\n✅ Neuer Access Token: {tokens.get('access_token')}")
print(f"✅ Refresh Token: {tokens.get('refresh_token')}")