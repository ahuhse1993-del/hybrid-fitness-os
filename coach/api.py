from flask import Flask, jsonify, request, send_file, send_from_directory
from flask_cors import CORS
import os, psycopg2, json, time, traceback
from datetime import date, timedelta, datetime
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

HEVY_CAIRN_FOLDER_ID = 3380361

@app.after_request
def no_cache(response):
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

def get_today():
    try:
        import pytz
        zurich = pytz.timezone('Europe/Zurich')
        return datetime.now(zurich).date()
    except Exception:
        return (datetime.utcnow() + timedelta(hours=2)).date()

def get_db():
    database_url = os.getenv("RAILWAY_DATABASE_URL") or os.getenv("DATABASE_URL")
    return psycopg2.connect(database_url)

@app.route('/')
def home():
    return send_file(os.path.join(os.path.dirname(__file__), '..', 'files', 'cairn_home_v4.html'))

@app.route('/analyse')
def analyse():
    return send_file(os.path.join(os.path.dirname(__file__), '..', 'files', 'cairn_analyse_v4.html'))

@app.route('/mobile')
def mobile():
    return send_file(os.path.join(os.path.dirname(__file__), '..', 'files', 'cairn_home_mobile.html'))

@app.route('/plan-setup')
def plan_setup():
    return send_file(os.path.join(os.path.dirname(__file__), '..', 'files', 'cairn_plan_onboarding.html'))

# ─── PLAN STATUS ───
@app.route('/api/plan/status', methods=['GET'])
def plan_status():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT id, name, goal_type, race_name, race_date, total_weeks, status FROM plans WHERE status = 'active' ORDER BY created_at DESC LIMIT 1")
        row = cur.fetchone()
        conn.close()
        if row:
            return jsonify({
                "status": "ok",
                "has_plan": True,
                "plan": {
                    "id": row[0], "name": row[1], "goal_type": row[2],
                    "race_name": row[3], "race_date": str(row[4]) if row[4] else None,
                    "total_weeks": row[5], "status": row[6]
                }
            })
        return jsonify({"status": "ok", "has_plan": False})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ─── GPX UPLOAD + ANALYSE ───
@app.route('/api/gpx/analyse', methods=['POST'])
def analyse_gpx():
    """
    Empfängt eine GPX-Datei als multipart/form-data oder base64 JSON.
    Gibt Streckenkennzahlen zurück die in den Plan-Prompt fliessen.
    """
    try:
        import sys, os; sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..")); from data.gpx_parser import parse_gpx

        gpx_content = None

        # Multipart upload
        if 'file' in request.files:
            f = request.files['file']
            gpx_content = f.read().decode('utf-8')
        # Base64 JSON
        elif request.is_json:
            data = request.get_json(force=True)
            import base64
            gpx_b64 = data.get('gpx_base64', '')
            if gpx_b64:
                gpx_content = base64.b64decode(gpx_b64).decode('utf-8')

        if not gpx_content:
            return jsonify({"status": "error", "message": "Keine GPX-Datei"}), 400

        result = parse_gpx(gpx_content)
        if "error" in result:
            return jsonify({"status": "error", "message": result["error"]}), 400

        return jsonify({"status": "ok", "gpx": result})

    except Exception as e:
        return jsonify({"status": "error", "message": str(e), "trace": traceback.format_exc()}), 500

# ─── PLAN GENERIEREN (asynchron via GitHub Action) ───
@app.route('/api/plan/job/<job_id>', methods=['GET'])
def get_plan_job(job_id):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT status, error FROM plan_jobs WHERE id = %s", (job_id,))
        row = cur.fetchone()
        conn.close()
        if not row:
            return jsonify({'status': 'unknown'})
        status, error = row
        result = {'status': status}
        if error:
            result['message'] = error
        return jsonify(result)
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/plan/generate', methods=['POST'])
def generate_plan():
    try:
        from psycopg2.extras import Json
        data = request.get_json(force=True)
        job_id = str(int(time.time()))

        conn = get_db()
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO plan_jobs (id, data, status) VALUES (%s, %s, 'pending')",
                (job_id, Json(data))
            )
            conn.commit()
        finally:
            conn.close()

        # Plan-Generierung läuft als GitHub Action (data/generate_plan.py), nicht mehr im
        # Flask-Prozess selbst — vermeidet Railway-Request-Timeouts bei langen Plänen.
        import urllib.request
        github_token = os.getenv("CAIRN_GITHUB_TOKEN")
        if github_token:
            payload = json.dumps({"ref": "main", "inputs": {"job_id": job_id}}).encode()
            req = urllib.request.Request(
                "https://api.github.com/repos/ahuhse1993-del/hybrid-fitness-os/actions/workflows/plan_generate.yml/dispatches",
                data=payload,
                headers={
                    "Authorization": f"Bearer {github_token}",
                    "Accept": "application/vnd.github.v3+json",
                    "Content-Type": "application/json"
                },
                method="POST"
            )
            urllib.request.urlopen(req, timeout=10)
        else:
            print("Kein CAIRN_GITHUB_TOKEN gesetzt — Plan-Job gespeichert, aber kein Workflow getriggert.")

        return jsonify({'status': 'ok', 'job_id': job_id})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

# ─── WORKOUT-VORSCHLÄGE (Coach-Feedback zu Strength-Sessions eines Plans) ───
@app.route('/api/plan/workout-suggestions', methods=['GET'])
def get_workout_suggestions():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, workout_suggestions FROM plans
            WHERE status = 'active' ORDER BY created_at DESC LIMIT 1
        """)
        row = cur.fetchone()
        conn.close()
        if not row:
            return jsonify({"status": "ok", "plan_id": None, "suggestions": []})
        return jsonify({"status": "ok", "plan_id": row[0], "suggestions": row[1] or []})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e), "trace": traceback.format_exc()}), 500

@app.route('/api/plan/workout-suggestions/respond', methods=['POST'])
def respond_workout_suggestion():
    try:
        from psycopg2.extras import Json
        data = request.get_json(force=True)
        suggestion_id = data.get('suggestion_id')
        new_status = data.get('status')
        comment = data.get('comment')
        plan_id = data.get('plan_id')

        if not suggestion_id or new_status not in ('accepted', 'rejected', 'pending'):
            return jsonify({"status": "error", "message": "suggestion_id und status (accepted/rejected) erforderlich"}), 400

        conn = get_db()
        cur = conn.cursor()
        if plan_id:
            cur.execute("SELECT id, workout_suggestions FROM plans WHERE id = %s", (plan_id,))
        else:
            cur.execute("""
                SELECT id, workout_suggestions FROM plans
                WHERE status = 'active' ORDER BY created_at DESC LIMIT 1
            """)
        row = cur.fetchone()
        if not row:
            conn.close()
            return jsonify({"status": "error", "message": "Kein Plan gefunden"}), 404

        plan_id, suggestions = row[0], row[1] or []
        found = False
        for s in suggestions:
            if s.get('id') == suggestion_id:
                s['status'] = new_status
                s['comment'] = comment
                s['responded_at'] = datetime.utcnow().isoformat()
                found = True
                break

        if not found:
            conn.close()
            return jsonify({"status": "error", "message": "Vorschlag nicht gefunden"}), 404

        cur.execute("UPDATE plans SET workout_suggestions = %s WHERE id = %s", (Json(suggestions), plan_id))
        conn.commit()
        conn.close()
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e), "trace": traceback.format_exc()}), 500

@app.route('/api/checkin', methods=['POST'])
def save_checkin():
    data = request.get_json()
    feel = data.get('feel', '')
    notes = ', '.join(data.get('notes', []))
    text = data.get('text', '')
    already_trained = data.get('already_trained', False)
    today = get_today()

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

    try:
        import urllib.request, urllib.error, time
        github_token = os.getenv("CAIRN_GITHUB_TOKEN")
        if github_token:
            headers = {
                "Authorization": f"Bearer {github_token}",
                "Accept": "application/vnd.github.v3+json",
                "Content-Type": "application/json"
            }
            if already_trained:
                try:
                    req0 = urllib.request.Request(
                        "https://api.github.com/repos/ahuhse1993-del/hybrid-fitness-os/actions/workflows/garmin_sync.yml/dispatches",
                        data=b'{"ref":"main"}',
                        headers=headers,
                        method="POST"
                    )
                    urllib.request.urlopen(req0, timeout=10)
                    time.sleep(90)
                except Exception:
                    pass

            req = urllib.request.Request(
                "https://api.github.com/repos/ahuhse1993-del/hybrid-fitness-os/actions/workflows/health_sync.yml/dispatches",
                data=b'{"ref":"main"}',
                headers=headers,
                method="POST"
            )
            urllib.request.urlopen(req, timeout=10)
            time.sleep(5)

            req2 = urllib.request.Request(
                "https://api.github.com/repos/ahuhse1993-del/hybrid-fitness-os/actions/workflows/health_sync.yml/runs?per_page=1",
                headers=headers
            )
            resp = urllib.request.urlopen(req2, timeout=10)
            runs_data = json.loads(resp.read())
            run_id = runs_data["workflow_runs"][0]["id"]

            for _ in range(12):
                time.sleep(5)
                req3 = urllib.request.Request(
                    f"https://api.github.com/repos/ahuhse1993-del/hybrid-fitness-os/actions/runs/{run_id}",
                    headers=headers
                )
                resp3 = urllib.request.urlopen(req3, timeout=10)
                run_data = json.loads(resp3.read())
                if run_data.get("status") == "completed":
                    break
    except Exception:
        pass

    return jsonify({"status": "ok"})

@app.route('/api/morning-brief', methods=['GET'])
def morning_brief():
    try:
        from coach.morning_brief import generate_morning_brief

        today = get_today()
        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            SELECT feel, notes, athlete_text, morning_brief, suggestion,
                   session_type, session_zone, primary_target, secondary_target,
                   sleep_duration_h, hrv_last_night
            FROM daily_logs WHERE date = %s
        """, (today,))
        row = cur.fetchone()

        athlete_feedback = {}
        if row:
            has_health_data = row[9] is not None or row[10] is not None
            if row[3] and row[4] is not None and has_health_data:
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
        return jsonify({"status": "error", "message": str(e), "trace": traceback.format_exc()}), 500

