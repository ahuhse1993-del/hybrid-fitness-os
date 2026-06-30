"""
CAIRN Workout Chat
Lets the athlete have a conversation with the coach about a specific workout.
Uses the same knowledge base as workout analysis, plus conversation history.
"""

import os
import json
import anthropic
from knowledge.loader import load_workout_analysis_knowledge


def generate_chat_reply(training_id: int, message: str, history: list) -> str:
    import psycopg2
    database_url = os.getenv("RAILWAY_DATABASE_URL") or os.getenv("DATABASE_URL")
    conn = psycopg2.connect(database_url)
    cur = conn.cursor()

    cur.execute("""
        SELECT id, date, type, notes, duration_minutes, distance_km, heart_rate_avg
        FROM trainings WHERE id = %s
    """, (training_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return "Ich finde dieses Training nicht. Kannst du es nochmal versuchen?"

    cur.execute("""
        SELECT split_number, distance_km, pace_seconds, heart_rate_avg, elevation_gain, cadence_avg
        FROM splits WHERE training_id = %s ORDER BY split_number
    """, (training_id,))
    splits = cur.fetchall()
    conn.close()

    name = row[3] or row[2]
    distance = float(row[5]) if row[5] else 0
    duration = row[4] or 0
    avg_hr = row[6] or 0
    date_str = str(row[1])
    activity_type = row[2]

    splits_text = ""
    for s in splits:
        pace_str = f"{s[2]//60}:{str(s[2]%60).zfill(2)}/km" if s[2] else "—"
        elev = f"+{s[4]:.0f}m" if s[4] and s[4] > 0 else (f"{s[4]:.0f}m" if s[4] and s[4] < 0 else "flat")
        cad = f"{s[5]} spm" if s[5] else "—"
        splits_text += f"  Km {s[0]}: {pace_str} | HF {s[3] or '—'} | {elev} | Kadenz {cad}\n"

    knowledge = load_workout_analysis_knowledge()

    system_prompt = f"""You are CAIRN — a professional endurance coach.

Your entire coaching philosophy, communication style and decision framework is defined in the knowledge documents below.

{knowledge}

---

CONTEXT — the workout being discussed:
Name: {name}
Date: {date_str}
Type: {activity_type}
Distance: {distance:.1f} km
Duration: {duration} min
Average HR: {avg_hr} bpm

Splits (pace | heart rate | elevation | cadence per km):
{splits_text if splits_text else "No split data available."}

CONVERSATION RULE:
This is an ongoing chat about this specific workout. The athlete may ask follow-up questions,
challenge your analysis, or want clarification. Respond conversationally — not like a report.
Keep replies focused and not too long, like a real coach texting back.

LANGUAGE RULE:
Always respond in German (Deutsch).

OUTPUT RULE:
Respond with plain text only. No JSON, no markdown formatting, no preamble.
Just the coach's reply."""

    messages = []
    for h in history[-10:]:
        role = h.get("role", "user")
        content = h.get("content", "")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": message})

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=600,
        messages=messages,
        system=system_prompt,
    )

    return response.content[0].text.strip()