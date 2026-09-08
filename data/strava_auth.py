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

db_url = os.getenv("RAILWAY_DATABASE_URL") or os.getenv("DATABASE_URL")
if db_url and tokens.get("access_token") and tokens.get("refresh_token"):
    import psycopg2
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO strava_tokens (id, access_token, refresh_token, expires_at, athlete_id)
        VALUES (1, %s, %s, %s, %s)
        ON CONFLICT (id) DO UPDATE SET
            access_token=EXCLUDED.access_token,
            refresh_token=EXCLUDED.refresh_token,
            expires_at=EXCLUDED.expires_at,
            athlete_id=EXCLUDED.athlete_id,
            updated_at=NOW()
    """, [tokens["access_token"], tokens["refresh_token"],
          tokens["expires_at"], tokens.get("athlete", {}).get("id")])
    conn.commit()
    conn.close()
    print("✅ Refresh Token dauerhaft in CAIRN DB gespeichert")
elif not db_url:
    print("⚠️ RAILWAY_DATABASE_URL/DATABASE_URL nicht gesetzt — Token nur oben ausgegeben, nicht gespeichert.")