@app.route('/api/dashboard', methods=['GET'])
def dashboard():
    try:
        conn = get_db()
        cur = conn.cursor()
        today = get_today()
        monday = today - timedelta(days=today.weekday())
        sunday = monday + timedelta(days=6)

        cur.execute("""
            SELECT COALESCE(SUM(distance_km), 0), COUNT(*)
            FROM trainings
            WHERE date >= %s AND date <= %s
            AND type NOT IN ('WeightTraining', 'Strength')
        """, (monday, sunday))
        row = cur.fetchone()
        week_km = round(float(row[0]), 1) if row[0] else 0
        week_sessions_done = int(row[1]) if row[1] else 0

        cur.execute("""
            SELECT COUNT(*) FROM training_plan
            WHERE week_date = %s AND session_type != 'Rest Day'
        """, (monday,))
        row = cur.fetchone()
        week_sessions_planned = int(row[0]) if row[0] else 0

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
        return jsonify({"status": "error", "message": str(e), "trace": traceback.format_exc()}), 500

# ─── ATHLETE PROFILE (Schicht 1 — editierbar) ───
@app.route('/api/athlete/profile', methods=['GET'])
def get_athlete_profile():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT name, age, weight_kg,
                   hr_z1_min, hr_z1_max, hr_z2_min, hr_z2_max, hr_z3_min, hr_z3_max,
                   hr_z4_min, hr_z4_max, hr_z5_min, hr_z5_max,
                   pace_z1, pace_z2, pace_z3, pace_z4, pace_z5,
                   shoes, cross_rennrad, cross_schwimmen, cross_wandern, cross_ski,
                   long_term_goals
            FROM athlete_profile ORDER BY id LIMIT 1
        """)
        row = cur.fetchone()
        conn.close()

        if not row:
            return jsonify({"status": "ok", "has_profile": False, "profile": None})

        profile = {
            "name": row[0] or "",
            "age": row[1],
            "weight_kg": float(row[2]) if row[2] is not None else None,
            "hr_zones": {
                "z1": {"min": row[3], "max": row[4]},
                "z2": {"min": row[5], "max": row[6]},
                "z3": {"min": row[7], "max": row[8]},
                "z4": {"min": row[9], "max": row[10]},
                "z5": {"min": row[11], "max": row[12]},
            },
            "pace_zones": {
                "z1": row[13] or "", "z2": row[14] or "", "z3": row[15] or "",
                "z4": row[16] or "", "z5": row[17] or "",
            },
            "shoes": row[18] or [],
            "cross_training": {
                "rennrad": bool(row[19]), "schwimmen": bool(row[20]),
                "wandern": bool(row[21]), "ski": bool(row[22]),
            },
            "long_term_goals": row[23] or "",
        }
        return jsonify({"status": "ok", "has_profile": True, "profile": profile})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e), "trace": traceback.format_exc()}), 500

@app.route('/api/athlete/profile', methods=['POST'])
def save_athlete_profile():
    try:
        from psycopg2.extras import Json
        data = request.get_json(force=True)

        hr = data.get('hr_zones', {}) or {}
        pace = data.get('pace_zones', {}) or {}
        cross = data.get('cross_training', {}) or {}

        def hr_min(z): return (hr.get(z) or {}).get('min')
        def hr_max(z): return (hr.get(z) or {}).get('max')

        values = (
            data.get('name', ''), data.get('age'), data.get('weight_kg'),
            hr_min('z1'), hr_max('z1'), hr_min('z2'), hr_max('z2'),
            hr_min('z3'), hr_max('z3'), hr_min('z4'), hr_max('z4'),
            hr_min('z5'), hr_max('z5'),
            pace.get('z1', ''), pace.get('z2', ''), pace.get('z3', ''),
            pace.get('z4', ''), pace.get('z5', ''),
            Json(data.get('shoes', [])),
            bool(cross.get('rennrad')), bool(cross.get('schwimmen')),
            bool(cross.get('wandern')), bool(cross.get('ski')),
            data.get('long_term_goals', ''),
        )

        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT id FROM athlete_profile ORDER BY id LIMIT 1")
        existing = cur.fetchone()

        if existing:
            cur.execute("""
                UPDATE athlete_profile SET
                    name=%s, age=%s, weight_kg=%s,
                    hr_z1_min=%s, hr_z1_max=%s, hr_z2_min=%s, hr_z2_max=%s,
                    hr_z3_min=%s, hr_z3_max=%s, hr_z4_min=%s, hr_z4_max=%s,
                    hr_z5_min=%s, hr_z5_max=%s,
                    pace_z1=%s, pace_z2=%s, pace_z3=%s, pace_z4=%s, pace_z5=%s,
                    shoes=%s,
                    cross_rennrad=%s, cross_schwimmen=%s, cross_wandern=%s, cross_ski=%s,
                    long_term_goals=%s, updated_at=NOW()
                WHERE id=%s
            """, values + (existing[0],))
        else:
            cur.execute("""
                INSERT INTO athlete_profile
                    (name, age, weight_kg,
                     hr_z1_min, hr_z1_max, hr_z2_min, hr_z2_max,
                     hr_z3_min, hr_z3_max, hr_z4_min, hr_z4_max,
                     hr_z5_min, hr_z5_max,
                     pace_z1, pace_z2, pace_z3, pace_z4, pace_z5,
                     shoes, cross_rennrad, cross_schwimmen, cross_wandern, cross_ski,
                     long_term_goals)
                VALUES (%s,%s,%s, %s,%s,%s,%s, %s,%s,%s,%s, %s,%s, %s,%s,%s,%s,%s, %s, %s,%s,%s,%s, %s)
            """, values)

        conn.commit()
        conn.close()
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e), "trace": traceback.format_exc()}), 500

# ─── COACH CONTEXT (Schicht 2 — read-only, nur Coach schreibt) ───
@app.route('/api/athlete/context', methods=['GET'])
def get_athlete_context():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT category, content, created_at
            FROM coach_context
            ORDER BY created_at DESC
            LIMIT 100
        """)
        rows = cur.fetchall()
        conn.close()
        context = [
            {"category": r[0] or "", "content": r[1], "created_at": r[2].isoformat() if r[2] else None}
            for r in rows
        ]
        return jsonify({"status": "ok", "context": context})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e), "trace": traceback.format_exc()}), 500

# ─── HEVY ROUTINE (Übungsliste einer CAIRN-Routine nach Name) ───
@app.route('/api/hevy/routine', methods=['GET'])
def get_hevy_routine():
    try:
        import requests
        name = (request.args.get('name') or '').strip()
        if not name:
            return jsonify({"status": "error", "message": "name erforderlich"}), 400

        hevy_resp = requests.get(
            "https://api.hevyapp.com/v1/routines",
            headers={"api-key": os.getenv("HEVY_API_KEY")},
            params={"page": 1, "pageSize": 10},
            timeout=10
        )
        routines = hevy_resp.json().get('routines', [])

        match = None
        for r in routines:
            if r.get('folder_id') == HEVY_CAIRN_FOLDER_ID and (r.get('title') or '').strip() == name:
                match = r
                break

        if not match:
            return jsonify({"status": "error", "message": "Routine nicht gefunden"}), 404

        exercises = []
        for e in match.get('exercises', []):
            sets = e.get('sets', [])
            reps = sets[-1].get('reps') if sets else None
            exercises.append({
                "title": e.get('title', ''),
                "sets": len(sets),
                "reps": reps
            })

        return jsonify({"status": "ok", "routine_name": name, "exercises": exercises})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e), "trace": traceback.format_exc()}), 500

