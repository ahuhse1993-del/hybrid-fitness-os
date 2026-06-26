from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
import os, psycopg2, json
from datetime import date, timedelta
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

def get_db():
    database_url = os.getenv("RAILWAY_DATABASE_URL") or os.getenv("DATABASE_URL")
    return psycopg2.connect(database_url)

@app.route('/')
def home():
    return send_file(os.path.join(os.path.dirname(__file__), '..', 'files', 'cairn_home_v4.html'))

@app.route('/analyse')
def analyse():
    return send_file(os.path.join(os.path.dirname(__file__), '..', 'files', 'cairn_analyse_v4.html'))

@app.route('/api/checkin', methods=['POST'])
def save_checkin():
    data = request.get_json()
    feel = data.get('feel', '')
    notes = ', '.join(data.get('notes', []))
    text = data.get('text', '')
    today = date.today()

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id FROM daily_logs WHERE date = %s", (today,))
    existing = cur.fetchone()

    if existing:
        cur.execute("""
            UPDATE daily_logs SET feel=%s, notes=%s, athlete_text=%s,
            morning_brief=NULL, suggestion=NULL, session_type=NULL, session_zone=NULL,
            primary_target=NULL, secondary_target=NULL
            WHERE date=%s
        """, (feel, notes, text, today))
    else:
        cur.execute("""
            INSERT INTO daily_logs (date, feel, notes, athlete_text)
            VALUES (%s, %s, %s, %s)
        """, (today, feel, notes, text))

    conn.commit()
    conn.close()

    # GitHub Actions Health Sync triggern und warten
    try:
        import urllib.request, urllib.error, time
        github_token = os.getenv("CAIRN_GITHUB_TOKEN")
        if github_token:
            headers = {
                "Authorization": f"Bearer {github_token}",
                "Accept": "application/vnd.github.v3+json",
                "Content-Type": "application/json"
            }

            # Workflow triggern
            req = urllib.request.Request(
                "https://api.github.com/repos/ahuhse1993-del/hybrid-fitness-os/actions/workflows/health_sync.yml/dispatches",
                data=b'{"ref":"main"}',
                headers=headers,
                method="POST"
            )
            urllib.request.urlopen(req, timeout=10)

            # 5 Sekunden warten damit GitHub den Run erstellt
            time.sleep(5)

            # Run ID holen
            req2 = urllib.request.Request(
                "https://api.github.com/repos/ahuhse1993-del/hybrid-fitness-os/actions/workflows/health_sync.yml/runs?per_page=1",
                headers=headers
            )
            resp = urllib.request.urlopen(req2, timeout=10)
            runs_data = json.loads(resp.read())
            run_id = runs_data["workflow_runs"][0]["id"]

            # Auf Completion warten (max 90 Sekunden)
            for _ in range(18):
                time.sleep(5)
                req3 = urllib.request.Request(
                    f"https://api.github.com/repos/ahuhse1993-del/hybrid-fitness-os/actions/runs/{run_id}",
                    headers=headers
                )
                resp3 = urllib.request.urlopen(req3, timeout=10)
                run_data = json.loads(resp3.read())
                status = run_data.get("status")
                conclusion = run_data.get("conclusion")
                if status == "completed":
                    break

    except Exception as e:
        pass

    return jsonify({"status": "ok"})

