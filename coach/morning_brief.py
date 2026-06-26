import anthropic
import os
import json
from datetime import date, timedelta
from dotenv import load_dotenv

load_dotenv()

from data.data_service import get_daily_snapshot

TRAINING_CATALOGUE = {
    "Rest Day":        {"zone": "",          "primary": "none",       "secondary": "none"},
    "Recovery Run":    {"zone": "Z1",        "primary": "heart_rate", "secondary": "none"},
    "Easy Run":        {"zone": "Z2",        "primary": "heart_rate", "secondary": "pace"},
    "Long Run":        {"zone": "Z2",        "primary": "heart_rate", "secondary": "pace"},
    "Progression Run": {"zone": "Z2-Z4",     "primary": "pace",       "secondary": "heart_rate"},
    "Tempo Run":       {"zone": "Z3-Z4",     "primary": "pace",       "secondary": "heart_rate"},
    "Threshold Run":   {"zone": "Z4",        "primary": "pace",       "secondary": "heart_rate"},
    "Intervalle":      {"zone": "Z5",        "primary": "pace",       "secondary": "none"},
    "Hill Repeats":    {"zone": "Z4-Z5",     "primary": "rpe",        "secondary": "pace"},
    "Strides":         {"zone": "Z5",        "primary": "rpe",        "secondary": "none"},
    "Race Pace Run":   {"zone": "Wettkampf", "primary": "pace",       "secondary": "heart_rate"},
    "Trail Run":       {"zone": "Z2-Z3",     "primary": "rpe",        "secondary": "none"},
    "Krafttraining":   {"zone": "",          "primary": "none",       "secondary": "none"},
    "Mobilität":       {"zone": "",          "primary": "none",       "secondary": "none"},
}

DAY_NAMES_DE = {
    0: "Montag", 1: "Dienstag", 2: "Mittwoch", 3: "Donnerstag",
    4: "Freitag", 5: "Samstag", 6: "Sonntag"
}

