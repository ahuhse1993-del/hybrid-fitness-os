import psycopg2
import os
from dotenv import load_dotenv

load_dotenv("/Users/lexshapes/hybrid-fitness-os/.env")

def fix_duplicates():
    conn = psycopg2.connect(os.getenv("RAILWAY_DATABASE_URL"))
    cur = conn.cursor()

    print("=== CAIRN Duplikat-Bereinigung v2 ===\n")
    total_fixed = 0

    # WeightTraining: Hevy > Garmin > Strava
    cur.execute("""
        SELECT date FROM trainings
        WHERE type = 'WeightTraining'
        GROUP BY date HAVING COUNT(*) > 1
    """)
    dates = cur.fetchall()
    print(f"WeightTraining Duplikate: {len(dates)}")

    for (date,) in dates:
        cur.execute("""
            SELECT id, duration_minutes, heart_rate_avg, hevy_id, strava_id, garmin_id
            FROM trainings
            WHERE date = %s AND type = 'WeightTraining'
            ORDER BY hevy_id NULLS LAST, garmin_id NULLS LAST
        """, (date,))
        entries = cur.fetchall()
        winner = next((e for e in entries if e[3]), None) or \
                 next((e for e in entries if e[5]), None) or entries[0]
        losers = [e for e in entries if e[0] != winner[0]]

        for loser in losers:
            safe_strava_id = None
            if loser[4]:
                cur.execute("SELECT id FROM trainings WHERE strava_id = %s AND id != %s", (loser[4], winner[0]))
                if not cur.fetchone():
                    safe_strava_id = loser[4]

            cur.execute("""
                UPDATE trainings SET
                    duration_minutes = COALESCE(duration_minutes, %s),
                    heart_rate_avg   = COALESCE(heart_rate_avg, %s),
                    garmin_id        = COALESCE(garmin_id, %s),
                    strava_id        = CASE WHEN strava_id IS NULL AND %s IS NOT NULL THEN %s ELSE strava_id END
                WHERE id = %s
            """, (loser[1], loser[2], loser[5], safe_strava_id, safe_strava_id, winner[0]))
            cur.execute("DELETE FROM splits WHERE training_id = %s", (loser[0],))
            cur.execute("DELETE FROM trainings WHERE id = %s", (loser[0],))
            total_fixed += 1
            print(f"  OK {date} | WeightTraining | ID {loser[0]} -> ID {winner[0]}")

    # Run/Ride/etc: Garmin > Strava
    cur.execute("""
        SELECT date, type FROM trainings
        WHERE type != 'WeightTraining'
        GROUP BY date, type HAVING COUNT(*) > 1
    """)
    groups = cur.fetchall()
    print(f"\nRun/Ride/etc. Duplikate: {len(groups)}")

    for (date, act_type) in groups:
        cur.execute("""
            SELECT id, duration_minutes, heart_rate_avg, distance_km, strava_id, garmin_id
            FROM trainings
            WHERE date = %s AND type = %s
            ORDER BY garmin_id NULLS LAST, strava_id NULLS LAST
        """, (date, act_type))
        entries = cur.fetchall()
        winner = next((e for e in entries if e[5]), None) or entries[0]
        losers = [e for e in entries if e[0] != winner[0]]

        for loser in losers:
            safe_strava_id = None
            if loser[4]:
                cur.execute("SELECT id FROM trainings WHERE strava_id = %s AND id != %s", (loser[4], winner[0]))
                if not cur.fetchone():
                    safe_strava_id = loser[4]

            cur.execute("""
                UPDATE trainings SET
                    duration_minutes = COALESCE(duration_minutes, %s),
                    heart_rate_avg   = COALESCE(heart_rate_avg, %s),
                    distance_km      = COALESCE(distance_km, %s),
                    strava_id        = CASE WHEN strava_id IS NULL AND %s IS NOT NULL THEN %s ELSE strava_id END
                WHERE id = %s
            """, (loser[1], loser[2], loser[3], safe_strava_id, safe_strava_id, winner[0]))
            cur.execute("DELETE FROM splits WHERE training_id = %s", (loser[0],))
            cur.execute("DELETE FROM trainings WHERE id = %s", (loser[0],))
            total_fixed += 1
            print(f"  OK {date} | {act_type} | ID {loser[0]} -> ID {winner[0]}")

    conn.commit()
    conn.close()
    print(f"\n=== FERTIG === {total_fixed} Duplikate bereinigt")

if __name__ == "__main__":
    fix_duplicates()