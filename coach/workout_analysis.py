"""
CAIRN Workout Analysis
Generates workout analysis using:
- Knowledge base (markdown docs from cairn-coach-standard)
- Activity data + splits from DB
- Anthropic Claude
"""

import os
import json
import anthropic
from knowledge.loader import load_workout_analysis_knowledge


def generate_workout_analysis(training_id: int) -> dict:
    """
    Generates a full workout analysis for a given training_id.
    Returns dict matching the existing analysis response structure.
    """
    import psycopg2
    database_url = os.getenv("RAILWAY_DATABASE_URL") or os.getenv("DATABASE_URL")
    conn = psycopg2.connect(database_url)
    cur = conn.cursor()

    # ── Activity ──
    cur.execute("""
        SELECT id, date, type, notes, duration_minutes, distance_km, heart_rate_avg
        FROM trainings WHERE id = %s
    """, (training_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return None

    # ── Splits ──
    cur.execute("""
        SELECT split_number, distance_km, pace_seconds, heart_rate_avg, elevation_gain
        FROM splits WHERE training_id = %s ORDER BY split_number
    """, (training_id,))
    splits = cur.fetchall()

    # ── Recent trainings for context ──
    cur.execute("""
        SELECT date, type, notes, duration_minutes, distance_km, heart_rate_avg
        FROM trainings
        WHERE date < %s
        ORDER BY date DESC
        LIMIT 5
    """, (row[1],))
    recent = cur.fetchall()

    conn.close()

    # ── Format data ──
    name = row[3] or row[2]
    distance = float(row[5]) if row[5] else 0
    duration = row[4] or 0
    avg_hr = row[6] or 0
    date_str = str(row[1])
    activity_type = row[2]

    splits_text = ""
    for s in splits[:20]:
        pace_str = f"{s[2]//60}:{str(s[2]%60).zfill(2)}/km" if s[2] else "—"
        elev = f"+{s[4]:.0f}m" if s[4] and s[4] > 0 else (f"{s[4]:.0f}m" if s[4] and s[4] < 0 else "—")
        splits_text += f"  Km {s[0]}: {pace_str} | HF {s[3] or '—'} | Elevation {elev}\n"

    recent_text = ""
    for r in recent:
        parts = [str(r[0]), r[1]]
        if r[4]: parts.append(f"{float(r[4]):.1f} km")
        if r[3]: parts.append(f"{r[3]} min")
        if r[5]: parts.append(f"HF {r[5]} bpm")
        recent_text += "  " + " | ".join(str(p) for p in parts) + "\n"

    # ── Load knowledge ──
    knowledge = load_workout_analysis_knowledge()

    # ── Build prompt ──
    system_prompt = f"""You are CAIRN — a professional endurance coach.

Your entire coaching philosophy, communication style and decision framework is defined in the knowledge documents below.

Read them carefully. Every response must comply with them.

{knowledge}

---

LANGUAGE RULE:
Always respond in German (Deutsch). The athlete is German-speaking.
Write naturally. Like a coach standing next to the athlete after training.

OUTPUT RULE:
Return ONLY a JSON object. No markdown fences. No preamble.
Structure:
{{
  "summary": "2-3 sentences — coach opening + what happened",
  "observations": ["observation 1", "observation 2", "observation 3"],
  "meaning": "1-2 sentences — what this means for upcoming training",
  "recommendation": "one clear sentence — what the coach recommends next",
  "next_session": "concrete description of the next recommended session",
  "closing": "one grounded closing thought",
  "tags": [{{"label": "short label max 20 chars", "type": "good or warn"}}]
}}

The summary should open like a real coach would — human, direct, sometimes with a reaction.
Not like a report. Not like a fitness app."""

    user_prompt = f"""Analyse this workout for Alexander:

## Activity
Name: {name}
Date: {date_str}
Type: {activity_type}
Distance: {distance:.1f} km
Duration: {duration} min
Average HR: {avg_hr} bpm

## Splits
{splits_text if splits_text else "No split data available."}

## Recent Training Context
{recent_text if recent_text else "No recent training data."}

Write the full workout analysis."""

    # ── Call Anthropic ──
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        messages=[{"role": "user", "content": user_prompt}],
        system=system_prompt,
    )

    raw = message.content[0].text.strip()
    raw = raw.replace('```json', '').replace('```', '').strip()

    try:
        result = json.loads(raw)
    except Exception:
        result = {
            "summary": raw,
            "observations": [],
            "meaning": "",
            "recommendation": "",
            "next_session": "",
            "closing": "",
            "tags": [],
        }

    return result