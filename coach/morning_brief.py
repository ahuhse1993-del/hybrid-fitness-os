import anthropic
import os
import sys
from dotenv import load_dotenv

load_dotenv()

sys.path.append("/Users/lexshapes/hybrid-fitness-os")
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

    # Athlet-Feedback aufbereiten
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

Schreibe jetzt den Morning Brief. Struktur:
1. Kurze Begrüssung (1 Satz)
2. Was ich sehe (2-3 Sätze — beziehe dich auf Schlaf, Erholung UND was der Athlet selbst sagt)
3. Meine Empfehlung für heute (konkret, 2-3 Sätze)
4. Warum (1-2 Sätze)
5. Schlussgedanke (1 prägnanter Satz)

Regeln:
- Kurze Sätze
- Nie: Algorithmus, Score, Metrik, System, Readiness
- Immer: "Ich sehe...", "Ich würde...", "Dein Körper..."
- Wenn der Athlet etwas in eigenen Worten geschrieben hat — darauf eingehen
- Auf Deutsch
- Max 150 Wörter"""

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}]
    )

    return message.content[0].text

if __name__ == "__main__":
    print("=== CAIRN Morning Brief ===\n")
    brief = generate_morning_brief()
    print(brief)