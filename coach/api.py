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

@app.route('/api/plan', methods=['GET'])
def get_plan():
    try:
        conn = get_db()
        cur = conn.cursor()

        today = date.today()
        monday = today - timedelta(days=today.weekday())

        cur.execute("""
            SELECT id, week_date, day_of_week, session_type, session_zone,
                   duration_min, distance_km, notes
            FROM training_plan
            WHERE week_date >= %s AND week_date < %s
            ORDER BY week_date, day_of_week
        """, (monday, monday + timedelta(weeks=4)))

        rows = cur.fetchall()
        conn.close()

        plan = []
        for r in rows:
            plan.append({
                "id": r[0],
                "week_date": str(r[1]),
                "day_of_week": r[2],
                "session_type": r[3],
                "session_zone": r[4],
                "duration_min": r[5],
                "distance_km": float(r[6]) if r[6] else 0,
                "notes": r[7] or ""
            })

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
        prompt += 'Antworte NUR mit JSON, kein Markdown:\n'
        prompt += '{"ok": true, "message": "Kurzes Feedback auf Deutsch"}\n\n'
        prompt += 'WICHTIG fuer die message:\n'
        prompt += 'Sprich wie ein erfahrener Bergfuehrer. Ruhig, direkt, menschlich.\n'
        prompt += 'Nie: Regelverstoß, Session, Parameter, Algorithmus.\n'
        prompt += 'Immer kurze Saetze. Max 2 Saetze. Auf Deutsch.\n'
        prompt += 'Beispiel ok=false: "Nach einem Beintraining brauchen die Muskeln Zeit. Ich wuerde die Intervalle einen Tag verschieben."\n'
        prompt += 'Beispiel ok=true: "Die Woche sieht gut aus. Ich wuerde nichts aendern."'
        

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

@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({"status": "ok", "date": str(date.today())})

if __name__ == '__main__':
    port = int(os.getenv("PORT", 5002))
    app.run(debug=False, host='0.0.0.0', port=port)