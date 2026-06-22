import anthropic
import os
import json
from dotenv import load_dotenv

load_dotenv()

from data.data_service import get_daily_snapshot

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
    prompt += '{"brief": "Morning Brief hier", "suggestion": "Vorschlag hier"}\n\n'
    prompt += "brief: Vollstaendiger Morning Brief, max 150 Woerter, kurze Saetze, auf Deutsch. "
    prompt += "Niemals: Algorithmus, Score, Metrik. Immer: Ich sehe, Ich wuerde, Dein Koerper. "
    prompt += "Bezieht sich auf Schlaf, Erholung und was der Athlet selbst gesagt hat.\n"
    prompt += "suggestion: Ein einziger konkreter Vorschlag fuer heute, max 12 Woerter, kein Punkt am Ende."

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = message.content[0].text.strip()

    try:
        result = json.loads(raw)
        return result
    except json.JSONDecodeError:
        return {
            "brief": raw,
            "suggestion": "Heute ruhig bleiben - Erholung ist Training"
        }

if __name__ == "__main__":
    print("=== CAIRN Morning Brief ===\n")
    result = generate_morning_brief()
    print("Brief:", result.get("brief"))
    print("\nSuggestion:", result.get("suggestion"))