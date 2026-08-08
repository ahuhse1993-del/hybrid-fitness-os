from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
import os, psycopg2, json, threading, time, traceback
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

# ─── PLAN GENERIEREN ───
plan_jobs = {}

@app.route('/api/plan/job/<job_id>', methods=['GET'])
def get_plan_job(job_id):
    job = plan_jobs.get(job_id, {'status': 'unknown'})
    return jsonify(job)

@app.route('/api/plan/generate', methods=['POST'])
def generate_plan():
    try:
        data = request.get_json(force=True)
        job_id = str(int(time.time()))
        plan_jobs[job_id] = {'status': 'running'}
        t = threading.Thread(target=run_plan_job, args=(data, job_id))
        t.daemon = True
        t.start()
        return jsonify({'status': 'ok', 'job_id': job_id})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

def run_plan_job(data, job_id):
    with app.app_context():
        try:
            generate_plan_internal(data)
            plan_jobs[job_id] = {'status': 'done'}
        except Exception as e:
            plan_jobs[job_id] = {'status': 'error', 'message': str(e), 'trace': traceback.format_exc()}

def generate_plan_internal(data):
    try:
        import anthropic
        data = data

        goal_type = data.get('goal_type', 'race')
        race_type = data.get('race_type', '')
        race_name = data.get('race_name', '')
        race_date = data.get('race_date', '')
        race_distance_km = data.get('race_distance_km', 0)
        gpx_data = data.get('gpx_data', None)  # GPX-Analyse Ergebnis
        days_per_week = data.get('days_per_week', 5)
        long_run_day = data.get('long_run_day', 6)
        quality_sessions = data.get('quality_sessions', 1)
        strength_sessions = data.get('strength_sessions', 2)
        strength_days = data.get('strength_days', [])
        week_structure = data.get('week_structure', None)
        total_weeks = data.get('total_weeks', 16)
        phases = data.get('phases', [])
        start_date = data.get('start_date', None)
        cross_training = data.get('cross_training', False)
        cross_training_types = data.get('cross_training_types', [])
        cross_training_days = data.get('cross_training_days', 0)

        # Startdatum berechnen
        today = get_today()
        if start_date:
            try:
                start_day = date.fromisoformat(start_date)
                # Montag der Startwoche
                start_monday = start_day - timedelta(days=start_day.weekday())
                # Startdatum merken um erste Woche zu trimmen
                actual_start_day = start_day.isoweekday()  # 1=Mo, 7=So
            except Exception:
                start_monday = today
                actual_start_day = 1
        else:
            start_monday = today
            actual_start_day = 1
        day_names = ['', 'Montag', 'Dienstag', 'Mittwoch', 'Donnerstag', 'Freitag', 'Samstag', 'Sonntag']

        # Wochen in zwei Hälften aufteilen um Timeout zu vermeiden
        half = total_weeks // 2
        week_ranges = [(1, half), (half + 1, total_weeks)] if total_weeks > 10 else [(1, total_weeks)]

        all_weeks = []

        # CAIRN-Routinen: offizielle Strength-Training-Vorlagen aus der cairn_routines Tabelle
        # (dort von data/hevy_routines_sync.py per GitHub Action alle 2 Tage aus Hevy synchronisiert)
        HEVY_CATEGORIES = {
            'Upper Body CAIRN': 'oberkörper',
            'Lower Body + Arms CAIRN': 'unterkörper',
            'Full Body Light CAIRN': 'full_body_light',
        }

        hevy_context = ""
        routine_by_category = {}
        try:
            hevy_conn = get_db()
            hevy_cur = hevy_conn.cursor()
            hevy_cur.execute("SELECT title, exercises FROM cairn_routines")
            rows = hevy_cur.fetchall()
            hevy_conn.close()

            cairn_routines = {}
            for title, exercises in rows:
                title = (title or '').strip()
                if not title or title in cairn_routines:
                    continue  # Duplikat ignorieren
                cairn_routines[title] = exercises or []

            if cairn_routines:
                hevy_lines = ["STRENGTH TRAINING: Verwende NUR diese Workout-Namen (exakt so wie hier geschrieben):"]
                for title, exercises in cairn_routines.items():
                    category = HEVY_CATEGORIES.get(title, 'Ganzkörper')
                    routine_by_category.setdefault(category, title)
                    hevy_lines.append(f"- {title} [{category}]: {', '.join(exercises[:4])}")
                hevy_lines.append("Jeder andere Strength Training Name ist VERBOTEN.")

                if routine_by_category.get('oberkörper') and routine_by_category.get('unterkörper'):
                    hevy_lines.append(f"Normale Wochen: {routine_by_category['oberkörper']} und {routine_by_category['unterkörper']} abwechselnd")
                if routine_by_category.get('full_body_light'):
                    hevy_lines.append(f"Deload/Taper: {routine_by_category['full_body_light']}")

                hevy_context = "\n" + "\n".join(hevy_lines) + "\n"
        except Exception as hevy_err:
            print(f"Hevy Routinen Fehler: {hevy_err}")

        oberkoerper_routine = routine_by_category.get('oberkörper')
        unterkoerper_routine = routine_by_category.get('unterkörper')
        full_body_routine = routine_by_category.get('full_body_light')

        cross_training_context = ""
        if cross_training:
            cross_training_context = f"""
CROSS TRAINING: {cross_training_days}x pro Woche — Typen: {', '.join(cross_training_types) if cross_training_types else 'flexibel'}. Nutze session_type='Cross Training' mit notes=Typ (z.B. 'Rennrad 60 min').
"""

        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


        for (week_from, week_to) in week_ranges:
            weeks_in_range = week_to - week_from + 1

            # Phase für diesen Block bestimmen
            phase_context = []
            for ph in phases:
                phase_context.append(f"{ph.get('name','').upper()}: {ph.get('weeks',0)} Wochen")

            gpx_context = ""
            if gpx_data:
                gpx_context = f"""
STRECKENPROFIL (GPX-Analyse):
- Distanz: {gpx_data.get('distance_km')} km
- Höhenmeter aufwärts: {gpx_data.get('elevation_gain_m')} m
- Höhenmeter pro km: {gpx_data.get('gain_per_km')} m/km
- Profil: {gpx_data.get('profile_de')}
- Max. Steigung: {gpx_data.get('max_grade_pct')} %
"""

            prompt = f"""Du bist CAIRN Coach. Erstelle Woche {week_from} bis {week_to} eines {total_weeks}-Wochen Trainingsplans.

ATHLETENPROFIL:
- Ziel: {goal_type}
- Rennen: {race_name} ({race_type}) · {race_distance_km if race_distance_km else '?'} km
- Renndatum: {race_date}
- Gesamtplan: {total_weeks} Wochen · Phasen: {', '.join(phase_context)}

WOCHENSTRUKTUR — GENAU {days_per_week} Sessions pro Woche:
- {strength_sessions}x Strength Training — NUR an: {', '.join([['','Mo','Di','Mi','Do','Fr','Sa','So'][d] for d in strength_days]) if strength_days else 'flexibel'}
- 1x Long Run — IMMER an {day_names[long_run_day]} (Tag {long_run_day})
- {quality_sessions}x Quality (Tempo Session / Interval Session / Sprint Session / Hill Session)
- {days_per_week - strength_sessions - 1 - quality_sessions}x Easy Run oder Trail Run
- {7 - days_per_week}x Rest Day — diese Tage komplett leer lassen, KEIN Eintrag
{gpx_context}
{hevy_context}
{cross_training_context}
REGELN:
1. Long Run IMMER an Tag {long_run_day} ({day_names[long_run_day]})
2. Nie 2 harte Sessions direkt hintereinander
3. Nach Long Run: Rest Day oder Easy Run
4. Strength Training nicht direkt vor Quality Session
5. Deload alle 4 Wochen (Volumen -20%)
6. Trail Run = RPE-basiert, keine Pace

ERLAUBTE SESSION-TYPEN — NUR diese 14, exakt so geschrieben (kein anderer Wert erlaubt):
Easy Run, Recovery Run, Long Run, Tempo Session, Interval Session, Sprint Session, Hill Session, Trail Run, Cross Training, Strength Training, Mobility, Rest Day, Time Trial, Race Day

Strength Training hat KEINE eigenen session_type-Unterkategorien. Oberkörper A/B, Unterkörper A/B oder Full Body gehören ausschließlich ins notes-Feld, z.B. session_type: "Strength Training", notes: "Oberkörper A".

WICHTIG: Antworte NUR mit JSON. Kein Text davor oder danach. Kein plan_meta. Beginne direkt mit {{
{{"weeks": [{{"week_number": {week_from}, "phase": "base", "total_km": 40, "sessions": [{{"day_of_week": 1, "session_type": "Easy Run", "session_zone": "Z1-Z2", "distance_km": 8, "duration_min": 55, "notes": "Easy Z1-Z2 RPE 1-3"}}]}}]}}

Wochen {week_from} bis {week_to}. day_of_week: 1=Mo bis 7=So. Rest Days nicht eintragen. Genau {days_per_week} Sessions pro Woche."""

            message = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=8000,
                messages=[{"role": "user", "content": prompt}]
            )

            # Text aus allen Content-Blöcken zusammensetzen (inkl. nach Web Search)
            raw = ""
            for block in message.content:
                if hasattr(block, 'text'):
                    raw += block.text
            raw = raw.replace('```json', '').replace('```', '').strip()
            # JSON aus dem Text extrahieren - suche nach { "weeks": [
            if not raw.startswith('{'):
                import re
                json_match = re.search(r'\{[\s\S]*"weeks"[\s\S]*\}', raw)
                if json_match:
                    raw = json_match.group(0)
            try:
                part_json = json.loads(raw)
                all_weeks.extend(part_json.get('weeks', []))
                print(f"OK weeks {week_from}-{week_to}: {len(part_json.get('weeks', []))} weeks")
            except Exception as parse_err:
                print(f"JSON parse error for weeks {week_from}-{week_to}: {parse_err}")
                print(f"Raw length: {len(raw)}")
                print(f"Raw start: {raw[:500]}")
                print(f"Stop reason: {message.stop_reason}")
                print(f"Content blocks: {len(message.content)}")
                for i, block in enumerate(message.content):
                    print(f"  Block {i}: type={getattr(block, 'type', 'unknown')}, has_text={hasattr(block, 'text')}")
                continue

        plan_json = {"weeks": all_weeks}

        # In DB speichern
        conn = get_db()
        cur = conn.cursor()

        # Plan-Metadaten
        cur.execute("""
            INSERT INTO plans (name, goal_type, race_name, race_date, race_distance_km,
                total_weeks, days_per_week, long_run_day, quality_sessions, strength_sessions, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'active')
            RETURNING id
        """, (
            race_name or f"{goal_type} Plan {total_weeks}W",
            goal_type, race_name,
            race_date if race_date else None,
            race_distance_km or 0,
            total_weeks, days_per_week, long_run_day,
            quality_sessions, strength_sessions
        ))
        plan_id = cur.fetchone()[0]

        # Alten Plan archivieren
        cur.execute("UPDATE plans SET status='archived' WHERE status='active' AND id != %s", (plan_id,))

        # Alten training_plan löschen
        cur.execute("DELETE FROM training_plan WHERE plan_id = %s OR plan_id IS NULL", (plan_id,))

        # Sessions eintragen
        sessions_inserted = 0
        strength_counter_per_week = {}
        for week in plan_json.get('weeks', []):
            week_num = week.get('week_number', 1)
            phase = week.get('phase', 'base')
            week_monday = start_monday + timedelta(weeks=week_num - 1)

            for session in week.get('sessions', []):
                day_of_week = session.get('day_of_week', 1)
                # Erste Woche: Sessions vor dem Startdatum überspringen
                if week_num == 1 and day_of_week < actual_start_day:
                    continue
                # CAIRN Routine Namen direkt beim Insert setzen
                session_type_val = session.get('session_type', 'Easy Run')
                notes_val = session.get('notes', '')
                if session_type_val == 'Strength Training':
                    phase_lower = phase.lower()
                    if 'taper' in phase_lower or 'deload' in phase_lower:
                        if full_body_routine:
                            notes_val = full_body_routine
                    else:
                        week_key = week_num
                        strength_counter_per_week[week_key] = strength_counter_per_week.get(week_key, 0) + 1
                        if strength_counter_per_week[week_key] % 2 == 1:
                            if unterkoerper_routine:
                                notes_val = unterkoerper_routine
                        else:
                            if oberkoerper_routine:
                                notes_val = oberkoerper_routine

                cur.execute("""
                    INSERT INTO training_plan
                    (week_date, day_of_week, session_type, session_zone,
                     duration_min, distance_km, notes, phase, plan_id, plan_week)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    week_monday,
                    day_of_week,
                    session_type_val,
                    session.get('session_zone', ''),
                    session.get('duration_min', 0),
                    session.get('distance_km', 0),
                    notes_val,
                    phase,
                    plan_id,
                    week_num
                ))
                sessions_inserted += 1

        conn.commit()
        conn.close()

        # ─── Workout-Vorschläge: Coach vergleicht geplante Strength-Sessions mit den CAIRN-Routinen ───
        try:
            strength_notes = []
            for week in plan_json.get('weeks', []):
                for session in week.get('sessions', []):
                    if session.get('session_type') == 'Strength Training':
                        note = session.get('notes', '') or 'Strength Training'
                        if note not in strength_notes:
                            strength_notes.append(note)

            if strength_notes and hevy_context:
                # Athletenprofil laden — Langzeitziele haben Vorrang vor generischen Übungsempfehlungen
                profile_conn = get_db()
                profile_cur = profile_conn.cursor()
                profile_cur.execute("""
                    SELECT long_term_goals, cross_rennrad, cross_schwimmen, cross_wandern, cross_ski
                    FROM athlete_profile ORDER BY id LIMIT 1
                """)
                profile_row = profile_cur.fetchone()
                profile_conn.close()

                long_term_goals = (profile_row[0] if profile_row else '') or 'keine angegeben'
                cross_prefs = []
                if profile_row:
                    if profile_row[1]: cross_prefs.append('Rennrad')
                    if profile_row[2]: cross_prefs.append('Schwimmen')
                    if profile_row[3]: cross_prefs.append('Wandern')
                    if profile_row[4]: cross_prefs.append('Ski')

                athlete_profile_context = f"ATHLETENPROFIL LANGZEITZIELE: {long_term_goals}"
                if cross_prefs:
                    athlete_profile_context += f"\nCROSS TRAINING PRÄFERENZEN: {', '.join(cross_prefs)}"

                suggestion_prompt = f"""Du bist CAIRN Coach. Du hast gerade einen neuen Trainingsplan erstellt.

{athlete_profile_context}

GEPLANTE STRENGTH-TRAINING-EINHEITEN IN DIESEM PLAN:
{chr(10).join('- ' + n for n in strength_notes)}

{hevy_context}

AUFGABE:
Vergleiche die geplanten Einheiten mit den offiziellen CAIRN-Routinen und deren Übungsauswahl.
Wo sinnvoll: schlage konkrete Anpassungen vor — Übungen ergänzen, streichen oder anpassen.
Nur wenn es wirklich etwas zu verbessern gibt, nicht erzwingen. Maximal 4 Vorschläge. Wenn nichts zu verbessern ist: leere Liste.
Berücksichtige die Langzeitziele des Athleten. Wenn Optik oder ästhetische Ziele genannt sind, haben diese Vorrang vor reinem Functional Training. Schlage nur Änderungen vor die WIRKLICH fehlen — prüfe die Übungsliste sorgfältig bevor du etwas vorschlägst.

Für jeden Vorschlag:
- workout_name: exakt einer der oben genannten geplanten Einheiten-Namen
- change: was konkret ändern (1 Satz, mit konkreten Übungsnamen)
- reason: warum (1-2 Sätze, CAIRN-Ton — ruhig, direkt, wie ein erfahrener Bergführer, nie wie Software)

Antworte NUR mit JSON:
{{"suggestions": [{{"workout_name": "...", "change": "...", "reason": "..."}}]}}"""

                sugg_message = client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=1200,
                    messages=[{"role": "user", "content": suggestion_prompt}]
                )
                sugg_raw = ""
                for block in sugg_message.content:
                    if hasattr(block, 'text'):
                        sugg_raw += block.text
                sugg_raw = sugg_raw.replace('```json', '').replace('```', '').strip()
                if not sugg_raw.startswith('{'):
                    import re
                    m = re.search(r'\{[\s\S]*"suggestions"[\s\S]*\}', sugg_raw)
                    if m:
                        sugg_raw = m.group(0)

                raw_suggestions = json.loads(sugg_raw).get('suggestions', [])
                now_iso = datetime.utcnow().isoformat()
                workout_suggestions = [
                    {
                        "id": f"{plan_id}-{i + 1}",
                        "workout_name": s.get('workout_name', ''),
                        "change": s.get('change', ''),
                        "reason": s.get('reason', ''),
                        "status": "pending",
                        "comment": None,
                        "created_at": now_iso,
                        "responded_at": None,
                    }
                    for i, s in enumerate(raw_suggestions)
                ]

                if workout_suggestions:
                    from psycopg2.extras import Json
                    sugg_conn = get_db()
                    sugg_cur = sugg_conn.cursor()
                    sugg_cur.execute(
                        "UPDATE plans SET workout_suggestions = %s WHERE id = %s",
                        (Json(workout_suggestions), plan_id)
                    )
                    sugg_conn.commit()
                    sugg_conn.close()
        except Exception as sugg_err:
            print(f"Workout-Vorschläge Fehler: {sugg_err}")

        return jsonify({
            "status": "ok",
            "plan_id": plan_id,
            "weeks": len(plan_json.get('weeks', [])),
            "sessions": sessions_inserted
        })

    except Exception as e:
        return jsonify({"status": "error", "message": str(e), "trace": traceback.format_exc()}), 500

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

        system = """Du bist CAIRN, ein erfahrener Endurance Coach.
Sprich wie ein ruhiger, erfahrener Bergführer. Nie wie Software.
Kurze Sätze. Direkt. Menschlich. Auf Augenhöhe.
Nie: 'Readiness Score', 'approved', 'freigegeben', 'Algorithmus'.
Immer: Beobachtung, Einordnung, klare Empfehlung.

""" + page_context.get(page, '') + workout_suggestions_context

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

if __name__ == '__main__':
    port = int(os.getenv("PORT", 5002))
    app.run(debug=False, host='0.0.0.0', port=port)