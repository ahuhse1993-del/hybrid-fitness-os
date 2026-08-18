"""
CAIRN Garmin Activity Stream Import
=====================================
Holt den sekundengenauen Aktivitäts-Stream von Garmin (get_activity_details)
und schreibt ihn in die activity_stream Tabelle.

Jeder Punkt enthält: distance_m, elapsed_s, heart_rate, speed_ms,
cadence, power, elevation_m, lat, lon — alle auf derselben Distanz-Achse.

Wird aufgerufen aus:
  - coach/jobs/sync_completed_activities.py (nach splits-Import)
  - Manuell: python data/garmin_import_stream.py <training_id> <garmin_id>
"""

import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger(__name__)

# ── Metric-Key-Mapping ────────────────────────────────────────────────────────
# Garmin liefert metricDescriptors mit diesen keys — wir mappen auf unser Schema.
METRIC_MAP = {
    "directLatitude":        "lat",
    "directLongitude":       "lon",
    "directAltitude":        "elevation_m",
    "directElevation":       "elevation_m",   # von Garmin je Sportart directAltitude ODER directElevation
    "directHeartRate":       "heart_rate",
    "directSpeed":           "speed_ms",
    "directRunCadence":      "cadence",       # bereits in spm — Vorrang vor der Double-Cadence-Heuristik
    "directDoubleCadence":   "cadence_raw",   # RPM×2 bei Laufen → ÷2 → spm (Fallback wenn directRunCadence fehlt)
    "directCadence":         "cadence_raw",   # Rad: echte RPM
    "directBikeCadence":     "cadence_raw",
    "directPower":           "power",
    "directRunningPower":    "power",
    "sumDistance":           "distance_m",
    "sumElapsedDuration":    "elapsed_s",
    "sumMovingDuration":     "moving_s",
}


def _parse_stream(details: dict) -> list[dict]:
    """
    Parst die Garmin get_activity_details-Antwort in eine Liste von Dicts.
    Gibt nur Punkte zurück, die eine gültige Distanz (>= 0) haben.
    """
    descriptors = details.get("metricDescriptors", [])
    raw_metrics  = details.get("activityDetailMetrics", [])

    if not descriptors or not raw_metrics:
        return []

    # Index → unser Feldname
    idx_map = {}
    for d in descriptors:
        key = d.get("key", "")
        if key in METRIC_MAP:
            idx_map[d["metricsIndex"]] = METRIC_MAP[key]

    points = []
    for entry in raw_metrics:
        values = entry.get("metrics", [])
        row = {}
        for idx, field in idx_map.items():
            if idx < len(values):
                row[field] = values[idx]

        # distance_m ist Pflicht
        dist = row.get("distance_m")
        if dist is None or dist < 0:
            continue

        # Kadenz normalisieren: directRunCadence liefert bereits spm direkt und
        # hat Vorrang (kein Raten noetig). Nur wenn das fehlt (z.B. Rad-Aktivitaeten
        # ohne directRunCadence-Descriptor) auf die Double-Cadence-Heuristik
        # zurueckfallen: directDoubleCadence = Schritte/min × 2 bei Laufen,
        # echte RPM bei Rad.
        cadence_raw = row.pop("cadence_raw", None)
        if row.get("cadence") is None and cadence_raw is not None:
            row["cadence"] = round(cadence_raw / 2) if cadence_raw > 80 else round(cadence_raw)

        # Typen sichern
        if row.get("heart_rate") is not None:
            row["heart_rate"] = int(round(row["heart_rate"]))
        if row.get("cadence") is not None:
            row["cadence"] = int(row["cadence"])
        if row.get("power") is not None:
            row["power"] = int(round(row["power"]))

        row["distance_m"] = round(float(dist), 1)
        if row.get("elapsed_s") is not None:
            row["elapsed_s"] = round(float(row["elapsed_s"]), 1)
        if row.get("moving_s") is not None:
            row["moving_s"] = round(float(row["moving_s"]), 1)
        if row.get("elevation_m") is not None:
            row["elevation_m"] = round(float(row["elevation_m"]), 1)
        if row.get("speed_ms") is not None:
            row["speed_ms"] = round(float(row["speed_ms"]), 3)
        if row.get("lat") is not None:
            row["lat"] = round(float(row["lat"]), 7)
        if row.get("lon") is not None:
            row["lon"] = round(float(row["lon"]), 7)

        points.append(row)

    return points