@app.route('/api/plan', methods=['GET'])
def get_plan():
    try:
        conn = get_db()
        cur = conn.cursor()
        today = get_today()

        offset_weeks = request.args.get('offset_weeks', default=0, type=int)
        monday = today - timedelta(days=today.weekday()) + timedelta(weeks=offset_weeks)

        cur.execute("""
            SELECT id, week_date, day_of_week, session_type, session_zone,
                   duration_min, distance_km, notes, phase
            FROM training_plan
            WHERE week_date >= %s AND week_date < %s
            ORDER BY week_date, day_of_week
        """, (monday - timedelta(weeks=1), monday + timedelta(weeks=52)))
        plan_rows = cur.fetchall()

        cur.execute("""
            SELECT date, type, notes, duration_minutes, distance_km, heart_rate_avg, id
            FROM trainings
            WHERE date >= %s AND date <= %s
            ORDER BY date
        """, (monday, max(today, monday + timedelta(weeks=4))))
        actual_rows = cur.fetchall()
        conn.close()

        type_match = {
            'Easy Run': ['Run'],
            'Recovery Run': ['Run'],
            'Long Run': ['Run', 'TrailRun'],
            'Tempo Session': ['Run'],
            'Interval Session': ['Run'],
            'Sprint Session': ['Run'],
            'Hill Session': ['Run', 'TrailRun'],
            'Trail Run': ['TrailRun', 'Run'],
            'Cross Training': ['Ride', 'Swim', 'Walk', 'Hike', 'Yoga', 'Other'],
            'Strength Training': ['WeightTraining'],
            'Mobility': ['WeightTraining', 'Yoga'],
            'Time Trial': ['Run', 'TrailRun'],
            'Race Day': ['Run', 'TrailRun'],
        }

        actual_by_date = {}
        for r in actual_rows:
            d = str(r[0])
            if d not in actual_by_date:
                actual_by_date[d] = []
            actual_by_date[d].append({
                "type": r[1], "name": r[2] or r[1],
                "duration_min": r[3],
                "distance_km": float(r[4]) if r[4] else 0,
                "avg_hr": r[5], "training_id": r[6],
            })

        plan = []
        matched_training_ids = set()

        for r in plan_rows:
            week_date = str(r[1])
            day_of_week = r[2]
            session_type = r[3]
            item_date = date.fromisoformat(week_date) + timedelta(days=day_of_week - 1)
            item_date_str = str(item_date)
            is_past = item_date <= today

            item = {
                "id": r[0], "week_date": week_date, "day_of_week": day_of_week,
                "session_type": session_type, "session_zone": r[4],
                "duration_min": r[5], "distance_km": float(r[6]) if r[6] else 0,
                "notes": r[7] or "", "phase": r[8] or "base", "is_done": False, "is_mismatch": False,
                "actual_type": None, "actual_name": None,
                "actual_km": 0, "actual_min": 0, "actual_hr": None, "training_id": None
            }

            if is_past and item_date_str in actual_by_date:
                expected_types = type_match.get(session_type, [])
                for actual in actual_by_date[item_date_str]:
                    if actual["training_id"] not in matched_training_ids:
                        item["is_done"] = True
                        item["is_mismatch"] = actual["type"] not in expected_types
                        item["actual_type"] = actual["type"]
                        item["actual_name"] = actual["name"]
                        item["actual_km"] = actual["distance_km"]
                        item["actual_min"] = actual["duration_min"]
                        item["actual_hr"] = actual["avg_hr"]
                        item["training_id"] = actual["training_id"]
                        matched_training_ids.add(actual["training_id"])
                        break

            plan.append(item)

        for d_str, activities in actual_by_date.items():
            for actual in activities:
                if actual["training_id"] not in matched_training_ids:
                    act_date = date.fromisoformat(d_str)
                    act_monday = act_date - timedelta(days=act_date.weekday())
                    plan.append({
                        "id": -actual["training_id"],
                        "week_date": str(act_monday),
                        "day_of_week": act_date.isoweekday(),
                        "session_type": actual["type"], "session_zone": "",
                        "duration_min": actual["duration_min"],
                        "distance_km": actual["distance_km"],
                        "notes": actual["name"], "phase": "base", "is_done": True,
                        "is_mismatch": False, "is_spontaneous": True,
                        "actual_type": actual["type"], "actual_name": actual["name"],
                        "actual_km": actual["distance_km"], "actual_min": actual["duration_min"],
                        "actual_hr": actual["avg_hr"], "training_id": actual["training_id"]
                    })
                    matched_training_ids.add(actual["training_id"])

        return jsonify({"status": "ok", "plan": plan})

    except Exception as e:
        return jsonify({"status": "error", "message": str(e), "trace": traceback.format_exc()}), 500

@app.route('/api/activities/month', methods=['GET'])
def get_month():
    try:
        today = get_today()
        year = request.args.get('year', default=today.year, type=int)
        month = request.args.get('month', default=today.month, type=int)

        first_day = date(year, month, 1)
        last_day = date(year + 1, 1, 1) - timedelta(days=1) if month == 12 else date(year, month + 1, 1) - timedelta(days=1)
        view_start = first_day - timedelta(days=first_day.weekday())
        view_end = last_day + timedelta(days=(6 - last_day.weekday()))

        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            SELECT date, type, notes, duration_minutes, distance_km, heart_rate_avg, id
            FROM trainings
            WHERE date >= %s AND date <= %s
            ORDER BY date
        """, (view_start, view_end))
        actual_rows = cur.fetchall()

        cur.execute("""
            SELECT week_date, day_of_week, session_type, session_zone, duration_min, distance_km, notes, id
            FROM training_plan
            WHERE week_date >= %s AND week_date <= %s
            ORDER BY week_date, day_of_week
        """, (view_start, view_end))
        plan_rows = cur.fetchall()

        conn.close()

        actual_by_date = {}
        for r in actual_rows:
            d = str(r[0])
            if d not in actual_by_date:
                actual_by_date[d] = []
            actual_by_date[d].append({
                "type": r[1],
                "name": (r[2] or r[1]).split(' | ')[0],
                "duration_min": r[3],
                "distance_km": float(r[4]) if r[4] else 0,
                "avg_hr": r[5],
                "training_id": r[6],
                "is_done": True
            })

        plan_by_date = {}
        for r in plan_rows:
            week_date = date.fromisoformat(str(r[0]))
            plan_date = str(week_date + timedelta(days=r[1] - 1))
            if plan_date not in plan_by_date:
                plan_by_date[plan_date] = []
            plan_by_date[plan_date].append({
                "session_type": r[2],
                "session_zone": r[3] or "",
                "duration_min": r[4],
                "distance_km": float(r[5]) if r[5] else 0,
                "notes": r[6] or "",
                "plan_id": r[7],
                "is_done": False
            })

        return jsonify({
            "status": "ok",
            "year": year,
            "month": month,
            "view_start": str(view_start),
            "view_end": str(view_end),
            "today": str(today),
            "actual": actual_by_date,
            "plan": plan_by_date
        })

    except Exception as e:
        return jsonify({"status": "error", "message": str(e), "trace": traceback.format_exc()}), 500

@app.route('/api/activities/recent', methods=['GET'])
def get_recent_activities():
    try:
        limit = request.args.get('limit', default=50, type=int)
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, date, type, notes, distance_km, duration_minutes
            FROM trainings
            ORDER BY date DESC
            LIMIT %s
        """, (limit,))
        rows = cur.fetchall()
        conn.close()
        activities = []
        for r in rows:
            activities.append({
                "training_id": r[0],
                "date": str(r[1]),
                "type": r[2],
                "name": (r[3] or r[2]).split(' | ')[0],
                "distance_km": float(r[4]) if r[4] else 0,
                "duration_min": r[5] or 0
            })
        return jsonify({"status": "ok", "activities": activities})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e), "trace": traceback.format_exc()}), 500

def build_athlete_context():
    """
    Lädt Athletenprofil, CAIRN-Routinen, Plan, jüngste Aktivitäten und Befinden
    und baut daraus den ATHLETE CONTEXT Block für den Coach-Chat-System-Prompt.
    """
    try:
        today = get_today()
        conn = get_db()
        cur = conn.cursor()

        # 1. athlete_profile
        cur.execute("""
            SELECT name, long_term_goals,
                   hr_z1_min, hr_z1_max, hr_z2_min, hr_z2_max, hr_z3_min, hr_z3_max,
                   hr_z4_min, hr_z4_max, hr_z5_min, hr_z5_max,
                   pace_z1, pace_z2, pace_z3, pace_z4, pace_z5,
                   cross_rennrad, cross_schwimmen, cross_wandern, cross_ski
            FROM athlete_profile ORDER BY id LIMIT 1
        """)
        profile_row = cur.fetchone()

        # 2. cairn_routines
        cur.execute("SELECT title, exercises FROM cairn_routines")
        routine_rows = cur.fetchall()

        # 3. training_plan: aktuelle + nächste 2 Wochen
        monday = today - timedelta(days=today.weekday())
        window_end = monday + timedelta(weeks=3)
        cur.execute("""
            SELECT session_type, notes, day_of_week, week_date
            FROM training_plan
            WHERE week_date >= %s AND week_date < %s
            ORDER BY week_date, day_of_week
        """, (monday, window_end))
        plan_rows = cur.fetchall()

        # 4. trainings: letzte 28 Tage (4 Wochen)
        cur.execute("""
            SELECT date, type, distance_km, duration_minutes, heart_rate_avg
            FROM trainings
            WHERE date >= %s
            ORDER BY date
        """, (today - timedelta(days=28),))
        training_rows = cur.fetchall()

        # 5. daily_logs: letzte 7 Tage
        cur.execute("""
            SELECT date, feel, hrv_last_night, sleep_duration_h, resting_hr
            FROM daily_logs
            WHERE date >= %s
            ORDER BY date DESC
        """, (today - timedelta(days=7),))
        log_rows = cur.fetchall()

        # 6. plans: aktiver Plan
        cur.execute("""
            SELECT race_name, race_date, total_weeks
            FROM plans WHERE status = 'active'
            ORDER BY created_at DESC LIMIT 1
        """)
        plan_row = cur.fetchone()

        conn.close()
    except Exception as e:
        print(f"Athlete-Context Fehler: {e}")
        return ""

    # ── Aufbereiten ──
    name = (profile_row[0] if profile_row else '') or 'Athlet'
    long_term_goals = (profile_row[1] if profile_row else '') or 'keine angegeben'

    hr_zones_str = ''
    pace_zones_str = ''
    cross_prefs = []
    if profile_row:
        hr_parts = []
        for i, label in enumerate(['Z1', 'Z2', 'Z3', 'Z4', 'Z5']):
            lo, hi = profile_row[2 + i * 2], profile_row[3 + i * 2]
            if lo is not None and hi is not None:
                hr_parts.append(f"{label} {lo}-{hi}")
        hr_zones_str = ', '.join(hr_parts)

        pace_parts = []
        for i, label in enumerate(['Z1', 'Z2', 'Z3', 'Z4', 'Z5']):
            val = profile_row[12 + i]
            if val:
                pace_parts.append(f"{label} {val}")
        pace_zones_str = ', '.join(pace_parts)

        if profile_row[17]: cross_prefs.append('Rennrad')
        if profile_row[18]: cross_prefs.append('Schwimmen')
        if profile_row[19]: cross_prefs.append('Wandern')
        if profile_row[20]: cross_prefs.append('Ski')

    routines_str = '; '.join(
        f"{title}: {', '.join(exercises or [])}" for title, exercises in routine_rows
    )

    day_names = ['', 'Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So']
    plan_lines = []
    for session_type, notes, day_of_week, week_date in plan_rows:
        line = f"{day_names[day_of_week]} {week_date}: {session_type}"
        if notes:
            line += f" ({notes})"
        plan_lines.append(line)
    plan_str = '\n'.join(plan_lines) if plan_lines else 'Keine Sessions geplant.'

    total_km = sum(float(r[2]) for r in training_rows if r[2])
    total_sessions = len(training_rows)
    hrv_values = [float(r[2]) for r in log_rows if r[2] is not None]
    avg_hrv = round(sum(hrv_values) / len(hrv_values), 1) if hrv_values else None

    today_log = next((r for r in log_rows if str(r[0]) == str(today)), None)
    feel_today = (today_log[1] if today_log else None) or '—'
    hrv_today = today_log[2] if today_log else None
    sleep_today = today_log[3] if today_log else None

    race_name = plan_row[0] if plan_row else None
    race_date = plan_row[1] if plan_row else None

    lines = [
        "ATHLETE CONTEXT:",
        f"Name: {name}",
        "Ziel: " + (f"{race_name} am {race_date}" if race_name else "kein aktives Rennen"),
        f"Langzeitziele: {long_term_goals}",
    ]
    if hr_zones_str:
        lines.append(f"HF-Zonen: {hr_zones_str}")
    if pace_zones_str:
        lines.append(f"Pace-Zonen: {pace_zones_str}")
    if cross_prefs:
        lines.append(f"Cross Training Präferenzen: {', '.join(cross_prefs)}")
    lines.append(f"Trainingsplan (aktuelle + nächste 2 Wochen):\n{plan_str}")
    lines.append(
        f"Letzte 4 Wochen: {total_km:.0f} km, {total_sessions} Sessions"
        + (f", Ø HRV {avg_hrv}" if avg_hrv else "")
    )
    if routines_str:
        lines.append(f"CAIRN Routinen: {routines_str}")
    befinden = f"Feel {feel_today}"
    if hrv_today is not None:
        befinden += f", HRV {hrv_today}"
    if sleep_today is not None:
        befinden += f", Schlaf {sleep_today}h"
    lines.append(f"Befinden heute: {befinden}")

    return '\n'.join(lines)

