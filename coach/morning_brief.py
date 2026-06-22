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
        line = f"- {a.get('date')} | {a.get('type')} | {a.get('name')} | {a.get('distance_km')}km | {a.get('duration_min')}min | HF {a.get('avg_hr')} bpm"
        recent.append(line)
    recent_text = "\n".join(recent)

    feedback_text = ""
    if athlete_feedback:
        feel = athlete_feedback.get('feel', '')
        notes = athlete_feedback.get('notes', [])
        text = athlete_feedback.get('text', '')
        if feel:
            feedback_text += f"Gefühl: {feel}\n"
        if notes:
            feedback_text += f"Notizen: {', '.join(notes)}\n"
        if text:
            feedback_text += f"Eigene Worte: \"{text}\"\n"

    prompt = f"""Du bist CAIRN — ein erfahrener Ausdauer-Coach. Du sprichst wie ein ruhiger Bergführer. Nie wie Software.

Athlet-Daten von heute Morgen:
- Schlaf: {sleep.get('duration_h')}h | Score: {sleep.get('score')} | Tief: {sleep.get('deep_h')}h | REM: {sleep.get('rem_h')}h
- HRV: {hrv.get('hrv_last_night')} ms | Status: {hrv.get('status')} | 5-Tage-Avg: {hrv.get('hrv_5day_avg')}
- Body Battery: +{bb.get('charged')} / -{bb.get('drained')}
- Ruhepuls: {rhr.get('rhr')} bpm

Letzte Aktivitäten:
{recent_text}

{f"Athlet sagt heute Morgen:{chr(10)}{feedback_text}" if feedback_text else ""}

Antworte NUR mit einem JSON-Objekt. Kein Text davor oder danach. Kein Markdown. Nur reines JSON:

{{
  "brief": "Der vollständige Morning Brief als Fliesstext. Max 150 Wörter. Kurze Sätze. Niemals: Algorithmus, Score, Metrik. Immer: Ich sehe, Ich würde, Dein Körper. Auf Deutsch. Bezieht sich auf Schlaf, Erholung und was der Athlet selbst gesagt hat.",
  "suggestion": "Ein einziger konkreter Vorschlag für heute — max 12 Wörter. Kein Punkt am Ende. Beispiel: Ich würde heute auf 4x1 km kürzen — gleiche Qualität, weniger Last"
}}"""

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
        # Fallback falls JSON nicht sauber
        return {
            "brief": raw,
            "suggestion": "Heute ruhig bleiben — Erholung ist Training"
        }

if __name__ == "__main__":
    print("=== CAIRN Morning Brief ===\n")
    result = generate_morning_brief()
    print("Brief:", result.get("brief"))
    print("\nSuggestion:", result.get("suggestion"))