@app.route('/api/morning-brief', methods=['GET'])
def morning_brief():
    try:
        from coach.morning_brief import generate_morning_brief

        today = date.today()
        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            SELECT feel, notes, athlete_text, morning_brief, suggestion,
                   session_type, session_zone, primary_target, secondary_target
            FROM daily_logs WHERE date = %s
        """, (today,))
        row = cur.fetchone()

        athlete_feedback = {}
        if row:
            if row[3] and row[4] is not None:
                conn.close()
                return jsonify({
                    "status": "ok",
                    "brief": row[3],
                    "suggestion": row[4] or "",
                    "session_type": row[5] or "",
                    "session_zone": row[6] or "",
                    "primary_target": row[7] or "none",
                    "secondary_target": row[8] or "none",
                    "replan_needed": bool(row[4])
                })
            athlete_feedback = {
                'feel': row[0] or '',
                'notes': row[1].split(', ') if row[1] else [],
                'text': row[2] or ''
            }

        result = generate_morning_brief(athlete_feedback=athlete_feedback)
        brief = result.get("brief", "")
        suggestion = result.get("suggestion", "")
        session_type = result.get("session_type", "")
        session_zone = result.get("session_zone", "")
        primary_target = result.get("primary_target", "none")
        secondary_target = result.get("secondary_target", "none")
        replan_needed = result.get("replan_needed", False)

        if row:
            cur.execute("""
                UPDATE daily_logs SET morning_brief=%s, suggestion=%s,
                session_type=%s, session_zone=%s, primary_target=%s, secondary_target=%s
                WHERE date=%s
            """, (brief, suggestion, session_type, session_zone, primary_target, secondary_target, today))
        else:
            cur.execute("""
                INSERT INTO daily_logs (date, morning_brief, suggestion, session_type, session_zone, primary_target, secondary_target)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (today, brief, suggestion, session_type, session_zone, primary_target, secondary_target))

        conn.commit()
        conn.close()

        return jsonify({
            "status": "ok",
            "brief": brief,
            "suggestion": suggestion,
            "session_type": session_type,
            "session_zone": session_zone,
            "primary_target": primary_target,
            "secondary_target": secondary_target,
            "replan_needed": replan_needed
        })

    except Exception as e:
        import traceback
        return jsonify({"status": "error", "message": str(e), "trace": traceback.format_exc()}), 500

@app.route('/api/dashboard', methods=['GET'])
def dashboard():
    try:
        conn = get_db()
        cur = conn.cursor()
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        sunday = monday + timedelta(days=6)

        # Echte Aktivitäten diese Woche
        cur.execute("""
            SELECT COALESCE(SUM(distance_km), 0), COUNT(*)
            FROM trainings
            WHERE date >= %s AND date <= %s
            AND type NOT IN ('WeightTraining', 'Strength')
        """, (monday, sunday))
        row = cur.fetchone()
        week_km = round(float(row[0]), 1) if row[0] else 0
        week_sessions_done = int(row[1]) if row[1] else 0

        # Geplante Einheiten diese Woche
        cur.execute("""
            SELECT COUNT(*) FROM training_plan
            WHERE week_date = %s AND session_type != 'Rest Day'
        """, (monday,))
        row = cur.fetchone()
        week_sessions_planned = int(row[0]) if row[0] else 0

        # Health Snapshot — heute zuerst, dann gestern
        cur.execute("""
            SELECT hrv_last_night, sleep_duration_h, resting_hr,
                   body_battery_charged, body_battery_drained
            FROM daily_logs
            WHERE date IN (%s, %s)
            ORDER BY date DESC
            LIMIT 1
        """, (today, today - timedelta(days=1)))
        health = cur.fetchone()

        hrv = health[0] if health and health[0] else None
        sleep = round(float(health[1]), 1) if health and health[1] else None
        rhr = health[2] if health and health[2] else None
        bb_charged = health[3] if health and health[3] else None

        conn.close()

        return jsonify({
            "status": "ok",
            "week": {
                "km_done": week_km,
                "sessions_done": week_sessions_done,
                "sessions_planned": week_sessions_planned
            },
            "health": {
                "hrv": hrv,
                "sleep_h": sleep,
                "rhr": rhr,
                "body_battery": bb_charged
            }
        })

    except Exception as e:
        import traceback
        return jsonify({"status": "error", "message": str(e), "trace": traceback.format_exc()}), 500