@app.route('/api/coach-chat', methods=['POST'])
def coach_chat():
    try:
        import anthropic
        data = request.get_json(force=True)
        messages = data.get('messages', [])
        page = data.get('page', 'today')

        page_context = {
            'today': 'Der Athlet befindet sich auf der Today-Seite. Kontext: Morning Brief, heutige Session, Befinden.',
            'plan': 'Der Athlet befindet sich auf der Plan-Seite. Kontext: Wochenplan, Umplanung, Trainingsstruktur.',
            'activities': 'Der Athlet befindet sich auf der Activities-Seite. Kontext: Vergangene Aktivitäten, Analyse.',
            'athlete': 'Der Athlet befindet sich auf der Athlete-Seite. Kontext: Profil, Langzeitziele, Schuhe, Schema.',
            'coach': 'Der Athlet hat den Coach direkt geöffnet. Allgemeines Coaching.'
        }

        workout_suggestions_context = ""
        try:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("""
                SELECT workout_suggestions FROM plans
                WHERE status = 'active' ORDER BY created_at DESC LIMIT 1
            """)
            row = cur.fetchone()
            conn.close()
            suggestions = (row[0] if row else None) or []
            if suggestions:
                lines = []
                for s in suggestions:
                    line = f"- [{s.get('status', 'pending')}] {s.get('workout_name', '')}: {s.get('change', '')} — Begründung: {s.get('reason', '')}"
                    if s.get('comment'):
                        line += f" | Kommentar vom Athlet: {s.get('comment')}"
                    lines.append(line)
                workout_suggestions_context = (
                    "\n\nWORKOUT-VORSCHLÄGE ZUM AKTUELLEN PLAN (inkl. abgelehnte):\n"
                    + "\n".join(lines)
                    + "\nWenn der Athlet danach fragt: erkläre deine Vorschläge, in eigenen Worten. "
                    "Bei einem abgelehnten Vorschlag: wenn der Athlet danach fragt, wiederhole ihn und begründe ihn neu — anders, nicht wortgleich."
                )
        except Exception:
            workout_suggestions_context = ""

        athlete_context = build_athlete_context()

        system = """Du bist CAIRN, ein erfahrener Endurance Coach.
Sprich wie ein ruhiger, erfahrener Bergführer. Nie wie Software.
Kurze Sätze. Direkt. Menschlich. Auf Augenhöhe.
Nie: 'Readiness Score', 'approved', 'freigegeben', 'Algorithmus'.
Immer: Beobachtung, Einordnung, klare Empfehlung.

""" + page_context.get(page, '') + workout_suggestions_context

        if athlete_context:
            system += (
                "\n\n" + athlete_context
                + "\n\nNutze diesen Kontext um individuell zu antworten — nie generisch."
            )

        clean_messages = [m for m in messages if m.get('role') in ['user', 'assistant'] and m.get('content', '').strip()]

        if not clean_messages:
            return jsonify({"status": "ok", "reply": "Ich bin da. Was liegt an?"})

        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=300,
            system=system,
            messages=clean_messages
        )
        reply = response.content[0].text
        return jsonify({"status": "ok", "reply": reply})

    except Exception as e:
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
        return jsonify({"status": "error", "message": str(e), "trace": traceback.format_exc()}), 500

@app.route('/api/plan/check', methods=['POST'])
def check_plan():
    try:
        import anthropic
        from knowledge.loader import load_plan_adaptation_knowledge
        data = request.get_json(force=True)
        week_plan = data.get('week_plan', [])

        # Wochenstruktur als lesbaren Text aufbauen
        lines = []
        for day in week_plan:
            session = day.get('session_type', 'Rest Day')
            notes = day.get('notes', '')
            lines.append(f"{day.get('day', '')}: {session}" + (f" ({notes.split(' · ')[0]})" if notes else ''))

        knowledge = load_plan_adaptation_knowledge()

        prompt = f"""Du bist CAIRN Coach. Prüfe diese Trainingsstruktur auf Probleme.

WOCHENSTRUKTUR (mehrere Wochen möglich):
{chr(10).join(lines)}

DEINE WISSENSBASIS:
{knowledge}

AUFGABE:
Prüfe ob diese Struktur für den Athleten sinnvoll ist.
Achte besonders auf:
- Harte Sessions direkt hintereinander
- Kein Erholungstag nach Long Run
- Kraft direkt vor oder nach Quality Sessions
- Zu viel Belastung ohne Deload

Wenn alles ok ist: ok=true, kurze bestätigende Aussage.
Wenn Problem: ok=false, 1-2 Sätze im CAIRN-Ton was du siehst und warum.
Nie wie Software. Nie "Algorithmus". Wie ein erfahrener Bergführer.

Antworte NUR mit JSON:
{{"ok": true, "message": "Was du siehst in 1-2 Sätzen."}}"""

        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = message.content[0].text.strip().replace('```json', '').replace('```', '').strip()
        try:
            result = json.loads(raw)
        except Exception:
            result = {"ok": True, "message": "Passt so."}
        return jsonify({"status": "ok", "check": result})

    except Exception as e:
        return jsonify({"status": "error", "message": str(e), "trace": traceback.format_exc()}), 500