def generate_morning_brief(athlete_feedback: dict = None):
    snapshot = get_daily_snapshot()

    sleep = snapshot.get("sleep", {})
    hrv = snapshot.get("hrv", {})
    bb = snapshot.get("body_battery", {})
    rhr = snapshot.get("resting_hr", {})
    activities = snapshot.get("recent_activities", [])

    # Datum-Kontext für den Coach
    try:
        import pytz
        from datetime import datetime
        zurich = pytz.timezone('Europe/Zurich')
        today = datetime.now(zurich).date()
    except Exception:
        from datetime import datetime
        today = (datetime.utcnow() + timedelta(hours=2)).date()

    yesterday = today - timedelta(days=1)
    day_before = today - timedelta(days=2)

    today_str = f"{DAY_NAMES_DE[today.weekday()]}, {today.strftime('%d.%m.%Y')}"
    yesterday_str = yesterday.strftime('%d.%m.%Y')
    day_before_str = day_before.strftime('%d.%m.%Y')

    # Aktivitäten mit relativem Datum beschriften
    recent = []
    for a in activities[:5]:
        act_date_str = str(a.get('date', ''))
        try:
            act_date = date.fromisoformat(act_date_str)
            if act_date == today:
                rel = "heute"
            elif act_date == yesterday:
                rel = "gestern"
            elif act_date == day_before:
                rel = "vorgestern"
            else:
                diff = (today - act_date).days
                rel = f"vor {diff} Tagen ({act_date_str})"
        except Exception:
            rel = act_date_str

        line = f"- {rel} | {a.get('type')} | {a.get('name')} | {a.get('distance_km')}km | {a.get('duration_min')}min | HF {a.get('avg_hr')} bpm"
        recent.append(line)
    recent_text = "\n".join(recent)

    feedback_text = ""
    feel_score = 5
    if athlete_feedback:
        feel = athlete_feedback.get('feel', '5')
        notes = athlete_feedback.get('notes', [])
        text = athlete_feedback.get('text', '')
        try:
            feel_score = int(feel)
        except Exception:
            feel_score = 5
        feedback_text += f"Gefuehl (1-10): {feel_score}\n"
        if notes:
            feedback_text += "Notizen: " + ', '.join(notes) + "\n"
        if text:
            feedback_text += f"Eigene Worte: \"{text}\"\n"

    session_types = ", ".join(TRAINING_CATALOGUE.keys())

    prompt = "Du bist CAIRN - ein erfahrener Ausdauer-Coach. Du sprichst wie ein ruhiger Bergfuehrer. Nie wie Software.\n\n"
    prompt += f"Heutiges Datum: {today_str}\n\n"
    prompt += "Athlet-Daten von heute Morgen:\n"
    prompt += f"- Schlaf: {sleep.get('duration_h')}h | Score: {sleep.get('score')} | Tief: {sleep.get('deep_h')}h | REM: {sleep.get('rem_h')}h\n"
    prompt += f"- HRV: {hrv.get('hrv_last_night')} ms | Status: {hrv.get('status')} | 5-Tage-Avg: {hrv.get('hrv_5day_avg')}\n"
    prompt += f"- Body Battery: +{bb.get('charged')} / -{bb.get('drained')}\n"
    prompt += f"- Ruhepuls: {rhr.get('rhr')} bpm\n\n"
    prompt += "Letzte Aktivitaeten (mit relativem Datum):\n" + recent_text + "\n\n"
    if feedback_text:
        prompt += "Athlet-Feedback:\n" + feedback_text + "\n"

    prompt += """WICHTIGE COACHING-REGEL:
Wenn der Athlet sich gut fuehlt (Gefuehl 7-10) UND keine klaren Warnsignale vorhanden sind → halte am Plan fest. Kein Vorschlag noetig.
Nur wenn Gefuehl niedrig (1-5) ODER klare Warnsignale (HRV stark gesunken, Schlaf schlecht, eigene Worte zeigen Erschoepfung) → mache einen konkreten Anpassungsvorschlag.
Wenn alles in Ordnung ist: suggestion = "" (leer lassen).

WICHTIG fuer den Brief:
- Verwende die relativen Zeitangaben (gestern, heute, vorgestern) korrekt basierend auf den Aktivitaetsdaten.
- Sprich konkret ueber die Aktivitaeten mit den richtigen Zeitangaben.
- Nie: Algorithmus, Score, Metrik, Regelverstoß, Session.
- Immer: Ich sehe, Ich wuerde, Dein Koerper.
- Max 150 Woerter, kurze Saetze.

Antworte NUR mit diesem JSON. Kein Text davor oder danach. Kein Markdown:
{"brief": "...", "suggestion": "", "session_type": "...", "replan_needed": false}

session_type: Einer dieser Typen: """ + session_types + """
replan_needed: true nur wenn klare Warnsignale oder Athlet fuehlt sich schlecht (1-5). Sonst false."""

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = message.content[0].text.strip()

    try:
        result = json.loads(raw)
        session_type = result.get("session_type", "")
        if session_type in TRAINING_CATALOGUE:
            cat = TRAINING_CATALOGUE[session_type]
            result["session_zone"] = cat["zone"]
            result["primary_target"] = cat["primary"]
            result["secondary_target"] = cat["secondary"]
        else:
            result["session_zone"] = ""
            result["primary_target"] = "none"
            result["secondary_target"] = "none"

        if not result.get("replan_needed", False):
            result["suggestion"] = ""

        return result
    except json.JSONDecodeError:
        return {
            "brief": raw,
            "suggestion": "",
            "session_type": "Easy Run",
            "session_zone": "Z2",
            "primary_target": "heart_rate",
            "secondary_target": "none",
            "replan_needed": False
        }

if __name__ == "__main__":
    print("=== CAIRN Morning Brief ===\n")
    result = generate_morning_brief()
    print(json.dumps(result, indent=2, ensure_ascii=False))