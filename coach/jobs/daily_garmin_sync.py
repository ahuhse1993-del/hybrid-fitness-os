"""
CAIRN Daily Garmin Sync Job
Läuft täglich morgens via Railway Cron.
Pusht alle Sessions der nächsten 14 Tage die noch nicht auf Garmin sind.
"""
import logging, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("cairn.daily_sync")

def main():
    from coach.garmin_batch import run_batch
    logger.info("=== CAIRN Daily Garmin Sync startet ===")
    result = run_batch(session_ids=None)
    if "error" in result:
        logger.error("Job fehlgeschlagen: %s", result["error"])
        sys.exit(1)
    logger.info("Ergebnis: created=%s updated=%s moved=%s unchanged=%s failed=%s",
                len(result.get("created",[])),
                len(result.get("updated",[])),
                len(result.get("moved",[])),
                len(result.get("unchanged",[])),
                len(result.get("failed",[])))
    if result.get("failed"):
        logger.warning("Fehlgeschlagen: %s", result["failed"])

if __name__ == "__main__":
    main()