# ─── PLAN SESSION VERSCHIEBEN (Drag & Drop) ───
@app.route('/api/plan/move', methods=['POST'])
def move_plan_session():
    """
    Verschiebt eine Session von A nach B (wochenübergreifend).
    Tauscht wenn B belegt, setzt ein wenn B leer (Rest Day).
    Danach: Coach-Check der betroffenen Tage.
    Gibt Coach-Warnung zurück wenn nötig – speichert trotzdem.
    """
    try:
        import anthropic
        from knowledge.loader import load_plan_adaptation_knowledge
        data = request.get_json(force=True)

        source_id = data.get('source_id')          # training_plan.id der gezogenen Session
        target_week = data.get('target_week')       # Ziel-Wochendatum (YYYY-MM-DD)
        target_day = data.get('target_day')         # Ziel-Tag 1-7

        if not source_id or not target_week or not target_day:
            return jsonify({"status": "error", "message": "source_id, target_week, target_day erforderlich"}), 400

        conn = get_db()
        cur = conn.cursor()

        # Quelle laden
        cur.execute("""
            SELECT id, week_date, day_of_week, session_type, session_zone,
                   distance_km, duration_min, notes, phase, plan_id
            FROM training_plan WHERE id = %s
        """, (source_id,))
        source = cur.fetchone()
        if not source:
            conn.close()
            return jsonify({"status": "error", "message": "Session nicht gefunden"}), 404

        source_week = str(source[1])
        source_day = source[2]

        # Ziel prüfen ob belegt
        cur.execute("""
            SELECT id, session_type FROM training_plan
            WHERE week_date = %s AND day_of_week = %s
        """, (target_week, target_day))
        target = cur.fetchone()

        # Tausch oder Einsetzen
        if target:
            # Tausch: Ziel kommt an Quellposition
            cur.execute("""
                UPDATE training_plan SET week_date=%s, day_of_week=%s
                WHERE id=%s
            """, (source_week, source_day, target[0]))

        # Quelle an Zielposition
        cur.execute("""
            UPDATE training_plan SET week_date=%s, day_of_week=%s
            WHERE id=%s
        """, (target_week, target_day, source_id))

        conn.commit()

        # Betroffene Tage für Coach-Check laden (3 Tage um Quelle + 3 Tage um Ziel)
        source_date = date.fromisoformat(source_week) + timedelta(days=source_day - 1)
        target_date = date.fromisoformat(target_week) + timedelta(days=target_day - 1)
        check_start = min(source_date, target_date) - timedelta(days=2)
        check_end = max(source_date, target_date) + timedelta(days=2)

        cur.execute("""
            SELECT week_date, day_of_week, session_type, notes
            FROM training_plan
            WHERE week_date >= %s AND week_date <= %s
            ORDER BY week_date, day_of_week
        """, (
            check_start - timedelta(days=check_start.weekday()),
            check_end + timedelta(days=6 - check_end.weekday())
        ))
        context_rows = cur.fetchall()
        conn.close()

        # Coach-Check
        day_names = ['', 'Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So']
        lines = []
        for r in context_rows:
            d = date.fromisoformat(str(r[0])) + timedelta(days=r[1] - 1)
            notes_short = (r[3] or '').split(' · ')[0] if r[3] else ''
            lines.append(f"{d.strftime('%d.%m')} {day_names[r[1]]}: {r[2]}" + (f" ({notes_short})" if notes_short else ''))

        knowledge = load_plan_adaptation_knowledge()

        prompt = f"""Du bist CAIRN Coach. Ein Athlet hat gerade eine Session verschoben.

BETROFFENE TAGE (Kontext um die Verschiebung):
{chr(10).join(lines)}

VERSCHOBEN: {source[3]} von {source_date.strftime('%d.%m')} nach {target_date.strftime('%d.%m')}

DEINE WISSENSBASIS:
{knowledge}

Prüfe nur ob es ein echtes Problem gibt.
Kleine Unschönheiten → ok=true, kurze positive Aussage.
Echtes Problem (z.B. Quality direkt nach Long Run, keine Erholung) → ok=false, 1-2 Sätze was du siehst.
Nie wie Software. Wie ein ruhiger Bergführer.

NUR JSON:
{{"ok": true, "message": "Deine Beobachtung in 1-2 Sätzen."}}"""

        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=250,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = message.content[0].text.strip().replace('```json', '').replace('```', '').strip()
        try:
            coach_check = json.loads(raw)
        except Exception:
            coach_check = {"ok": True, "message": "Passt."}

        return jsonify({
            "status": "ok",
            "moved": True,
            "swapped": target is not None,
            "coach": coach_check
        })

    except Exception as e:
        return jsonify({"status": "error", "message": str(e), "trace": traceback.format_exc()}), 500

@app.route('/api/activity/<int:training_id>', methods=['GET'])
def get_activity(training_id):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, date, type, notes, duration_minutes, distance_km,
                   heart_rate_avg, garmin_id
            FROM trainings WHERE id = %s
        """, (training_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return jsonify({"status": "error", "message": "Nicht gefunden"}), 404

        activity = {
            "id": row[0], "date": str(row[1]), "type": row[2],
            "name": row[3] or row[2], "duration_min": row[4],
            "distance_km": float(row[5]) if row[5] else 0,
            "avg_hr": row[6], "garmin_id": row[7]
        }

        cur.execute("""
            SELECT split_number, distance_km, pace_seconds, heart_rate_avg, elevation_gain, cadence_avg
            FROM splits WHERE training_id = %s ORDER BY split_number
        """, (training_id,))
        splits = []
        for s in cur.fetchall():
            pace_min = f"{s[2]//60}:{str(s[2]%60).zfill(2)}" if s[2] else None
            splits.append({
                "split": s[0], "distance_km": float(s[1]) if s[1] else 0,
                "pace": pace_min, "hr": s[3],
                "elevation": float(s[4]) if s[4] else 0, "cadence": s[5]
            })

        conn.close()
        return jsonify({"status": "ok", "activity": activity, "splits": splits})

    except Exception as e:
        return jsonify({"status": "error", "message": str(e), "trace": traceback.format_exc()}), 500

@app.route('/api/activity/<int:training_id>/analyse', methods=['GET'])
def analyse_activity(training_id):
    try:
        from coach.workout_analysis import generate_workout_analysis
        result = generate_workout_analysis(training_id)
        if result is None:
            return jsonify({"status": "error", "message": "Nicht gefunden"}), 404
        return jsonify({"status": "ok", "analysis": result})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e), "trace": traceback.format_exc()}), 500

@app.route('/api/activity/<int:training_id>/chat', methods=['POST'])
def activity_chat(training_id):
    try:
        from coach.workout_chat import generate_chat_reply
        data = request.get_json(force=True)
        message = data.get('message', '')
        history = data.get('history', [])
        if not message:
            return jsonify({"status": "error", "message": "Keine Nachricht"}), 400
        reply = generate_chat_reply(training_id, message, history)
        return jsonify({"status": "ok", "reply": reply})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e), "trace": traceback.format_exc()}), 500

@app.route('/api/activity/<int:training_id>/gps', methods=['GET'])
def get_gps(training_id):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT lat, lon, elevation FROM gps_tracks
            WHERE training_id = %s ORDER BY point_index
        """, (training_id,))
        rows = cur.fetchall()
        conn.close()
        points = [{"lat": float(r[0]), "lon": float(r[1]), "ele": float(r[2]) if r[2] else 0} for r in rows]
        return jsonify({"status": "ok", "points": points})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e), "trace": traceback.format_exc()}), 500

@app.route('/api/activity/<int:training_id>/hr', methods=['GET'])
def get_hr(training_id):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT timestamp_ms, heart_rate FROM hr_tracks
            WHERE training_id = %s ORDER BY point_index
        """, (training_id,))
        rows = cur.fetchall()
        conn.close()
        points = [{"ts": r[0], "hr": r[1]} for r in rows]
        return jsonify({"status": "ok", "points": points})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e), "trace": traceback.format_exc()}), 500

@app.route('/api/new-activities', methods=['GET'])
def new_activities():
    try:
        conn = get_db()
        cur = conn.cursor()
        today = get_today()
        cur.execute("""
            SELECT id, date, type, notes, distance_km, duration_minutes, analysis_done
            FROM trainings
            WHERE date = %s AND analysis_done = FALSE
            ORDER BY id DESC LIMIT 5
        """, (today,))
        rows = cur.fetchall()
        conn.close()
        activities = []
        for r in rows:
            activities.append({
                "id": r[0], "date": str(r[1]), "type": r[2],
                "name": r[3] or r[2],
                "distance_km": float(r[4]) if r[4] else 0,
                "duration_min": r[5] or 0, "analysis_done": r[6] or False
            })
        return jsonify({"status": "ok", "activities": activities})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e), "trace": traceback.format_exc()}), 500

@app.route('/api/activity/<int:training_id>/exercises', methods=['GET'])
def get_exercises(training_id):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT exercise_index, exercise_name, sets, reps_per_set, weight_kg_per_set, notes
            FROM hevy_exercises
            WHERE training_id = %s
            ORDER BY exercise_index
        """, (training_id,))
        rows = cur.fetchall()
        conn.close()
        exercises = []
        for r in rows:
            exercises.append({
                "index": r[0], "name": r[1], "sets": r[2],
                "reps": r[3], "weight_kg": r[4], "notes": r[5]
            })
        return jsonify({"status": "ok", "exercises": exercises})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e), "trace": traceback.format_exc()}), 500

@app.route('/api/activity/<int:training_id>/mark-analysed', methods=['POST'])
def mark_analysed(training_id):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("UPDATE trainings SET analysis_done = TRUE WHERE id = %s", (training_id,))
        conn.commit()
        conn.close()
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/activities/<int:activity_id>/analysis')
def activity_analysis_page(activity_id):
    """Native CAIRN-Analyseseite — statisches HTML, laedt Daten selbst per fetch()."""
    return send_file(os.path.join(os.path.dirname(__file__), '..', 'files', 'cairn_activity_analysis.html'))


@app.route('/api/activities/<int:activity_id>/full-analysis', methods=['GET'])
def get_full_activity_analysis(activity_id):
    """
    Backend fuer /activities/<id>/analysis: deterministische Analysewerte +
    evtl. bereits gespeicherte Coach-Analyse. Nutzt dieselbe Logik wie das
    MCP-Tool prepare_activity_analysis — Frontend und ChatGPT sehen exakt
    denselben Datensatz.
    """
    try:
        from coach.mcp_server import prepare_activity_analysis
        force_refresh = request.args.get('force_refresh', 'false').lower() == 'true'
        result = prepare_activity_analysis(activity_id, force_refresh=force_refresh)
        if "error" in result:
            return jsonify({"status": "error", "message": result["error"]}), 404
        return jsonify({"status": "ok", **result})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e), "trace": traceback.format_exc()}), 500