@app.route('/api/plan', methods=['GET'])
def get_plan():
    try:
        conn = get_db()
        cur = conn.cursor()

        today = date.today()
        monday = today - timedelta(days=today.weekday())

        # Trainingsplan (4 Wochen)
        cur.execute("""
            SELECT id, week_date, day_of_week, session_type, session_zone,
                   duration_min, distance_km, notes
            FROM training_plan
            WHERE week_date >= %s AND week_date < %s
            ORDER BY week_date, day_of_week
        """, (monday, monday + timedelta(weeks=4)))
        plan_rows = cur.fetchall()

        # Echte Aktivitäten dieser + letzter Woche
        cur.execute("""
            SELECT date, type, notes, duration_minutes, distance_km, heart_rate_avg, id
            FROM trainings
            WHERE date >= %s AND date <= %s
            ORDER BY date
        """, (monday, today))
        actual_rows = cur.fetchall()
        conn.close()

        # Typ-Matching: welche Garmin-Typen passen zu welchem Plan-Typ
        type_match = {
            'Easy Run': ['Run'],
            'Recovery Run': ['Run'],
            'Long Run': ['Run', 'TrailRun'],
            'Trail Run': ['TrailRun', 'Run'],
            'Intervalle': ['Run'],
            'Tempo Run': ['Run'],
            'Threshold Run': ['Run'],
            'Progression Run': ['Run'],
            'Race Pace Run': ['Run'],
            'Hill Repeats': ['Run', 'TrailRun'],
            'Strides': ['Run'],
            'Krafttraining': ['WeightTraining'],
            'Mobilität': ['WeightTraining'],
        }

        # Echte Aktivitäten nach Datum gruppieren
        actual_by_date = {}
        for r in actual_rows:
            d = str(r[0])
            if d not in actual_by_date:
                actual_by_date[d] = []
            actual_by_date[d].append({
                "type": r[1],
                "name": r[2] or r[1],
                "duration_min": r[3],
                "distance_km": float(r[4]) if r[4] else 0,
                "avg_hr": r[5],
                "training_id": r[6],
            })

        plan = []
        matched_training_ids = set()

        for r in plan_rows:
            week_date = str(r[1])
            day_of_week = r[2]
            session_type = r[3]

            item_date = date.fromisoformat(week_date) + timedelta(days=day_of_week)
            item_date_str = str(item_date)
            is_past = item_date <= today

            item = {
                "id": r[0],
                "week_date": week_date,
                "day_of_week": day_of_week,
                "session_type": session_type,
                "session_zone": r[4],
                "duration_min": r[5],
                "distance_km": float(r[6]) if r[6] else 0,
                "notes": r[7] or "",
                "is_done": False,
                "is_mismatch": False,
                "actual_type": None,
                "actual_name": None,
                "actual_km": 0,
                "actual_min": 0,
                "actual_hr": None,
                "training_id": None
            }

            if is_past and item_date_str in actual_by_date:
                expected_types = type_match.get(session_type, [])
                for actual in actual_by_date[item_date_str]:
                    if actual["training_id"] not in matched_training_ids:
                        matched = actual["type"] in expected_types
                        item["is_done"] = True
                        item["is_mismatch"] = not matched
                        item["actual_type"] = actual["type"]
                        item["actual_name"] = actual["name"]
                        item["actual_km"] = actual["distance_km"]
                        item["actual_min"] = actual["duration_min"]
                        item["actual_hr"] = actual["avg_hr"]
                        item["training_id"] = actual["training_id"]
                        matched_training_ids.add(actual["training_id"])
                        break

            plan.append(item)

        # Spontane Aktivitäten die keinem Plan entsprechen
        for d_str, activities in actual_by_date.items():
            for actual in activities:
                if actual["training_id"] not in matched_training_ids:
                    # Datum zurück in week_date + day_of_week umrechnen
                    act_date = date.fromisoformat(d_str)
                    act_monday = act_date - timedelta(days=act_date.weekday())
                    # Wochentag relativ zum Plan-Start (Sonntag = 0)
                    week_start = act_monday - timedelta(days=1)
                    day_offset = (act_date - week_start).days - 1

                    plan.append({
                        "id": -actual["training_id"],
                        "week_date": str(act_monday - timedelta(days=1)),
                        "day_of_week": day_offset,
                        "session_type": actual["type"],
                        "session_zone": "",
                        "duration_min": actual["duration_min"],
                        "distance_km": actual["distance_km"],
                        "notes": actual["name"],
                        "is_done": True,
                        "is_mismatch": False,
                        "is_spontaneous": True,
                        "actual_type": actual["type"],
                        "actual_name": actual["name"],
                        "actual_km": actual["distance_km"],
                        "actual_min": actual["duration_min"],
                        "actual_hr": actual["avg_hr"],
                        "training_id": actual["training_id"]
                    })
                    matched_training_ids.add(actual["training_id"])

        return jsonify({"status": "ok", "plan": plan})

    except Exception as e:
        import traceback
        return jsonify({"status": "error", "message": str(e), "trace": traceback.format_exc()}), 500

