"""
CAIRN – Alten training_plan löschen
Echte Aktivitäten (trainings Tabelle) bleiben erhalten.
Ausführen: python data/clear_plan.py
"""
import os, psycopg2
from dotenv import load_dotenv
load_dotenv()

conn = psycopg2.connect(os.getenv("RAILWAY_DATABASE_URL") or os.getenv("DATABASE_URL"))
cur = conn.cursor()

cur.execute("SELECT COUNT(*) FROM training_plan")
count = cur.fetchone()[0]
print(f"Gefundene Plan-Einträge: {count}")

cur.execute("DELETE FROM training_plan")
cur.execute("DELETE FROM plans WHERE status = 'active'")
conn.commit()

cur.execute("SELECT COUNT(*) FROM training_plan")
remaining = cur.fetchone()[0]
print(f"Verbleibende Einträge: {remaining}")

cur.execute("SELECT COUNT(*) FROM trainings")
activities = cur.fetchone()[0]
print(f"Aktivitäten (unberührt): {activities}")

conn.close()
print("Fertig – du kannst jetzt einen neuen Plan erstellen.")