@app.route('/api/athlete/gear', methods=['GET'])
def list_gear_for_frontend():
    """Fuer die Schuhauswahl-Dropdown auf der Analyseseite."""
    try:
        from coach.mcp_server import list_athlete_gear
        gear_type = request.args.get('gear_type')
        active_only = request.args.get('active_only', 'true').lower() != 'false'
        return jsonify({"status": "ok", "gear": list_athlete_gear(gear_type=gear_type, active_only=active_only)})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e), "trace": traceback.format_exc()}), 500


@app.route('/api/activities/<int:activity_id>/gear', methods=['POST'])
def assign_gear_for_frontend(activity_id):
    """
    Ordnet der Aktivitaet ein Gear-Item zu — ruft dieselbe Funktion wie das
    MCP-Tool assign_activity_gear auf, damit Frontend und ChatGPT denselben
    Datensatz verwenden (keine getrennte Zuordnungslogik).
    """
    try:
        from coach.mcp_server import assign_activity_gear
        data = request.get_json(force=True) or {}
        gear_id = data.get('gear_id')
        if not gear_id:
            return jsonify({"status": "error", "message": "gear_id erforderlich"}), 400
        result = assign_activity_gear(
            activity_id=activity_id, gear_id=gear_id,
            distance_km=data.get('distance_km'), assignment_source="frontend",
        )
        if "error" in result:
            return jsonify({"status": "error", "message": result["error"]}), 400
        return jsonify({"status": "ok", **result})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e), "trace": traceback.format_exc()}), 500


@app.route('/api/activities/<int:activity_id>/coach-analysis', methods=['POST'])
def save_coach_analysis_for_frontend(activity_id):
    """Erlaubt dem Frontend (nicht nur ChatGPT), eine Coach-Analyse zu speichern —
    dieselbe Funktion wie das MCP-Tool save_activity_coach_analysis."""
    try:
        from coach.mcp_server import save_activity_coach_analysis
        data = request.get_json(force=True) or {}
        result = save_activity_coach_analysis(
            activity_id=activity_id,
            source_data_hash=data.get('source_data_hash'),
            analysis=data.get('analysis') or {},
            analysis_schema_version=data.get('analysis_schema_version', 1),
        )
        if "error" in result:
            return jsonify({"status": "error", "message": result["error"]}), 400
        return jsonify({"status": "ok", **result})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e), "trace": traceback.format_exc()}), 500


@app.route('/api/strava/webhook', methods=['GET', 'POST'])
def strava_webhook():
    if request.method == 'GET':
        verify_token = request.args.get('hub.verify_token')
        challenge = request.args.get('hub.challenge')
        if verify_token == os.getenv('STRAVA_VERIFY_TOKEN', 'cairn_strava_webhook'):
            return jsonify({"hub.challenge": challenge})
        return jsonify({"error": "Invalid token"}), 403

    try:
        data = request.get_json()
        object_type = data.get('object_type')
        aspect_type = data.get('aspect_type')
        if object_type != 'activity' or aspect_type != 'create':
            return jsonify({"status": "ignored"})

        import urllib.request
        github_token = os.getenv("CAIRN_GITHUB_TOKEN")
        if github_token:
            payload = json.dumps({"ref": "main", "inputs": {"triggered_by": "webhook"}}).encode()
            req = urllib.request.Request(
                "https://api.github.com/repos/ahuhse1993-del/hybrid-fitness-os/actions/workflows/garmin_sync.yml/dispatches",
                data=payload,
                headers={
                    "Authorization": f"Bearer {github_token}",
                    "Accept": "application/vnd.github.v3+json",
                    "Content-Type": "application/json"
                },
                method="POST"
            )
            urllib.request.urlopen(req, timeout=5)
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

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
    return jsonify({"status": "ok", "date": str(get_today()), "version": "cairn-routine-fix-v4"})

@app.route('/api/cron/health-sync', methods=['GET', 'POST'])
def cron_health_sync():
    try:
        today = get_today()
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT health_complete FROM daily_logs WHERE date = %s", (today,))
        row = cur.fetchone()
        conn.close()

        if row and row[0]:
            return jsonify({"status": "ok", "message": "Already complete", "triggered": False})

        import urllib.request
        github_token = os.getenv("CAIRN_GITHUB_TOKEN")
        if not github_token:
            return jsonify({"status": "error", "message": "No GitHub token"}), 500

        req = urllib.request.Request(
            "https://api.github.com/repos/ahuhse1993-del/hybrid-fitness-os/actions/workflows/health_sync.yml/dispatches",
            data=b'{"ref":"main"}',
            headers={
                "Authorization": f"Bearer {github_token}",
                "Accept": "application/vnd.github.v3+json",
                "Content-Type": "application/json"
            },
            method="POST"
        )
        urllib.request.urlopen(req, timeout=10)
        return jsonify({"status": "ok", "message": "Sync triggered", "triggered": True})

    except Exception as e:
        return jsonify({"status": "error", "message": str(e), "trace": traceback.format_exc()}), 500

# ─── FRONTEND V5 ROUTES ───────────────────────────────────────────────────────

@app.route('/app')
def cairn_app():
    return send_file(os.path.join(os.path.dirname(__file__), '..', 'files', 'cairn_app_v6.html'))

@app.route('/static/assets/cairn/<path:filename>')
def cairn_assets(filename):
    """Serve CAIRN session illustration assets (SVG/PNG)."""
    asset_dir = os.path.join(os.path.dirname(__file__), '..', 'static', 'assets', 'cairn')
    return send_from_directory(asset_dir, filename)


# ─── FRONTEND API: Woche ──────────────────────────────────────────────────────

@app.route('/api/frontend/week')
def frontend_week():
    """
    Aggregierte Wochenansicht: heute + aktuelle Woche + nächste Woche.
    Gibt für jeden Tag eine Session-Karte zurück (auch Rest Days).
    Status: planned | pushed | completed | changed | pending | error
    """
    try:
        today = get_today()
        # Montag dieser Woche bis Sonntag nächster Woche
        monday_this = today - timedelta(days=today.weekday())
        sunday_next = monday_this + timedelta(days=13)

        conn = get_db()
        cur = conn.cursor()

        # Training plan für beide Wochen
        cur.execute("""
            SELECT id,
                   (week_date + (day_of_week - 1) * INTERVAL '1 day')::date AS session_date,
                   session_type, session_zone, distance_km, duration_min,
                   notes, phase, garmin_workout_id, workout_steps, name
            FROM training_plan
            WHERE week_date >= %s AND week_date <= %s
            ORDER BY week_date, day_of_week
        """, (monday_this, monday_this + timedelta(weeks=1)))
        plan_rows = cur.fetchall()

        # Absolvierte Trainings in diesem Zeitraum
        cur.execute("""
            SELECT date, type, distance_km, duration_minutes, heart_rate_avg, id
            FROM trainings
            WHERE date >= %s AND date <= %s
            ORDER BY date
        """, (monday_this, sunday_next))
        actual_rows = cur.fetchall()

        # Garmin Push-Status aus garmin_mcp_log (letzte 14 Tage)
        cur.execute("""
            SELECT workout_name, scheduled_date, garmin_workout_id, garmin_schedule_id,
                   status, action, updated_at
            FROM garmin_mcp_log
            WHERE scheduled_date >= %s AND scheduled_date <= %s
            ORDER BY updated_at DESC
        """, (monday_this, sunday_next))
        garmin_rows = cur.fetchall()
        conn.close()

        # Index: absolvierte Trainings nach Datum
        actual_by_date = {}
        for r in actual_rows:
            d = str(r[0])
            if d not in actual_by_date:
                actual_by_date[d] = []
            actual_by_date[d].append({
                "type": r[1],
                "distance_km": float(r[2]) if r[2] else 0,
                "duration_min": r[3],
                "avg_hr": r[4],
                "training_id": r[5],
            })

        # Index: Garmin-Status nach Datum
        garmin_by_date = {}
        for r in garmin_rows:
            d = str(r[1]) if r[1] else None
            if d and d not in garmin_by_date:
                garmin_by_date[d] = {
                    "garmin_workout_id": r[2],
                    "garmin_schedule_id": r[3],
                    "status": r[4],
                    "action": r[5],
                }

        # Session-Karten bauen
        DAY_DE = ['Montag', 'Dienstag', 'Mittwoch', 'Donnerstag', 'Freitag', 'Samstag', 'Sonntag']
        MONTH_DE = ['', 'Jan', 'Feb', 'Mär', 'Apr', 'Mai', 'Jun', 'Jul', 'Aug', 'Sep', 'Okt', 'Nov', 'Dez']

        sessions = []
        for r in plan_rows:
            session_date = r[1]
            d_str = str(session_date)
            session_type = r[2] or 'Rest Day'
            garmin_info = garmin_by_date.get(d_str, {})
            actual = actual_by_date.get(d_str, [{}])[0]

            # Status ableiten
            if actual.get("training_id"):
                status = "completed"
            elif garmin_info.get("status") == "failed":
                status = "error"
            elif garmin_info.get("status") == "pending":
                status = "pending"
            elif garmin_info.get("garmin_workout_id") or r[8]:  # garmin_workout_id in training_plan
                status = "pushed"
            else:
                status = "planned"

            # Workout-Steps für Strukturprofil
            steps = []
            if r[9]:  # workout_steps JSONB
                try:
                    steps = r[9] if isinstance(r[9], list) else json.loads(r[9])
                except Exception:
                    steps = []

            sessions.append({
                "id": r[0],
                "date": d_str,
                "day_label": DAY_DE[session_date.weekday()],
                "day_number": session_date.day,
                "month_label": MONTH_DE[session_date.month],
                "is_today": session_date == today,
                "is_past": session_date < today,
                "name": r[10] or "",
                "session_type": session_type,
                "session_zone": r[3] or "",
                "km": float(r[4]) if r[4] else None,
                "duration_min": r[5],
                "notes": r[6] or "",
                "phase": r[7] or "base",
                "status": status,
                "garmin_workout_id": garmin_info.get("garmin_workout_id") or r[8],
                "garmin_schedule_id": garmin_info.get("garmin_schedule_id"),
                "steps": steps,
                # Für absolvierte Sessions
                "actual_km": actual.get("distance_km"),
                "actual_min": actual.get("duration_min"),
                "actual_hr": actual.get("avg_hr"),
            })

        return jsonify({
            "status": "ok",
            "today": str(today),
            "range": {"start": str(monday_this), "end": str(sunday_next)},
            "sessions": sessions,
        })

    except Exception as e:
        return jsonify({"status": "error", "message": str(e), "trace": traceback.format_exc()}), 500