@app.route('/api/plan/update', methods=['POST'])
def update_plan():
    try:
        data = request.get_json()
        changes = data.get('changes', [])

        conn = get_db()
        cur = conn.cursor()

        for change in changes:
            cur.execute("""
                UPDATE training_plan
                SET week_date=%s, day_of_week=%s, updated_at=NOW()
                WHERE id=%s
            """, (change['week_date'], change['day_of_week'], change['id']))

        conn.commit()
        conn.close()

        return jsonify({"status": "ok"})

    except Exception as e:
        import traceback
        return jsonify({"status": "error", "message": str(e), "trace": traceback.format_exc()}), 500

@app.route('/api/plan/check', methods=['POST'])
def check_plan():
    try:
        import anthropic

        data = request.get_json(force=True)
        week_plan = data.get('week_plan', [])

        lines = []
        for day in week_plan:
            lines.append(day.get('day', '') + ': ' + day.get('session_type', '') + ' | ' + day.get('notes', ''))

        prompt = 'Du bist CAIRN Coach. Pruefe diese Wochenstruktur.\n\n'
        prompt += 'Wochenplan:\n' + '\n'.join(lines) + '\n\n'
        prompt += 'REGELN:\n'
        prompt += '1. Nach Unterkörper-Krafttraining (notes enthaelt Unterkörper oder Lower Body) darf NICHT direkt eine Qualitaetssession folgen (Intervalle, Tempo Run, Threshold Run, Hill Repeats, Race Pace Run). Mindestens ein Ruhetag oder Easy Run dazwischen.\n'
        prompt += '2. Nicht mehr als 2 harte Sessions hintereinander.\n'
        prompt += '3. Nach Long Run kein hartes Training direkt danach.\n\n'
        prompt += 'Sprich wie ein erfahrener Bergfuehrer. Ruhig, direkt, menschlich. Nie: Regelverstoß, Session, Parameter.\n'
        prompt += 'Max 2 Saetze. Beispiel ok=false: "Nach einem Beintraining brauchen die Muskeln Zeit. Ich wuerde die Intervalle einen Tag verschieben."\n\n'
        prompt += 'Antworte NUR mit JSON, kein Markdown:\n'
        prompt += '{"ok": true, "message": "Kurzes Feedback auf Deutsch"}'

        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}]
        )

        raw = message.content[0].text.strip()
        raw = raw.replace('```json', '').replace('```', '').strip()

        try:
            result = json.loads(raw)
        except Exception:
            result = {"ok": True, "message": "Plan sieht gut aus."}

        return jsonify({"status": "ok", "check": result})

    except Exception as e:
        import traceback
        return jsonify({"status": "error", "message": str(e), "trace": traceback.format_exc()}), 500

@app.route('/api/sync', methods=['POST'])
def trigger_sync():
    try:
        import urllib.request
        github_token = os.getenv("CAIRN_GITHUB_TOKEN")
        if github_token:
            req = urllib.request.Request(
                "https://api.github.com/repos/ahuhse1993-del/hybrid-fitness-os/actions/workflows/garmin_sync.yml/dispatches",
                data=b'{"ref":"main"}',
                headers={
                    "Authorization": f"Bearer {github_token}",
                    "Accept": "application/vnd.github.v3+json",
                    "Content-Type": "application/json"
                },
                method="POST"
            )
            urllib.request.urlopen(req, timeout=5)
            return jsonify({"status": "ok", "message": "Sync gestartet"})
        return jsonify({"status": "ok", "message": "Kein Token"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({"status": "ok", "date": str(date.today())})

if __name__ == '__main__':
    port = int(os.getenv("PORT", 5002))
    app.run(debug=False, host='0.0.0.0', port=port)