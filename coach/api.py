from flask import Flask, jsonify, request
from flask_cors import CORS
import os, psycopg2
from datetime import date
from dotenv import load_dotenv

load_dotenv("/Users/lexshapes/hybrid-fitness-os/.env")

app = Flask(__name__)
CORS(app)

def get_db():
    return psycopg2.connect(os.getenv("RAILWAY_DATABASE_URL"))

@app.route('/api/checkin', methods=['POST'])
def save_checkin():
    data = request.get_json()
    feel = data.get('feel', '')
    notes = ', '.join(data.get('notes', []))
    text = data.get('text', '')
    today = date.today()

    conn = get_db()
    cur = conn.cursor()

    # Eintrag für heute — falls schon vorhanden updaten
    cur.execute("SELECT id FROM daily_logs WHERE date = %s", (today,))
    existing = cur.fetchone()

    if existing:
        cur.execute("""
            UPDATE daily_logs SET feel=%s, notes=%s, athlete_text=%s
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
        import sys
        sys.path.insert(0, '/Users/lexshapes/hybrid-fitness-os')
        from coach.morning_brief import generate_morning_brief

        today = date.today()
        conn = get_db()
        cur = conn.cursor()

        # Feedback von heute holen
        cur.execute("""
            SELECT feel, notes, athlete_text, morning_brief
            FROM daily_logs WHERE date = %s
        """, (today,))
        row = cur.fetchone()

        athlete_feedback = {}
        if row:
            # Wenn Brief heute schon generiert — direkt zurückgeben
            if row[3]:
                conn.close()
                return jsonify({"status": "ok", "brief": row[3]})
            athlete_feedback = {
                'feel': row[0] or '',
                'notes': row[1].split(', ') if row[1] else [],
                'text': row[2] or ''
            }

        # Brief generieren
        brief = generate_morning_brief(athlete_feedback=athlete_feedback)

        # Brief in DB speichern
        if row:
            cur.execute("UPDATE daily_logs SET morning_brief=%s WHERE date=%s", (brief, today))
        else:
            cur.execute("INSERT INTO daily_logs (date, morning_brief) VALUES (%s, %s)", (today, brief))

        conn.commit()
        conn.close()

        return jsonify({"status": "ok", "brief": brief})

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({"status": "ok", "date": str(date.today())})

if __name__ == '__main__':
    port = int(os.getenv("PORT", 5002))
    app.run(debug=False, host='0.0.0.0', port=port)