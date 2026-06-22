import anthropic
import os
import json
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

def generate_morning_brief(athlete_feedback: dict = None):
    snapshot = get_daily_snapshot()

    sleep = snapshot.get("sleep", {})
    hrv = snapshot.get("hrv", {})
    bb = snapshot.get("body_battery", {})
    rhr = snapshot.get("resting_hr", {})
    activities = snapshot.get("recent_activities", [])

    recent = []
    for a in activities[:3]:
        line = "- " + str(a.get('date')) + " | " + str(a.get('type')) + " | " + str(a.get('name')) + " | " + str(a.get('distance_km')) + "km | " + str(a.get('duration_min')) + "min | HF " + str(a.get('avg_hr')) + " bpm"
        recent.append(line)
    recent_text = "\n".join(recent)

    feedback_text = ""
    if athlete_feedback:
        feel = athlete_feedback.get('feel', '')
        notes = athlete_feedback.get('notes', [])
        text = athlete_feedback.get('text', '')
        if feel:
            feedback_text += "Gefuehl: " + feel + "\n"
        if notes:
            feedback_text += "Notizen: " + ', '.join(notes) + "\n"
        if text:
            feedback_text += "Eigene Worte: \"" + text + "\"\n"

    session_types = ", ".join(TRAINING_CATALOGUE.keys())

    prompt = "Du bist CAIRN - ein erfahrener Ausdauer-Coach. Du sprichst wie ein ruhiger Bergfuehrer. Nie wie Software.\n\n"
    prompt += "Athlet-Daten von heute Morgen:\n"
    prompt += "- Schlaf: " + str(sleep.get('duration_h')) + "h | Score: " + str(sleep.get('score')) + " | Tief: " + str(sleep.get('deep_h')) + "h | REM: " + str(sleep.get('rem_h')) + "h\n"
    prompt += "- HRV: " + str(hrv.get('hrv_last_night')) + " ms | Status: " + str(hrv.get('status')) + " | 5-Tage-Avg: " + str(hrv.get('hrv_5day_avg')) + "\n"
    prompt += "- Body Battery: +" + str(bb.get('charged')) + " / -" + str(bb.get('drained')) + "\n"
    prompt += "- Ruhepuls: " + str(rhr.get('rhr')) + " bpm\n\n"
    prompt += "Letzte Aktivitaeten:\n" + recent_text + "\n\n"
    if feedback_text:
        prompt += "Athlet sagt heute Morgen:\n" + feedback_text + "\n"
    prompt += "Antworte NUR mit diesem JSON. Kein Text davor oder danach. Kein Markdown:\n"
    prompt += '{"brief": "...", "suggestion": "...", "session_type": "..."}\n\n'
    prompt += "brief: Vollstaendiger Morning Brief, max 150 Woerter, kurze Saetze, auf Deutsch.\n"
    prompt += "Niemals: Algorithmus, Score, Metrik. Immer: Ich sehe, Ich wuerde, Dein Koerper.\n"
    prompt += "suggestion: Ein einziger konkreter Vorschlag fuer heute, max 12 Woerter, kein Punkt am Ende.\n"
    prompt += "session_type: Genau einer dieser Typen: " + session_types + "\n"

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
        return result
    except json.JSONDecodeError:
        return {
            "brief": raw,
            "suggestion": "Heute ruhig bleiben",
            "session_type": "Recovery Run",
            "session_zone": "Z1",
            "primary_target": "heart_rate",
            "secondary_target": "none"
        }

if __name__ == "__main__":
    print("=== CAIRN Morning Brief ===\n")
    result = generate_morning_brief()
    print(json.dumps(result, indent=2, ensure_ascii=False))