# ─── FRONTEND API: Alle Wochen (für Week-View) ────────────────────────────────

@app.route('/api/frontend/week/all')
def frontend_week_all():
    """
    Alle Plan-Wochen für den Week-View der App.
    Gibt ALLE Kalenderwochen des aktiven Plans zurück, groupiert nach Montag–Sonntag.
    Jede Woche enthält sessions[] mit einer Karte pro Trainingstag.
    """
    try:
        today = get_today()
        conn = get_db()
        cur = conn.cursor()

        # Aktiven Plan laden (für current_week)
        cur.execute("""
            SELECT id, current_week, total_weeks
            FROM plans WHERE status = 'active' ORDER BY created_at DESC LIMIT 1
        """)
        plan_row = cur.fetchone()
        current_week_num = plan_row[1] if plan_row else 1

        # Alle Sessions aus training_plan
        cur.execute("""
            SELECT id,
                   (week_date + (day_of_week - 1) * INTERVAL '1 day')::date AS session_date,
                   week_date,
                   session_type, session_zone, distance_km, duration_min,
                   notes, phase, garmin_workout_id, workout_steps,
                   sync_status, elevation_gain_m, sport,
                   km_factor, actual_distance_km, linked_garmin_activity_id, name
            FROM training_plan
            ORDER BY week_date, day_of_week
        """)
        plan_rows = cur.fetchall()

        if not plan_rows:
            conn.close()
            return jsonify([])

        # Datumsbereich für Aktivitäten + Garmin-Logs
        all_dates = [r[1] for r in plan_rows]
        date_min, date_max = min(all_dates), max(all_dates)

        # Absolvierte Trainings
        cur.execute("""
            SELECT date, type, distance_km, duration_minutes, heart_rate_avg, id
            FROM trainings
            WHERE date >= %s AND date <= %s
        """, (date_min, date_max))
        actual_by_date = {}
        for r in cur.fetchall():
            d = str(r[0])
            actual_by_date.setdefault(d, []).append({
                "type": r[1], "distance_km": float(r[2]) if r[2] else 0,
                "duration_min": r[3], "avg_hr": r[4], "training_id": r[5],
            })

        # Garmin-Push-Status
        cur.execute("""
            SELECT workout_name, scheduled_date, garmin_workout_id, garmin_schedule_id,
                   status, action, updated_at
            FROM garmin_mcp_log
            WHERE scheduled_date >= %s AND scheduled_date <= %s
            ORDER BY updated_at DESC
        """, (date_min, date_max))
        garmin_by_date = {}
        for r in cur.fetchall():
            d = str(r[1]) if r[1] else None
            if d and d not in garmin_by_date:
                garmin_by_date[d] = {
                    "garmin_workout_id": r[2], "garmin_schedule_id": r[3],
                    "status": r[4], "action": r[5],
                }

        conn.close()

        DAY_SHORT  = ['MO', 'DI', 'MI', 'DO', 'FR', 'SA', 'SO']
        MONTH_SHORT = ['', 'JAN', 'FEB', 'MÄR', 'APR', 'MAI', 'JUN',
                       'JUL', 'AUG', 'SEP', 'OKT', 'NOV', 'DEZ']

        # Sessions aufbauen und nach Montag (week_date) gruppieren
        weeks_dict = {}  # monday_str -> {meta, sessions[]}
        for r in plan_rows:
            session_date = r[1]
            monday = r[2]          # week_date Montag
            monday_str = str(monday)
            d_str = str(session_date)

            session_type  = r[3] or 'Rest Day'
            garmin_info   = garmin_by_date.get(d_str, {})
            actual        = actual_by_date.get(d_str, [{}])[0]
            sync_status   = r[11] or ''

            # Status ableiten
            if actual.get("training_id"):
                status = "completed"
            elif garmin_info.get("status") == "failed":
                status = "error"
            elif garmin_info.get("status") == "pending":
                status = "pending"
            elif garmin_info.get("garmin_workout_id") or r[9]:
                status = "pushed"
            else:
                status = "planned"

            # Workout-Steps
            steps = []
            if r[10]:
                try:
                    raw = r[10] if isinstance(r[10], (list, dict)) else json.loads(r[10])
                    if isinstance(raw, list):
                        steps = raw
                    elif isinstance(raw, dict):
                        steps = raw.get("steps") or []
                except Exception:
                    steps = []

            session_obj = {
                "id": r[0],
                "date": d_str,
                "day_number": session_date.day,
                "day_short": DAY_SHORT[session_date.weekday()],
                "month_short": MONTH_SHORT[session_date.month],
                "is_today": session_date == today,
                "is_past": session_date < today,
                "name": r[17] or "",
                "session_type": session_type,
                "session_zone": r[4] or "",
                "planned_km": float(r[5]) if r[5] else None,
                "planned_duration_min": r[6],
                "notes": r[7] or "",
                "phase": r[8] or "base",
                "status": status,
                "sync_status": sync_status,
                "garmin_workout_id": garmin_info.get("garmin_workout_id") or r[9],
                "steps": steps,
                "elevation_gain_m": int(r[12]) if r[12] else None,
                "sport": r[13] or None,
                "km_factor": float(r[14]) if r[14] else None,
                "actual_distance_km": float(r[15]) if r[15] else None,
                "linked_garmin_activity_id": r[16],
                # actual_km aus trainings (Roh-Garmin-Daten, kein Faktor)
                "actual_km": actual.get("distance_km"),
                "actual_duration_min": actual.get("duration_min"),
                "actual_avg_hr": actual.get("avg_hr"),
                "training_id": actual.get("training_id"),
            }

            if monday_str not in weeks_dict:
                sunday = monday + timedelta(days=6)
                import calendar
                kw = monday.isocalendar()[1]
                weeks_dict[monday_str] = {
                    "monday": monday.strftime("%d.%m.%Y"),
                    "sunday": sunday.strftime("%d.%m.%Y"),
                    "kw": kw,
                    "is_current": False,
                    "is_past": sunday < today,
                    "sessions": [],
                }
            weeks_dict[monday_str]["sessions"].append(session_obj)

        # is_current markieren: Woche, in der today liegt
        monday_today = today - timedelta(days=today.weekday())
        monday_today_str = str(monday_today)
        if monday_today_str in weeks_dict:
            weeks_dict[monday_today_str]["is_current"] = True
            weeks_dict[monday_today_str]["is_past"] = False

        # Fehlende Tage (DO, SA, etc.) als Rest Day auffüllen
        for monday_str, week_data in weeks_dict.items():
            monday_dt = date.fromisoformat(monday_str)
            existing_dates = {s['date'] for s in week_data['sessions']}
            for i in range(7):
                d = monday_dt + timedelta(days=i)
                d_str = str(d)
                if d_str not in existing_dates:
                    week_data['sessions'].append({
                        "id": None,
                        "date": d_str,
                        "day_number": d.day,
                        "day_short": DAY_SHORT[d.weekday()],
                        "month_short": MONTH_SHORT[d.month],
                        "is_today": d == today,
                        "is_past": d < today,
                        "session_type": "Rest Day",
                        "session_zone": "",
                        "planned_km": None,
                        "planned_duration_min": None,
                        "notes": "",
                        "phase": "",
                        "status": "rest",
                        "sync_status": "",
                        "garmin_workout_id": None,
                        "steps": [],
                        "actual_km": None,
                        "actual_duration_min": None,
                        "actual_avg_hr": None,
                        "training_id": None,
                    })
            # Neu sortieren nach Datum
            week_data['sessions'].sort(key=lambda s: s['date'])

        # Sortiert nach Datum zurückgeben
        result = [weeks_dict[k] for k in sorted(weeks_dict.keys())]
        return jsonify(result)

    except Exception as e:
        return jsonify({"status": "error", "message": str(e), "trace": traceback.format_exc()}), 500


# ─── FRONTEND API: Plan ───────────────────────────────────────────────────────