def import_stream_for_activity(client, training_id: int, garmin_id: int,
                                conn=None, force: bool = False) -> dict:
    """
    Haupt-Import-Funktion.

    Args:
        client:      garminconnect-Client (mit aktivem Token)
        training_id: CAIRN trainings.id
        garmin_id:   Garmin activityId (int)
        conn:        Optionale DB-Verbindung (wird nicht geschlossen!)
                     Wenn None → eigene Verbindung öffnen + schließen.
        force:       Bestehende Punkte löschen und neu importieren.

    Returns:
        {"imported": N} oder {"skipped": "already_exists"} oder {"error": "..."}
    """
    from database.connection import get_connection

    own_conn = conn is None
    if own_conn:
        conn = get_connection()

    cur = conn.cursor()
    try:
        # Duplikat-Check
        cur.execute(
            "SELECT COUNT(*) FROM activity_stream WHERE training_id = %s",
            (training_id,)
        )
        existing = cur.fetchone()[0]

        if existing > 0 and not force:
            logger.debug("Stream bereits vorhanden für training_id=%s (%s Punkte)", training_id, existing)
            return {"skipped": "already_exists", "existing_points": existing}

        if existing > 0 and force:
            cur.execute("DELETE FROM activity_stream WHERE training_id = %s", (training_id,))
            logger.info("Alte Stream-Daten gelöscht für training_id=%s", training_id)

        # Garmin API — maxchart: bis zu 3000 Punkte fuer lange Aktivitaeten (~109min
        # @ Grellingen-Aktivitaet brauchten 1822 Punkte). Bei laengeren Aktivitaeten
        # liefert Garmin automatisch einen ausgeduennten Stream.
        # KORREKTUR: die Bibliothek (garminconnect==0.3.6) akzeptiert "maxchart",
        # nicht "max_metrics_count" — der urspruengliche Parametername existiert
        # nicht und liess jeden Aufruf mit TypeError scheitern (verifiziert gegen
        # inspect.signature(Garmin.get_activity_details): (self, activity_id,
        # maxchart=2000, maxpoly=4000)).
        details = client.get_activity_details(garmin_id, maxchart=3000)
        points = _parse_stream(details)

        if not points:
            logger.warning("Kein Stream verfügbar für Garmin ID %s", garmin_id)
            return {"error": "no_stream_data"}

        # Bulk-Insert
        rows = []
        for p in points:
            rows.append((
                training_id,
                p["distance_m"],
                p.get("elapsed_s"),
                p.get("moving_s"),
                p.get("heart_rate"),
                p.get("speed_ms"),
                p.get("cadence"),
                p.get("power"),
                p.get("elevation_m"),
                p.get("lat"),
                p.get("lon"),
            ))

        cur.executemany("""
            INSERT INTO activity_stream
                (training_id, distance_m, elapsed_s, moving_s, heart_rate,
                 speed_ms, cadence, power, elevation_m, lat, lon)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, rows)

        if own_conn:
            conn.commit()

        logger.info("Stream importiert: training_id=%s | %s Punkte", training_id, len(rows))
        return {"imported": len(rows)}

    except Exception as exc:
        if own_conn:
            conn.rollback()
        logger.error("Stream-Import Fehler für training_id=%s garmin_id=%s: %s",
                     training_id, garmin_id, exc)
        raise
    finally:
        cur.close()
        if own_conn:
            conn.close()


# ── CLI: Einzelne Aktivität nachimportieren ───────────────────────────────────
if __name__ == "__main__":
    import sys
    from dotenv import load_dotenv
    load_dotenv()
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(message)s")

    if len(sys.argv) < 3:
        print("Usage: python garmin_import_stream.py <training_id> <garmin_id> [--force]")
        sys.exit(1)

    training_id = int(sys.argv[1])
    garmin_id   = int(sys.argv[2])
    force       = "--force" in sys.argv

    from coach.garmin_push import garmin_client
    client = garmin_client()
    result = import_stream_for_activity(client, training_id, garmin_id, force=force)
    print(result)
