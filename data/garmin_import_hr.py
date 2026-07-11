"""
CAIRN Garmin Heart Rate Import
Holt sekundengenaue Herzfrequenz-Zeitreihen aus get_activity_details()
und speichert sie in hr_tracks.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from garminconnect import Garmin
from database.connection import get_connection
from dotenv import load_dotenv

load_dotenv()

def get_garmin_client():
    client = Garmin(os.getenv("GARMIN_EMAIL"), os.getenv("GARMIN_PASSWORD"))
    client.login()
    return client

def import_hr_for_activity(client, training_id, garmin_id, force=False):
    """
    Importiert die HR-Zeitreihe für eine einzelne Aktivität.
    """
    conn = get_connection()
    cur = conn.cursor()

    try:
        cur.execute("SELECT COUNT(*) FROM hr_tracks WHERE training_id = %s", (training_id,))
        count = cur.fetchone()[0]

        if count > 0 and not force:
            print(f"  HR-Daten bereits vorhanden für ID {training_id}")
            conn.close()
            return

        if count > 0 and force:
            cur.execute("DELETE FROM hr_tracks WHERE training_id = %s", (training_id,))

        data = client.get_activity_details(garmin_id)

        descriptors = data.get("metricDescriptors", [])
        hr_index = None
        ts_index = None
        for d in descriptors:
            if d.get("key") == "directHeartRate":
                hr_index = d.get("metricsIndex")
            if d.get("key") == "directTimestamp":
                ts_index = d.get("metricsIndex")

        if hr_index is None:
            print(f"  Keine HR-Metrik gefunden für {garmin_id}")
            conn.close()
            return

        metrics = data.get("activityDetailMetrics", [])
        if not metrics:
            print(f"  Keine Detail-Metriken für {garmin_id}")
            conn.close()
            return

        rows = []
        for i, sample in enumerate(metrics):
            values = sample.get("metrics", [])
            if hr_index >= len(values):
                continue
            hr = values[hr_index]
            ts = values[ts_index] if ts_index is not None and ts_index < len(values) else None
            if hr is None:
                continue
            rows.append((training_id, i, int(ts) if ts else None, int(hr)))

        if not rows:
            print(f"  Keine gültigen HR-Werte für {garmin_id}")
            conn.close()
            return

        cur.executemany("""
            INSERT INTO hr_tracks (training_id, point_index, timestamp_ms, heart_rate)
            VALUES (%s, %s, %s, %s)
        """, rows)
        conn.commit()
        print(f"  ✅ {len(rows)} HR-Punkte importiert für Training ID {training_id}")

    except Exception as e:
        conn.rollback()
        print(f"  ❌ HR Import Fehler: {e}")
        raise
    finally:
        conn.close()

if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 3:
        training_id = int(sys.argv[1])
        garmin_id = int(sys.argv[2])
        client = get_garmin_client()
        import_hr_for_activity(client, training_id, garmin_id, force=True)
    else:
        print("Usage: python garmin_import_hr.py <training_id> <garmin_id>")