@app.route('/api/frontend/plan')
def frontend_plan():
    """
    Planansicht: Race-Info, Phase, Belastungskurve (aus plan_weeks), Milestones.
    Fallback auf Heuristik wenn plan_weeks leer.
    """
    try:
        today = get_today()
        monday_today = today - timedelta(days=today.weekday())
        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            SELECT id, name, goal_type, race_name, race_date, race_distance_km,
                   total_weeks, current_week, status,
                   race_elevation_m, race_priority, target_time
            FROM plans WHERE status = 'active' ORDER BY created_at DESC LIMIT 1
        """)
        plan_row = cur.fetchone()
        if not plan_row:
            conn.close()
            return jsonify({"status": "ok", "has_plan": False})

        plan_id       = plan_row[0]
        race_date     = plan_row[4]
        elevation_m   = plan_row[9]
        race_priority = plan_row[10] or "A"
        target_time   = plan_row[11]
        countdown_days = (race_date - today).days if race_date else None

        # plan_weeks (kanonisch)
        cur.execute("""
            SELECT week_number, week_start, phase, is_deload, is_peak, target_run_km, week_focus
            FROM plan_weeks WHERE plan_id = %s ORDER BY week_number
        """, (plan_id,))
        pw_rows = cur.fetchall()

        weeks = []
        current_week = 1
        total_weeks  = 16

        if pw_rows:
            total_weeks = len(pw_rows)
            for r in pw_rows:
                wnum, wstart, phase, is_deload, is_peak, km, focus = r
                is_current = (wstart <= monday_today < wstart + timedelta(weeks=1))
                if is_current:
                    current_week = wnum
                weeks.append({
                    "week_date":   str(wstart),
                    "week_number": wnum,
                    "total_km":    float(km) if km else 0,
                    "phase":       phase or "base",
                    "is_deload":   bool(is_deload),
                    "is_peak":     bool(is_peak),
                    "is_current":  is_current,
                    "week_focus":  focus or "",
                })
        else:
            # Fallback aus training_plan
            cur.execute("""
                SELECT week_date,
                       ROUND(COALESCE(SUM(distance_km), 0)::numeric, 1) AS total_km,
                       MAX(phase) AS phase
                FROM training_plan
                WHERE plan_id = %s
                  AND session_type NOT IN ('Rest Day', 'Strength Training', 'Core', 'Mobility')
                GROUP BY week_date ORDER BY week_date
            """, (plan_id,))
            fb_rows = cur.fetchall()
            total_weeks = max(len(fb_rows), plan_row[6] or 16)
            prev_km = 0.0
            all_kms = [float(r[1]) if r[1] else 0 for r in fb_rows]
            max_km = max(all_kms) if all_kms else 0
            for i, r in enumerate(fb_rows):
                wdate, wkm, phase = r
                wkm = float(wkm) if wkm else 0
                is_deload = (prev_km > 0 and wkm > 0 and wkm / prev_km < 0.75)
                is_peak   = (max_km > 0 and wkm == max_km)
                if wkm > 0:
                    prev_km = wkm
                is_current = (str(wdate) == str(monday_today))
                if is_current:
                    current_week = i + 1
                weeks.append({
                    "week_date":   str(wdate),
                    "week_number": i + 1,
                    "total_km":    wkm,
                    "phase":       phase or "base",
                    "is_deload":   is_deload,
                    "is_peak":     is_peak,
                    "is_current":  is_current,
                    "week_focus":  "",
                })
            if not any(w["is_current"] for w in weeks):
                current_week = plan_row[7] or 1

        current_phase  = weeks[current_week - 1]["phase"] if weeks and 1 <= current_week <= len(weeks) else "base"
        current_focus  = weeks[current_week - 1]["week_focus"] if weeks and 1 <= current_week <= len(weeks) else ""

        # Milestones
        cur.execute("""
            SELECT id, step_number, title, criterion, target_date,
                   status, evidence, notes, achieved_at, week_number
            FROM milestones WHERE plan_id = %s ORDER BY step_number
        """, (plan_id,))
        milestone_rows = cur.fetchall()

        milestones = []
        next_set = False
        for r in milestone_rows:
            ms_status = r[5]
            is_next = (ms_status == 'open' and not next_set)
            if is_next:
                next_set = True
            milestones.append({
                "id": r[0], "step_number": r[1], "title": r[2],
                "criterion": r[3] or "", "target_date": str(r[4]) if r[4] else None,
                "status": ms_status, "is_next": is_next,
                "evidence": r[6] or "", "notes": r[7] or "",
                "achieved_at": str(r[8]) if r[8] else None,
                "week_number": r[9],
            })

        conn.close()

        return jsonify({
            "status": "ok",
            "has_plan": True,
            "race": {
                "name":           plan_row[3],
                "date":           str(race_date) if race_date else None,
                "distance_km":    plan_row[5],
                "elevation_m":    elevation_m,
                "priority":       race_priority,
                "target_time":    target_time,
                "countdown_days": countdown_days,
            },
            "block": {
                "current_week": current_week,
                "total_weeks":  total_weeks,
                "phase":        current_phase,
                "week_focus":   current_focus,
            },
            "weeks":      weeks,
            "milestones": milestones,
        })

    except Exception as e:
        return jsonify({"status": "error", "message": str(e), "trace": traceback.format_exc()}), 500


# ─── FRONTEND API: Sync ───────────────────────────────────────────────────────

@app.route('/api/frontend/sync')
def frontend_sync():
    """
    Sync-Status: Verbindungen, letzte Aktivitäten, letzte Änderungen.
    Keine Rohlogs, keine MCP-Funktionsnamen, keine SQL-Fehler im Response.
    """
    try:
        today = get_today()
        conn = get_db()
        cur = conn.cursor()

        # DB-Status: letzter daily_log Eintrag
        cur.execute("SELECT MAX(date) FROM daily_logs")
        last_health_date = cur.fetchone()[0]

        # Letzte Aktivität
        cur.execute("SELECT MAX(date) FROM trainings")
        last_activity_date = cur.fetchone()[0]

        # Garmin-Session: letzter erfolgreicher Push
        cur.execute("""
            SELECT updated_at FROM garmin_mcp_log
            WHERE status = 'success' ORDER BY updated_at DESC LIMIT 1
        """)
        row = cur.fetchone()
        last_garmin_push = row[0].isoformat() if row else None

        garmin_connected = last_garmin_push is not None

        # Letzte 3 Aktivitäten
        cur.execute("""
            SELECT date, type, notes, distance_km, duration_minutes
            FROM trainings
            ORDER BY date DESC LIMIT 3
        """)
        activity_rows = cur.fetchall()

        MONTH_DE = ['', 'Jan', 'Feb', 'Mär', 'Apr', 'Mai', 'Jun', 'Jul', 'Aug', 'Sep', 'Okt', 'Nov', 'Dez']
        DAY_DE_SHORT = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So']

        activities = []
        for r in activity_rows:
            act_date = r[0]
            if act_date == today:
                date_label = "Heute"
            elif act_date == today - timedelta(days=1):
                date_label = "Gestern"
            else:
                date_label = f"{DAY_DE_SHORT[act_date.weekday()]} {act_date.day}. {MONTH_DE[act_date.month]}"
            activities.append({
                "date": str(act_date),
                "date_label": date_label,
                "session_type": r[1],
                "title": (r[2] or r[1]).split(" | ")[0],
                "km": float(r[3]) if r[3] else None,
                "duration_min": r[4],
            })

        # Letzte Änderungen aus garmin_mcp_log (lesbare Zusammenfassung)
        cur.execute("""
            SELECT workout_name, scheduled_date, action, status,
                   garmin_workout_id, created_at, last_error
            FROM garmin_mcp_log
            ORDER BY created_at DESC LIMIT 10
        """)
        change_rows = cur.fetchall()

        ACTION_LABEL = {
            "push": "Workout erstellt",
            "move": "Workout verschoben",
            "delete": "Workout gelöscht",
            "preview": "Workout vorschau",
        }
        STATUS_LABEL = {
            "success": "synchronisiert",
            "pending": "ausstehend",
            "failed": "Fehler",
        }

        changes = []
        for r in change_rows:
            workout_name = r[0] or "Workout"
            sched_date = r[1]
            action = r[2]
            status = r[3]
            created_at = r[5]

            # Datum relativ
            if created_at:
                delta = today - created_at.date()
                if delta.days == 0:
                    when = f"Heute · {created_at.strftime('%H:%M')}"
                elif delta.days == 1:
                    when = f"Gestern · {created_at.strftime('%H:%M')}"
                else:
                    when = created_at.strftime(f"{delta.days} Tage")
            else:
                when = "—"

            description = ACTION_LABEL.get(action, action)
            if sched_date:
                day_name = DAY_DE_SHORT[sched_date.weekday()]
                description += f" · {day_name} {sched_date.day}. {MONTH_DE[sched_date.month]}"

            changes.append({
                "when": when,
                "created_at": created_at.isoformat() if created_at else None,
                "workout_name": workout_name,
                "description": description,
                "status": status,
                "status_label": STATUS_LABEL.get(status, status),
                "has_error": status == "failed",
                # Fehlertext bleibt generisch – keine technischen Details
                "error_hint": "Push fehlgeschlagen – bitte im Chat nachfragen." if status == "failed" else None,
            })

        conn.close()

        return jsonify({
            "status": "ok",
            "connections": {
                "mcp": {"status": "live", "label": "LIVE"},
                "garmin": {
                    "status": "connected" if garmin_connected else "unknown",
                    "label": "VERBUNDEN" if garmin_connected else "UNBEKANNT",
                    "last_push": last_garmin_push,
                },
                "database": {
                    "status": "current" if last_health_date == today else "stale",
                    "label": "AKTUELL" if last_health_date == today else "VERALTET",
                },
                "last_health_sync": str(last_health_date) if last_health_date else None,
                "last_activity_sync": str(last_activity_date) if last_activity_date else None,
            },
            "activities": activities,
            "changes": changes,
        })

    except Exception as e:
        return jsonify({"status": "error", "message": str(e), "trace": traceback.format_exc()}), 500


if __name__ == '__main__':
    port = int(os.getenv("PORT", 5002))
    app.run(debug=False, host='0.0.0.0', port=port)