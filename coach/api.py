from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
import os, psycopg2, json
from datetime import date
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
            morning_brief=NULL, suggestion=NULL
            WHERE date=%s
        """, (feel, notes, text, today))
    else:
        cur.execute("""
            INSERT INTO daily_logs (date, feel, notes, athlete_text)
            VALUES (%s, %s, %s, %s)
        """, (today, feel, notes, text))

    conn.commit()
    conn.close()
    print(f"Check-in gespeichert: {feel} | {notes} | {text[:50]}")
    return jsonify({"status": "ok"})

@app.route('/api/morning-brief', methods=['GET'])
def morning_brief():
    try:
        from coach.morning_brief import generate_morning_brief

        today = date.today()
        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            SELECT feel, notes, athlete_text, morning_brief, suggestion
            FROM daily_logs WHERE date = %s
        """, (today,))
        row = cur.fetchone()

        athlete_feedback = {}
        if row:
            if row[3] and row[4]:
                conn.close()
                return jsonify({
                    "status": "ok",
                    "brief": row[3],
                    "suggestion": row[4]
                })
            athlete_feedback = {
                'feel': row[0] or '',
                'notes': row[1].split(', ') if row[1] else [],
                'text': row[2] or ''
            }

        result = generate_morning_brief(athlete_feedback=athlete_feedback)
        brief = result.get("brief", "")
        suggestion = result.get("suggestion", "")

        if row:
            cur.execute("""
                UPDATE daily_logs SET morning_brief=%s, suggestion=%s
                WHERE date=%s
            """, (brief, suggestion, today))
        else:
            cur.execute("""
                INSERT INTO daily_logs (date, morning_brief, suggestion)
                VALUES (%s, %s, %s)
            """, (today, brief, suggestion))

        conn.commit()
        conn.close()

        return jsonify({"status": "ok", "brief": brief, "suggestion": suggestion})

    except Exception as e:
        import traceback
        return jsonify({"status": "error", "message": str(e), "trace": traceback.format_exc()}), 500

@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({"status": "ok", "date": str(date.today())})

if __name__ == '__main__':
    port = int(os.getenv("PORT", 5002))
    app.run(debug=False, host='0.0.0.0', port=port)